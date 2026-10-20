#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sn-submission-monitor — 期刊投稿状态监控通知工具

监控两类投稿系统下的稿件状态，一有变化立刻通知（Windows 弹窗 / 邮件）：

1. Springer Nature 投稿平台 (submission.springernature.com)
   覆盖 Scientific Reports、Communications Earth & Environment、
   Communications Physics/Biology/Medicine、npj 系列、Earth Science Informatics 等。
   使用 Playwright 无头浏览器登录抓取，支持监控账号下全部投稿。

2. Aries MTS / ScholarOne (mts-*.nature.com 等)
   覆盖 Nature Communications 等 Nature 系列期刊。
   纯 requests 实现，无需浏览器。

用法：
    python monitor.py --once              # 检查一次（配合计划任务/cron）
    python monitor.py --loop 15           # 常驻运行，每 15 分钟检查一次
    python monitor.py --show              # 查看当前缓存的状态
    python monitor.py --test-email        # 发一封测试邮件
    python monitor.py --reset             # 清空基线，下次运行重新建立

配置见 config.example.json（复制为 config.json 后填写）。
"""
import argparse
import base64
import json
import re
import smtplib
import ssl
import subprocess
import sys
import time
import traceback
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

DIR = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 60

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------- 基础设施

def log(msg, path):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.with_suffix(".log.1").write_text(path.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def norm(s):
    return re.sub(r"\s+", " ", s or "").replace("\xa0", " ").strip()


class App:
    def __init__(self, args):
        self.args = args
        self.cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        # 环境变量可覆盖敏感字段，避免明文写在配置里
        import os
        if os.environ.get("SNM_SN_PASSWORD"):
            self.cfg.setdefault("sn", {})["password"] = os.environ["SNM_SN_PASSWORD"]
        if os.environ.get("SNM_MTS_PASSWORD"):
            self.cfg.setdefault("mts", {})["password"] = os.environ["SNM_MTS_PASSWORD"]
        if os.environ.get("SNM_SMTP_AUTH_CODE"):
            self.cfg.setdefault("notify", {}).setdefault("email", {})["smtp_auth_code"] = \
                os.environ["SNM_SMTP_AUTH_CODE"]
        self.state_file = Path(args.config).parent / "state.json"
        self.storage_file = Path(args.config).parent / "sn_storage.json"
        self.log_file = Path(args.config).parent / "monitor.log"

    # ---------------- state ----------------
    def load_state(self):
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"sn": {}, "mts": {}, "_meta": {"consecutive_failures": 0, "alert_sent": False}}

    def save_state(self, st):
        self.state_file.write_text(json.dumps(st, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    # ---------------- 通知 ----------------
    def toast(self, title, body):
        if not self.args.toast:
            return
        if sys.platform != "win32":
            return
        def q(s):
            return "'" + str(s).replace("'", "''") + "'"
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null; "
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$t = $xml.GetElementsByTagName('text'); "
            "$t.Item(0).AppendChild($xml.CreateTextNode(%s)) | Out-Null; "
            "$t.Item(1).AppendChild($xml.CreateTextNode(%s)) | Out-Null; "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
            ").Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
        ) % (q(title), q(body))
        enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError("toast failed: %s" % r.stderr.decode("gbk", "replace")[:150])

    def email(self, subject, body):
        ec = self.cfg.get("notify", {}).get("email") or {}
        if not ec.get("smtp_auth_code"):
            log("邮件未配置，跳过: %s" % subject, self.log_file)
            return False
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("投稿状态监控", ec["smtp_user"]))
        msg["To"] = ec["to"]
        with smtplib.SMTP_SSL(ec["smtp_host"], int(ec.get("smtp_port", 465)),
                              context=ssl.create_default_context(), timeout=60) as s:
            s.login(ec["smtp_user"], ec["smtp_auth_code"])
            s.sendmail(ec["smtp_user"], [ec["to"]], msg.as_string())
        log("邮件已发送: %s" % subject, self.log_file)
        return True

    def notify_all(self, title, subject, body):
        """弹窗+邮件，各通道独立容错。"""
        try:
            self.toast(title, body.splitlines()[0][:120])
        except Exception as e:
            log("弹窗失败: %s" % e, self.log_file)
        try:
            self.email(subject, body)
        except Exception as e:
            log("邮件失败: %s" % e, self.log_file)


# ---------------------------------------------------------------- SN 平台（Playwright）

SN_BASE = "https://submission.springernature.com"


def sn_run(app, check_only_uuids=None):
    """返回 {uuid: card dict}。check_only_uuids 为 None 时监控全部。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("SN 平台监控需要 Playwright：pip install playwright && "
                           "python -m playwright install chromium")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA, locale="en-US",
            storage_state=str(app.storage_file) if app.storage_file.exists() else None)
        page = ctx.new_page()
        page.goto(SN_BASE + "/your-submissions", wait_until="domcontentloaded",
                  timeout=90000)
        page.wait_for_timeout(4000)

        if "idp" in page.url or "gateway" in page.url:
            log("SN 登录会话失效，重新登录 ...", app.log_file)
            sn_login(page, app)
            page.wait_for_url("**" + SN_BASE.replace("https://", "") + "/**", timeout=90000)
            app.storage_file.write_text(json.dumps(ctx.storage_state()), encoding="utf-8")
            page.wait_for_timeout(5000)

        for label in ("Accept all cookies", "Reject optional cookies"):
            try:
                page.click("button:text('%s')" % label, timeout=3000)
                page.wait_for_timeout(500)
                break
            except Exception:
                pass

        if "your-submissions" not in page.url:
            raise RuntimeError("登录后未进入投稿列表页: %s" % page.url[:120])

        cards = page.evaluate("""
        () => [...document.querySelectorAll('li.submission-item')].map(li => {
          const q = s => { const e = li.querySelector(s); return e ? e.textContent.trim() : null; };
          const link = li.querySelector('a[data-test="submission-details-link"]');
          const full = (li.innerText || '').replace(/\\u00a0/g, ' ');
          const idx = full.indexOf('View submission details');
          const status = idx >= 0 ? full.slice(idx + 'View submission details'.length).trim() : full;
          return {
            uuid: link ? link.getAttribute('href').split('/').pop() : null,
            title: q('[data-test="submission-title"]'),
            journal: q('[data-test="journal-title"]'),
            author: q('[data-test="corresponding-author"]'),
            version: q('[data-test="submission-version"]'),
            status: status,
            url: link ? link.href : null
          };
        }).filter(c => c.uuid)
        """)
        if not cards:
            raise RuntimeError("投稿列表页解析到 0 条记录（页面结构可能变化）")

        result = {}
        for c in cards:
            if check_only_uuids and c["uuid"] not in check_only_uuids:
                continue
            c["signature"] = norm(c["status"]) + "||" + norm(c["version"])
            result[c["uuid"]] = c

        if check_only_uuids:
            missing = set(check_only_uuids) - set(result)
            if missing:
                raise RuntimeError("以下投稿在列表中未找到: %s" % ", ".join(missing))
        browser.close()
        return result


