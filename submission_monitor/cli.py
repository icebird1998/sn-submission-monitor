"""Command line entry points. Safe diagnostics never reveal account credentials."""
import argparse
import getpass
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .app import App, EXIT_BUSY, EXIT_CONFIG
from .config import PASSWORD_ENVS, data_directory, default_config, enabled_platforms, load_config
from .errors import AlreadyRunning, ConfigError, MonitorError
from .storage import run_lock

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ROOT = PACKAGE_ROOT if (PACKAGE_ROOT / "monitor.py").exists() else Path.cwd()


def emit(message):
    if sys.stdout is not None:
        print(message)


def show(app):
    state = app.load_state()
    meta = state["_meta"]
    emit("缓存状态（不是实时查询） | 上次检查：%s | 最近全部成功：%s" %
         (meta.get("last_check", "尚无"), meta.get("last_ok_check", "尚无")))
    for platform in enabled_platforms(app.cfg):
        stats = meta.get("providers", {}).get(platform, {})
        emit("%s 最近成功：%s | 连续失败：%s" % (
            platform.upper(), stats.get("last_ok_check", meta.get("last_ok_check", "尚无")),
            stats.get("consecutive_failures", meta.get("consecutive_failures", 0))))
        for key, item in state[platform].items():
            emit("[%s] %s | %s | %s" % (platform.upper(), item.get("number") or item.get("ms_number") or key,
                                       item.get("status") or "—", item.get("status_date") or item.get("version") or "—"))
    emit("待投递通知：%d" % len(state["_outbox"]))


def doctor(app):
    emit("sn-submission-monitor %s | Python %d.%d | %s" %
         (__version__, sys.version_info.major, sys.version_info.minor, sys.platform))
    emit("配置有效 | 平台：%s" % ", ".join(enabled_platforms(app.cfg)))
    emit("运行目录：%s" % app.data_dir)
    emit("邮件：%s | Windows通知配置：%s" %
         ("已启用" if app.cfg["notify"]["email"].get("enabled") else "关闭",
          "启用" if app.cfg["notify"]["toast"] else "关闭"))
    failures = []
    for platform in enabled_platforms(app.cfg):
        dependency = "requests" if platform == "mts" else "playwright"
        if importlib.util.find_spec(dependency) is None:
            failures.append("缺少依赖 %s" % dependency)
    try:
        with run_lock(app.lock_file):
            # No login or network request: inspect state integrity and filesystem permissions.
            app.load_state()
    except AlreadyRunning:
        emit("提示：监控正在运行；等它完成后再运行诊断")
    except (OSError, MonitorError) as error:
        failures.append(app.redact(error))
    if any(p in enabled_platforms(app.cfg) for p in ("sn", "em")) and not failures:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as runtime:
                present = Path(runtime.chromium.executable_path).exists()
            if not present:
                failures.append("未发现 Chromium；运行 python -m playwright install chromium")
            else:
                emit("Chromium 可执行文件已安装（未启动、未联网）")
        except Exception:
            failures.append("无法检查 Chromium；请确认 playwright 安装与当前 Python 一致")
    if sys.platform != "win32" and app.cfg["notify"]["toast"]:
        emit("提示：非 Windows 系统不发送桌面通知，请启用邮件")
    if not app.active_channels():
        emit("警告：没有可用通知渠道，仅记录本地状态和日志")
    for failure in failures:
        emit("问题：" + failure)
    emit("未执行网络登录或测试邮件。" if not failures else "诊断发现问题，监控尚未验证可运行。")
    return EXIT_CONFIG if failures else 0


def _prompt(message, default=""):
    result = input(message + (" [%s]" % default if default else "") + ": ").strip()
    return result or default


