"""Check orchestration, durable notification queue and backwards-compatible state."""
import copy
import hashlib
import html
import json
import os
import re
import sys
import uuid
from datetime import datetime
from logging import Formatter, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .config import data_directory, enabled_platforms, load_config
from .errors import StateError
from .notify import send_email, send_toast
from .storage import atomic_json, new_state, read_state, run_lock


EXIT_CHECK = 1
EXIT_CONFIG = 2
EXIT_DELIVERY = 3
EXIT_BUSY = 4


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(value):
    text = str(value or "")
    for _ in range(2):
        text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def safe_url(value):
    try:
        url = urlsplit(value or "")
        if url.scheme != "https" or not url.hostname:
            return ""
        host = url.hostname.lower()
        # Manuscript links may contain one-time login/auth keys. Never email those.
        if host in ("editorialmanager.com", "www.editorialmanager.com") or host.endswith(".editorialmanager.com"):
            segments = [part for part in url.path.split("/") if part]
            return "https://" + host + ("/" + segments[0] + "/Default.aspx" if segments else "/")
        if host.startswith("mts-") and host.endswith(".nature.com"):
            return "https://" + host + "/cgi-bin/main.plex"
        if host == "submission.springernature.com":
            return urlunsplit(("https", host, url.path, "", ""))
    except (ValueError, TypeError):
        pass
    return ""