def sn_login(page, app):
    sn_cfg = app.cfg["sn"]
    for label in ("Accept all cookies", "Reject optional cookies"):
        try:
            page.click("button:text('%s')" % label, timeout=3000)
            page.wait_for_timeout(500)
            break
        except Exception:
            pass
    page.wait_for_selector("#login-email", timeout=40000)
    page.fill("#login-email", sn_cfg["email"])
    page.click("#email-submit")
    page.wait_for_selector('input[type="password"]', timeout=40000)
    page.fill('input[type="password"]', sn_cfg["password"])
    page.wait_for_timeout(500)
    for el in page.query_selector_all("button"):
        if (el.inner_text() or "").strip().lower() in ("log in", "login", "sign in"):
            el.click()
            break
    else:
        page.keyboard.press("Enter")


def sn_detail(page, url):
    """抓单篇详情页，取 CURRENT STATUS 全文与进度（用于变更通知的正文）。"""
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(4000)
    text = page.inner_text("body")
    if "CURRENT STATUS" not in text:
        return None
    def block(start, end):
        m = re.search(start + r"(.*?)" + end, text, re.S)
        return re.sub(r"[ \t]+", " ", m.group(1)).strip() if m else None
    status_block = block(r"CURRENT STATUS", r"Progress so far")
    progress = block(r"Progress so far", r"Learn about our submission process")
    progress = re.sub(r"\bShow history\b", "", progress or "").strip()
    return {"status_block": status_block, "progress": progress}