def init_config(path):
    """An explicit interactive setup never overwrites an existing private config."""
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise ConfigError("配置已存在，未覆盖；请使用新的 --config 路径或手动编辑")
    if not sys.stdin or not sys.stdin.isatty():
        raise ConfigError("--init 需要交互式终端；无交互环境请复制 config.example.json 填写")
    emit("仅用于本人或获授权的投稿账号；请遵守投稿网站条款。")
    emit("密码输入隐藏；保存于本机配置，不会上传。留空可稍后通过环境变量提供。")
    platform = _prompt("平台 sn / em / mts", "em").lower()
    if platform not in ("sn", "em", "mts"):
        raise ConfigError("平台只能选择 sn、em、mts")
    settings = {}
    if platform in ("em", "mts"):
        from .config import validate_base
        settings["base"] = validate_base(_prompt("期刊根地址（HTTPS，不要附查询参数）"), platform)
    if platform == "em":
        settings["label"] = _prompt("期刊显示名称（可选）")
        if _prompt("是否使用 Continue with Elsevier 登录？y/n", "y").lower() == "y":
            settings["sso"] = "elsevier"
    settings["email"] = _prompt("登录邮箱/用户名")
    settings["password"] = getpass.getpass("投稿系统密码（或留空用 %s）: " % PASSWORD_ENVS[platform])
    if platform == "mts":
        manuscript = _prompt("只监控的 ms_id（数字，留空=全部）")
        settings["watch"] = "single" if manuscript else "all"
        settings["ms_id"] = manuscript or None
    elif platform == "sn":
        value = _prompt("投稿 UUID 或详情 URL（多个用逗号分隔，留空=全部）")
        settings["watch"] = [s.strip() for s in value.split(",") if s.strip()] if value else "all"
    else:
        value = _prompt("稿件编号（多个用逗号分隔，留空=全部；输入 title=无稿号筛选）")
        if value.lower() == "title":
            settings["watch"] = [{"title": _prompt("完整稿件标题"),
                                  "submitted": _prompt("投稿日期（与列表中英文日期完全相同）")}]
        else:
            settings["watch"] = [s.strip() for s in value.split(",") if s.strip()] if value else "all"
    notify = {"toast": sys.platform == "win32", "email": {"enabled": False}}
    if _prompt("启用邮件通知？y/n", "y").lower() == "y":
        security = _prompt("加密方式 ssl / starttls", "ssl").lower()
        try:
            port = int(_prompt("SMTP端口", "587" if security == "starttls" else "465"))
        except ValueError:
            raise ConfigError("SMTP端口须为整数") from None
        user = _prompt("发件邮箱")
        notify["email"] = {"enabled": True, "smtp_host": _prompt("SMTP服务器", "smtp.qq.com"),
                           "smtp_port": port, "smtp_user": user, "smtp_security": security,
                           "smtp_auth_code": getpass.getpass("SMTP授权码（或留空用 SNM_SMTP_AUTH_CODE）: "),
                           "to": _prompt("收件邮箱", user)}
    cfg = {"platform": platform, platform: settings, "notify": notify,
           "interval_minutes": 15, "fail_alert_after": 6,
           "network": {"attempts": 3, "timeout_seconds": 45}}
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ConfigError("配置已被其他进程创建，未覆盖") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(cfg, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    emit("配置已保存至：%s" % path)
    try:
        load_config(path)
    except ConfigError as error:
        emit("待补全：%s" % error)
        emit("请填写配置或设置环境变量，然后运行 --check-config。")
        return EXIT_CONFIG
    emit("配置检查通过；下一步运行 --doctor、--list、--once。尚未登录或发送邮件。")
    return 0


def parser():
    result = argparse.ArgumentParser(description="投稿状态监控：本地查询、邮件/Windows通知")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", default=str(default_config(ROOT)), help="配置文件路径")
    actions = result.add_mutually_exclusive_group()
    actions.add_argument("--init", action="store_true", help="交互式生成私有配置，不覆盖现有配置")
    actions.add_argument("--once", action="store_true", help="检查一次（默认）")
    actions.add_argument("--loop", type=int, metavar="MINUTES", help="常驻运行，间隔至少5分钟")
    actions.add_argument("--show", action="store_true", help="只显示缓存状态和待发通知数量")
    actions.add_argument("--list", dest="list_live", action="store_true", help="联网列出投稿，不建基线不通知")
    actions.add_argument("--check-config", action="store_true", help="只验证本地配置，不联网")
    actions.add_argument("--doctor", action="store_true", help="检查依赖、浏览器、目录及状态文件，不联网")
    actions.add_argument("--test-email", action="store_true", help="明确发送一封测试邮件，不改状态")
    actions.add_argument("--reset", action="store_true", help="重建基线（须 --yes），保留待发通知及凭据")
    result.add_argument("--yes", action="store_true", help="确认 --reset 操作")
    result.add_argument("--no-toast", dest="toast", action="store_false", help="本次不发弹窗，待发弹窗保留至下次")
    result.add_argument("--headed", action="store_true", help="显示自动化浏览器，便于诊断登录流程")
    return result


def main(argv=None):
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    app = None
    try:
        if args.init:
            return init_config(args.config)
        if args.loop is not None and not 5 <= args.loop <= 1440:
            raise ConfigError("--loop 间隔必须为5–1440分钟，不建议高频访问投稿系统")
        if args.reset and not args.yes:
            raise ConfigError("重建基线须明确加 --yes；不会删除登录会话或待发通知")
        app = App(args)
        if args.check_config:
            emit("配置检查通过；未联网、未发送邮件。")
            return 0
        if args.doctor:
            return doctor(app)
        if args.show:
            show(app)
            return 0
        if args.list_live:
            results = app.list_live()
            for platform, items in results.items():
                for key, item in items.items():
                    emit(json.dumps({"platform": platform, "id": item.get("number") or item.get("ms_number") or key,
                                     "title": item.get("title"), "status": item.get("status"),
                                     "submitted": item.get("submitted"), "status_date": item.get("status_date")},
                                    ensure_ascii=False))
            emit("未更新状态基线、未发送通知；登录会话可被更新。")
            return 0
        if args.test_email:
            app.test_email()
            emit("SMTP服务器已接受测试邮件，请自行确认收件及垃圾箱。")
            return 0
        if args.reset:
            app.reset()
            emit("已重建基线；下次首次成功检查不发送状态更新通知。")
            return 0
        if args.loop is not None:
            while True:
                try:
                    app.check()
                except AlreadyRunning as error:
                    app.log(error)
                except (OSError, MonitorError) as error:
                    app.log("检查未完成：%s" % app.redact(error))
                time.sleep(args.loop * 60)
        return app.check()
    except AlreadyRunning as error:
        emit(str(error))
        return EXIT_BUSY
    except KeyboardInterrupt:
        emit("监控已停止；定时任务不受此命令影响。")
        return 130
    except ConfigError as error:
        emit("配置错误：%s" % error)
        return EXIT_CONFIG
    except Exception as error:
        safe = app.redact(error) if app else type(error).__name__
        if app:
            try:
                app.log("运行失败：%s" % safe)
            except OSError:
                pass
        emit("运行失败：%s" % safe)
        return 1
    finally:
        if app:
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