class App:
    def __init__(self, args):
        self.args = args
        self.config_file = Path(args.config).expanduser().resolve()
        self.cfg = load_config(self.config_file)
        self.data_dir = data_directory(self.cfg, self.config_file)
        self.state_file = self.data_dir / "state.json"
        self.storage_file = self.data_dir / "sn_storage.json"
        self.em_storage_file = self.data_dir / "em_storage.json"
        self.log_file = self.data_dir / "monitor.log"
        self.lock_file = self.data_dir / "monitor.lock"
        self._logger = None

    def redact(self, value):
        text = str(value)
        secrets = []
        for platform in ("sn", "mts", "em"):
            settings = self.cfg.get(platform) or {}
            if isinstance(settings, dict):
                secrets.extend(settings.get(key) for key in ("password", "email"))
        settings = self.cfg.get("notify", {}).get("email") or {}
        secrets.extend(settings.get(key) for key in ("smtp_auth_code", "smtp_user"))
        recipients = settings.get("to", [])
        if isinstance(recipients, str):
            secrets.append(recipients)
        elif isinstance(recipients, list):
            secrets.extend(recipients)
        for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        # Browser exceptions often include a complete OAuth callback URL.
        def clean_url(match):
            raw = match.group(0)
            try:
                url = urlsplit(raw)
                host = url.hostname or "[host]"
                if any(word in host for word in ("id.", "idp", "auth")):
                    return url.scheme + "://" + host + "/[auth-redacted]"
                return urlunsplit((url.scheme, host, url.path, "", ""))
            except ValueError:
                return "[url-redacted]"
        text = re.sub(r"https?://[^\s<>\"']+", clean_url, text)
        text = re.sub(r"(?i)\b(password|authorization|cookie|token|auth_key|ms_id_key)\b\s*[:=]\s*[^\s,;]+",
                      r"\1=[redacted]", text)
        return text[:1600]

    def log(self, message):
        clean = self.redact(message).replace("\r", "\\r").replace("\n", "\\n")
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", clean)
        if self._logger is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            logger = getLogger("submission_monitor." + uuid.uuid4().hex)
            logger.setLevel("INFO")
            logger.propagate = False
            handler = RotatingFileHandler(self.log_file, maxBytes=2 * 1024 * 1024,
                                          backupCount=2, encoding="utf-8")
            handler.setFormatter(Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            logger.addHandler(handler)
            self._logger = logger
        self._logger.info(clean)
        if sys.stdout is not None:
            try:
                print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), clean))
            except (OSError, UnicodeError):
                pass

    def close(self):
        if self._logger is not None:
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)
            self._logger = None

    def load_state(self):
        return read_state(self.state_file)

    def save_state(self, state):
        atomic_json(self.state_file, state)

    def save_session(self, context, path):
        atomic_json(path, context.storage_state())

    def email(self, subject, body, event_id=None):
        settings = self.cfg["notify"]["email"]
        if not settings.get("enabled"):
            return False
        send_email(settings, subject, body, event_id)
        self.log("SMTP 已接受邮件：%s" % subject)
        return True

    def toast(self, title, body):
        if not self.cfg["notify"]["toast"] or not getattr(self.args, "toast", True) or sys.platform != "win32":
            return False
        return send_toast(title, body)

    def active_channels(self):
        channels = []
        if self.cfg["notify"]["email"].get("enabled"):
            channels.append("email")
        if self.cfg["notify"]["toast"] and sys.platform == "win32":
            channels.append("toast")
        return channels

    def recipient_fingerprint(self):
        settings = self.cfg["notify"]["email"]
        recipients = settings.get("to")
        if isinstance(recipients, str):
            recipients = [recipients]
        value = [settings.get("smtp_user"), recipients]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def scope(self, platform):
        config = self.cfg[platform]
        identity = {key: config.get(key) for key in ("base", "email")}
        return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def watch_scope(self, platform):
        config = self.cfg[platform]
        value = [config.get("watch", "all"), config.get("ms_id")]
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def queue(self, state, platform, kind, subject, body, title="投稿状态更新", items=None):
        channels = self.active_channels()
        event = {"id": uuid.uuid4().hex, "platform": platform, "kind": kind,
                 "scope": self.scope(platform), "watch_scope": self.watch_scope(platform),
                 "items": items or {}, "created_at": now(), "title": title,
                 "subject": subject, "body": body,
                 "channels": {channel: {"done": False, "attempts": 0} for channel in channels}}
        if "email" in channels:
            event["recipient_fingerprint"] = self.recipient_fingerprint()
        state["_outbox"].append(event)
        self.log("通知已入队（%s，渠道：%s）" % (kind, ", ".join(channels) or "未配置，仅记日志"))

    def deliver(self, state):
        """Persist every channel acknowledgement; a failed email is retried next run."""
        enabled = set(enabled_platforms(self.cfg))
        active = set(self.active_channels())
        failed = False
        for event in state["_outbox"]:
            platform = event.get("platform")
            stale = platform not in enabled or event.get("scope") != self.scope(platform)
            if not stale and event.get("watch_scope") != self.watch_scope(platform):
                try:
                    retained = self.restrict_results(platform, event.get("items", {}), {})
                    stale = not retained or set(retained) != set(event.get("items", {}))
                except (RuntimeError, ValueError):
                    stale = True
            for channel, delivery in event["channels"].items():
                if delivery["done"]:
                    continue
                # Do not send old private events to a newly configured recipient/account.
                recipient_changed = channel == "email" and event.get("recipient_fingerprint") != self.recipient_fingerprint()
                if stale or recipient_changed:
                    delivery.update(done=True, skipped_at=now(), reason="scope_or_recipient_changed")
                    self.log("未发送的旧通知已取消：目标账号、监控范围或收件人已变更")
                    self.save_state(state)
                    continue
                if channel not in active:
                    # A temporarily disabled channel must not lose its queued alerts.
                    continue
                if channel == "toast" and not getattr(self.args, "toast", True):
                    continue
                try:
                    delivery["attempts"] += 1
                    if channel == "email":
                        self.email(event["subject"], event["body"], event["id"])
                    elif channel == "toast":
                        self.toast(event["title"], event["body"].splitlines()[0])
                    else:
                        raise StateError("未知通知渠道，队列已保留")
                    delivery.update(done=True, delivered_at=now())
                    delivery.pop("last_error", None)
                    self.save_state(state)
                except StateError:
                    raise
                except Exception as error:
                    delivery["last_error"] = self.redact(error)
                    delivery["last_attempt"] = now()
                    self.log("%s 通知发送失败，将于下次重试：%s" % (channel, self.redact(error)))
                    self.save_state(state)
                    failed = True
        state["_outbox"] = [event for event in state["_outbox"]
                            if any(not channel["done"] for channel in event["channels"].values())]
        self.save_state(state)
        if state["_outbox"]:
            self.log("仍有 %d 条通知待投递（失败或渠道暂停），已保留至下次检查" % len(state["_outbox"]))
        return failed or bool(state["_outbox"])

    def check(self, collector=None):
        if collector is None:
            from .providers import collect
            collector = collect
        with run_lock(self.lock_file):
            state = self.load_state()
            timestamp = now()
            meta = state["_meta"]
            meta["last_check"] = timestamp
            failures = 0
            for platform in enabled_platforms(self.cfg):
                identity = self.scope(platform)
                old_scope = state["_scope"].get(platform)
                scope_changed = old_scope is not None and old_scope != identity
                if scope_changed:
                    state[platform] = {}
                    meta["providers"][platform] = {}
                    self.log("%s 监控范围已改变，首次成功检查将建立新基线" % platform.upper())
                # A legacy state has no scope hash; keep its baseline and avoid a false reset.
                state["_scope"][platform] = identity
                stats = meta["providers"].setdefault(platform, {})
                stats["last_check"] = timestamp
                initialized = stats.get("baseline_initialized", bool(state[platform]))
                if self.cfg[platform].get("watch", "all") != "all" and state[platform]:
                    try:
                        state[platform] = self.restrict_results(platform, state[platform], state[platform])
                    except RuntimeError:
                        state[platform] = {}
                # Bound selector state needs to be visible to the provider without a second writer.
                self._checking_state = state
                try:
                    results = collector(self, platform)
                    if not isinstance(results, dict):
                        raise RuntimeError("平台未返回有效稿件列表")
                    for key, item in results.items():
                        if not isinstance(item, dict) or not norm(item.get("status")):
                            raise RuntimeError("稿件状态解析为空，未更新缓存")
                    results = self.restrict_results(platform, results, state[platform])
                    recovered = stats.get("alerted", False)
                    staged = copy.deepcopy(state)
                    self.update_manuscripts(staged, platform, results, initialized)
                    state[platform] = staged[platform]
                    state["_outbox"] = staged["_outbox"]
                    stats.update(last_ok_check=now(), consecutive_failures=0,
                                 baseline_initialized=True, alerted=False)
                    stats.pop("last_error", None)
                    if recovered:
                        self.queue(state, platform, "recovered", "【投稿监控已恢复】%s" % platform.upper(),
                                   "%s 平台检查已恢复。\n检测时间：%s" % (platform.upper(), now()), "投稿监控恢复")
                except (StateError, OSError) as error:
                    # A local disk error must not masquerade as a remote check failure.
                    if isinstance(error, StateError):
                        raise
                    self.record_failure(state, platform, stats, error)
                    failures += 1
                except Exception as error:
                    self.record_failure(state, platform, stats, error)
                    failures += 1
                finally:
                    self._checking_state = None
                self.save_state(state)
            if failures:
                meta["consecutive_failures"] = meta.get("consecutive_failures", 0) + 1
            else:
                meta["consecutive_failures"] = 0
                meta["last_ok_check"] = now()
            meta["alert_sent"] = any(stats.get("alerted", False) for stats in meta["providers"].values())
            meta["version"] = __version__
            self.save_state(state)
            pending = self.deliver(state)
            return EXIT_CHECK if failures else (EXIT_DELIVERY if pending else 0)

    def restrict_results(self, platform, results, previous):
        """Enforce explicit scope at the orchestration boundary, not only in collectors."""
        settings = self.cfg[platform]
        watch = settings.get("watch", "all")
        if watch == "all":
            return results
        selected = {}
        if platform == "em":
            for target in watch:
                matches = {}
                if isinstance(target, str):
                    number = target.strip().upper()
                    for key, item in results.items():
                        current = str(item.get("number") or key).strip().upper()
                        if current == number or (not re.search(r"R\d+$", number)
                                and re.fullmatch(re.escape(number) + r"R\d+", current)):
                            matches[key] = item
                else:
                    marker = json.dumps({k: norm(v) for k, v in target.items()},
                                        ensure_ascii=False, sort_keys=True)
                    bound = {str(v.get("_watch_number")).upper() for v in previous.values()
                             if v.get("_watch_key") == marker and v.get("_watch_number")}
                    for key, item in results.items():
                        current = str(item.get("number") or "").strip().upper()
                        root = re.sub(r"R\d+$", "", current)
                        if ((bound and root in bound) or (not bound
                                and norm(item.get("title")) == norm(target["title"])
                                and norm(item.get("submitted")) == norm(target["submitted"]))):
                            matches[key] = dict(item, _watch_key=marker, _watch_number=root)
                    identities = {v.get("_watch_number") or k for k, v in matches.items()}
                    if len(identities) > 1:
                        raise RuntimeError("标题/日期筛选匹配多篇稿件，未发送通知")
                if not matches:
                    raise RuntimeError("未找到配置中指定的 EM 稿件，未更新基线")
                selected.update(matches)
        elif platform == "sn":
            for target in watch:
                key = target.rstrip("/").rsplit("/", 1)[-1]
                if key not in results:
                    raise RuntimeError("未找到配置中指定的 SN 稿件，未更新基线")
                selected[key] = results[key]
        else:
            key = str(settings["ms_id"])
            if key not in results:
                raise RuntimeError("未找到配置中指定的 MTS 稿件，未更新基线")
            selected[key] = results[key]
        return selected

    def record_failure(self, state, platform, stats, error):
        count = stats.get("consecutive_failures", 0) + 1
        detail = self.redact(error)
        stats.update(consecutive_failures=count, last_error=detail)
        self.log("%s 检查失败(%d)：%s" % (platform.upper(), count, detail))
        if count >= self.cfg["fail_alert_after"] and not stats.get("alerted"):
            self.queue(state, platform, "failure", "【投稿监控异常】%s 连续 %d 次失败" % (platform.upper(), count),
                       "%s 连续 %d 次检查失败。\n原因：%s\n最近成功：%s\n请检查网络、登录会话或页面改版。"
                       % (platform.upper(), count, detail, stats.get("last_ok_check", "尚未成功")), "投稿监控异常")
            stats["alerted"] = True

    def update_manuscripts(self, state, platform, results, initialized):
        bucket = state[platform]
        changes = []
        for key, result in results.items():
            item = copy.deepcopy(result)
            item["status"] = norm(item["status"])
            item["title"] = norm(item.get("title"))
            item["url"] = safe_url(item.get("url"))
            item["last_seen"] = now()
            item["_key"] = key
            old = bucket.get(key)
            if old is None and platform == "em":
                # Never merge two numbered, unrelated submissions that share a title.
                candidates = [(old_key, value) for old_key, value in bucket.items()
                              if old_key != key and old_key not in results
                              and norm(value.get("title")) == item["title"]
                              and (not value.get("number") or re.sub(r"R\d+$", "", str(value.get("number")))
                                   == re.sub(r"R\d+$", "", str(item.get("number"))))]
                if len(candidates) == 1:
                    old_key, old = candidates[0]
                    bucket.pop(old_key)
                    self.log("EM 同一稿件编号/修订记录已迁移")
            if old is None:
                if initialized:
                    changes.append((item, None))
                else:
                    self.log("%s 基线已建立：%s — %s" %
                             (platform.upper(), item.get("number") or item.get("ms_number") or key, item["status"]))
            elif self.changed(platform, old, item):
                changes.append((item, old))
            bucket[key] = item
        if changes:
            sections = []
            for item, old in changes:
                sections.append("期刊：%s\n稿件：%s\n标题：%s\n旧状态：%s\n新状态：%s\n状态日期：%s\n链接：%s" % (
                    item.get("journal") or platform.upper(), item.get("number") or item.get("ms_number") or "尚未分配稿号",
                    item.get("title") or "—", old.get("status", "—") if old else "新发现投稿",
                    item["status"], item.get("status_date") or "—", item["url"] or "请登录投稿系统查看"))
            first = changes[0][0]
            subject = "【投稿状态更新】%s：%s" % (first.get("journal") or platform.upper(), first["status"])
            if len(changes) > 1:
                subject += "（共%d篇）" % len(changes)
            body = "\n\n".join(sections) + "\n\n检测时间：%s\n由 sn-submission-monitor 自动检查。" % now()
            event_items = {(item.get("_key") or item.get("number") or item.get("ms_number") or str(i)): item
                           for i, (item, _) in enumerate(changes)}
            self.queue(state, platform, "change", subject, body, items=event_items)
        elif initialized:
            self.log("%s 状态未变（本次跟踪 %d 篇）" % (platform.upper(), len(results)))

    @staticmethod
    def changed(platform, old, item):
        fields = ["status"]
        if platform == "em":
            fields.append("status_date")
        elif platform == "sn":
            fields.append("version")
        return any(norm(old.get(field)) != norm(item.get(field)) for field in fields)

    def list_live(self, collector=None):
        if collector is None:
            from .providers import collect
            collector = collect
        with run_lock(self.lock_file):
            state = self.load_state()
            results = {}
            for platform in enabled_platforms(self.cfg):
                results[platform] = self.restrict_results(platform, collector(self, platform), state[platform])
            return results

    def reset(self):
        with run_lock(self.lock_file):
            state = self.load_state()
            result = new_state()
            result["_outbox"] = state["_outbox"]
            self.save_state(result)
            self.log("基线已清空；登录凭据、会话和待发通知未删除")

    def test_email(self):
        if not self.cfg["notify"]["email"].get("enabled"):
            raise RuntimeError("邮件渠道未启用，未发送测试邮件")
        self.email("【投稿监控测试】邮件通道验证", "这是一封明确标注的测试邮件，不是稿件状态更新。\n"
                   "邮件服务器接受发送不等于进入收件箱，请自行确认收件及垃圾邮件。")

    def state_for_provider(self):
        return getattr(self, "_checking_state", None) or self.load_state()