# ---------------------------------------------------------------- MTS 平台（requests）

def mts_run(app, check_only_ms=None):
    """返回 {ms_id: {status,...}}。check_only_ms 为 None 时监控账号下全部稿件。"""
    import requests

    cfg = app.cfg["mts"]
    base = cfg["base"].rstrip("/")
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    r = sess.get(base + "/cgi-bin/main.plex", timeout=TIMEOUT)
    r.raise_for_status()
    hidden = {}
    for m in re.finditer(r"<input[^>]*>", r.text, re.I):
        tag = m.group(0)
        n = re.search(r"name=['\"]([^'\"]+)['\"]", tag)
        v = re.search(r"value=['\"]([^'\"]*)['\"]", tag)
        if n and v:
            hidden.setdefault(n.group(1), v.group(1))
    payload = {"form_type": "login_results",
               "j_id": hidden.get("j_id", ""),
               "ms_id_key": hidden.get("ms_id_key", ""),
               "login": cfg["email"], "password": cfg["password"],
               "submit_login": "Login"}
    r2 = sess.post(base + "/cgi-bin/main.plex", data=payload, timeout=TIMEOUT)
    r2.raise_for_status()
    if "form_type=logout" not in r2.text and "Log Out" not in r2.text:
        raise RuntimeError("MTS 登录失败（账号密码错误或页面改版）")

    def abs_url(href):
        from urllib.parse import urljoin
        return urljoin(base + "/", href)

    # 遍历所有稿件文件夹，收集全部 view_ms 链接
    manuscripts = {}
    folders, seen = [], set()
    for href in re.findall(r'href=["\']([^"\']*form_type=dt_folder[^"\']*)["\']',
                           r2.text, re.I):
        url = abs_url(href)
        key = re.sub(r"ms_id_key=[^&]*", "", url)
        if key not in seen:
            seen.add(key)
            folders.append(url)
    for url in folders:
        rf = sess.get(url, timeout=TIMEOUT)
        rf.raise_for_status()
        for m in re.finditer(r'href=["\']([^"\']*form_type=view_ms[^"\']*)["\']', rf.text, re.I):
            link = abs_url(m.group(1))
            mid = re.search(r"ms_id=(\d+)", link)
            if mid:
                manuscripts[mid.group(1)] = link
    if not manuscripts:
        raise RuntimeError("MTS 未找到任何稿件")

    if check_only_ms:
        if str(check_only_ms) not in manuscripts:
            raise RuntimeError("MTS 稿件 ms_id=%s 未找到" % check_only_ms)
        manuscripts = {str(check_only_ms): manuscripts[str(check_only_ms)]}

    import html as html_mod
    def parse_field(html, label):
        m = re.search(label + r"\s*(?:</a>)?\s*</TH>\s*<TD[^>]*>(.*?)</TD>", html, re.I | re.S)
        if not m:
            return None
        txt = re.sub(r"<[^>]+>", " ", m.group(1))
        txt = html_mod.unescape(txt).replace("\xa0", " ")
        return re.sub(r"\s+", " ", txt).strip() or None

    result = {}
    for mid, link in manuscripts.items():
        rv = sess.get(link, timeout=TIMEOUT)
        rv.raise_for_status()
        status = parse_field(rv.text, r"Current\s+Stage")
        if not status:
            raise RuntimeError("MTS 稿件 ms_id=%s 解析不到 Current Stage" % mid)
        result[mid] = {
            "status": status,
            "ms_number": parse_field(rv.text, r"Manuscript\s*#"),
            "title": parse_field(rv.text, r">\s*Title"),
            "journal": base,
            "url": base + "/cgi-bin/main.plex",
            "signature": norm(status),
        }
    return result


# ---------------------------------------------------------------- 主逻辑

def check_once(app):
    st = app.load_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st["_meta"]["last_check"] = now

    try:
        sn_cards, mts_items = {}, {}
        if app.cfg.get("platform") in ("sn", "both"):
            watch = app.cfg.get("sn", {}).get("watch", "all")
            only = None
            if isinstance(watch, list):
                only = {u.rstrip("/").split("/")[-1] for u in watch}
            sn_cards = sn_run(app, only)
        if app.cfg.get("platform") in ("mts", "both"):
            only_ms = app.cfg.get("mts", {}).get("ms_id") if app.cfg.get(
                "mts", {}).get("watch") != "all" else None
            mts_items = mts_run(app, only_ms)
    except Exception as e:
        st["_meta"]["consecutive_failures"] = st["_meta"].get("consecutive_failures", 0) + 1
        log("检查失败(%d): %s" % (st["_meta"]["consecutive_failures"], e), app.log_file)
        thr = int(app.cfg.get("fail_alert_after", 6))
        if st["_meta"]["consecutive_failures"] >= thr and not st["_meta"].get("alert_sent"):
            try:
                app.notify_all(
                    "⚠️ 投稿监控异常",
                    "【投稿状态监控异常】连续 %d 次检查失败" % st["_meta"]["consecutive_failures"],
                    "监控连续 %d 次失败，请人工检查。\n原因: %s\n日志: %s"
                    % (st["_meta"]["consecutive_failures"], e, app.log_file))
                st["_meta"]["alert_sent"] = True
            except Exception:
                log("告警通知失败: " + traceback.format_exc(limit=2), app.log_file)
        app.save_state(st)
        return 1

    st["_meta"]["consecutive_failures"] = 0
    st["_meta"]["alert_sent"] = False
    st["_meta"]["last_ok_check"] = now

    changes = []   # [(平台, key, item, old_state)]
    fresh = False

    for key, card in sn_cards.items():
        old = st["sn"].get(key)
        if old is None:
            fresh = True
            st["sn"][key] = {"signature": card["signature"], "journal": card["journal"],
                             "title": card["title"], "version": card["version"],
                             "status": first_status_line(card["status"])}
            log("SN 基线 [%s] %s: %s" % (card["journal"], key[:8],
                                         first_status_line(card["status"])), app.log_file)
        elif old["signature"] != card["signature"]:
            changes.append(("sn", key, card, old))
        else:
            for f in ("journal", "title", "version"):
                if card.get(f):
                    st["sn"][key][f] = card[f]
            st["sn"][key]["status"] = first_status_line(card["status"])

    for key, item in mts_items.items():
        old = st["mts"].get(key)
        if old is None:
            fresh = True
            st["mts"][key] = {k: item.get(k) for k in
                              ("signature", "journal", "title", "status", "ms_number")}
            log("MTS 基线 [%s] %s: %s" % (item.get("ms_number", key), key, item["status"]),
                app.log_file)
        elif old["signature"] != item["signature"]:
            changes.append(("mts", key, item, old))
        else:
            st["mts"][key]["status"] = item["status"]

    if fresh and not changes:
        app.save_state(st)
        if not st["sn"] and not st["mts"]:
            log("无变化（空结果）", app.log_file)
        return 0

    if not changes:
        log("无变化", app.log_file)
        app.save_state(st)
        return 0

    # 有变化 -> 组织通知（合并成一封邮件/一次弹窗）
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA, locale="en-US",
            storage_state=str(app.storage_file) if app.storage_file.exists() else None)
        page = ctx.new_page()
        parts, first = [], None
        for platform, key, item, old in changes:
            detail = None
            if platform == "sn":
                try:
                    detail = sn_detail(page, item["url"])
                except Exception as e:
                    log("详情页抓取失败(%s): %s" % (key[:8], e), app.log_file)
            old_status = old.get("status") or "-"
            new_status = (first_status_line(item["status"]) if platform == "sn"
                          else item["status"])
            if first is None:
                first = (item.get("journal"), new_status)
            parts.append(
                "◆ %s\n  标题: %s\n  版本: %s\n  旧状态: %s\n  新状态: %s%s\n  链接: %s"
                % (item.get("journal") or "-", item.get("title") or "-",
                   item.get("version") or "-", old_status, new_status,
                   ("\n  详情:\n" + indent(detail["status_block"] + "\n" + (detail["progress"] or "")))
                   if detail else "", item.get("url") or "-"))
            # 更新 state
            bucket = st[platform]
            bucket[key].update({
                "signature": item["signature"],
                "status": new_status,
                **{f: item.get(f) for f in ("journal", "title", "version", "ms_number")
                   if item.get(f)}})
        browser.close()

    journal0, status0 = first
    subject = "【投稿状态更新】%s: %s" % (journal0 or "", status0)
    if len(changes) > 1:
        subject += "（共 %d 篇有变化）" % len(changes)
    body = ("检测到 %d 篇投稿状态变化\n\n%s\n\n检测时间: %s\n"
            "—— 本邮件由 sn-submission-monitor 自动发送" % (len(changes), "\n\n".join(parts), now))
    app.notify_all("🎉 投稿状态更新", subject, body)
    log("状态变更 x%d: %s" % (len(changes), subject), app.log_file)
    app.save_state(st)
    return 0


def first_status_line(status_text):
    """SN 卡片状态文本取首行（跳过空行）。"""
    for ln in (status_text or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln
    return None


def indent(text):
    return "\n".join("    " + ln for ln in (text or "").strip().splitlines())


def show_state(app):
    st = app.load_state()
    meta = st.get("_meta", {})
    print("上次检查: %s | 上次成功: %s | 连续失败: %s"
          % (meta.get("last_check"), meta.get("last_ok_check"),
             meta.get("consecutive_failures")))
    for platform in ("sn", "mts"):
        for key, it in st.get(platform, {}).items():
            print("[%s] %s\n    %s | %s | %s" % (
                platform, it.get("journal") or "-", it.get("ms_number") or key,
                it.get("version") or "-", it.get("status") or "-"))
    if not st.get("sn") and not st.get("mts"):
        print("（还没有基线，先运行一次检查）")


def main():
    ap = argparse.ArgumentParser(description="期刊投稿状态监控通知工具")
    ap.add_argument("--config", default=str(DIR / "config.json"), help="配置文件路径")
    ap.add_argument("--loop", type=int, metavar="MINUTES", help="常驻运行，每 N 分钟检查一次")
    ap.add_argument("--once", action="store_true", help="检查一次（默认行为）")
    ap.add_argument("--show", action="store_true", help="显示当前缓存状态")
    ap.add_argument("--test-email", action="store_true", help="发送测试邮件")
    ap.add_argument("--reset", action="store_true", help="清空基线")
    ap.add_argument("--no-toast", dest="toast", action="store_false", help="本次不弹窗")
    args = ap.parse_args()

    app = App(args)
    if args.reset:
        if app.state_file.exists():
            app.state_file.unlink()
        print("基线已清空，下次运行将重新建立（不发送通知）")
        return 0
    if args.show:
        show_state(app)
        return 0
    if args.test_email:
        app.email("【投稿状态监控】测试邮件",
                  "这是 sn-submission-monitor 的测试邮件，收到即邮件通道正常。")
        print("测试邮件已发送")
        return 0

    if args.loop:
        while True:
            try:
                check_once(app)
            except Exception:
                log("未捕获异常: " + traceback.format_exc(limit=3), app.log_file)
            time.sleep(max(1, args.loop) * 60)
    check_once(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
