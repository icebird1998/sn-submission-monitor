"""Configuration validation and environment overrides; never print secret values."""
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError

PASSWORD_ENVS = {
    "sn": "SNM_SN_PASSWORD",
    "mts": "SNM_MTS_PASSWORD",
    "em": "SNM_EM_PASSWORD",
}
PLACEHOLDERS = ("YOUR_", "TO_BE", "CHANGE_ME", "REPLACE_ME")


def configured(value):
    return isinstance(value, str) and bool(value.strip()) and not value.upper().startswith(PLACEHOLDERS)


def enabled_platforms(cfg):
    value = cfg.get("platform", "sn")
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names:
        raise ConfigError("platform 必须为 sn、mts、em、both、all 或它们的非空列表")
    result = []
    for name in names:
        if not isinstance(name, str) or name not in ("sn", "mts", "em", "both", "all"):
            raise ConfigError("platform 包含不支持的平台")
        for item in {"both": ["sn", "mts"], "all": ["sn", "mts", "em"]}.get(name, [name]):
            if item not in result:
                result.append(item)
    return result


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError("%s 必须为 %d–%d 的整数" % (name, minimum, maximum))
    return value


def validate_base(value, platform):
    if not isinstance(value, str):
        raise ConfigError("%s.base 必须为 HTTPS URL" % platform)
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ConfigError("%s.base URL 无效" % platform) from None
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or port not in (None, 443)):
        raise ConfigError("%s.base 必须为不带查询参数、凭据的 HTTPS 站点地址" % platform)
    host = url.hostname.lower()
    if platform == "em":
        if (host != "editorialmanager.com" and not host.endswith(".editorialmanager.com")):
            raise ConfigError("em.base 必须指向 editorialmanager.com 投稿系统")
        if not re.fullmatch(r"/[A-Za-z0-9_-]+/?", url.path):
            raise ConfigError("em.base 请填期刊根地址，例如 https://www.editorialmanager.com/example")
    elif platform == "mts":
        if not (host.startswith("mts-") and host.endswith(".nature.com")) or url.path not in ("", "/"):
            raise ConfigError("mts.base 请填 https://mts-期刊代码.nature.com（不是 ScholarOne）")
    return value.rstrip("/")


def _watch(cfg, platform):
    watch = cfg.get("watch", "all")
    if watch == "all":
        return
    if platform == "mts":
        if watch != "single" or not re.fullmatch(r"[0-9]+", str(cfg.get("ms_id", ""))):
            raise ConfigError("MTS 单稿请填 watch: single 和数字 ms_id")
        return
    if not isinstance(watch, list) or not watch:
        raise ConfigError("%s.watch 必须为 all 或非空筛选列表" % platform)
    for target in watch:
        if isinstance(target, str) and target.strip():
            if platform == "sn":
                raw = target.strip()
                if raw.startswith("https://"):
                    parsed = urlsplit(raw)
                    if (parsed.hostname != "submission.springernature.com" or parsed.query
                            or parsed.fragment or parsed.username or parsed.password
                            or not re.fullmatch(r"/submission-details/[0-9a-fA-F-]+/?", parsed.path)):
                        raise ConfigError("sn.watch 只接受 Springer Nature 投稿详情 URL 或 UUID")
                    raw = parsed.path.rstrip("/").rsplit("/", 1)[-1]
                if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", raw):
                    raise ConfigError("sn.watch 投稿 UUID 格式无效")
            elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. /-]{0,149}", target.strip()):
                raise ConfigError("em.watch 稿件号格式无效")
        elif (platform == "em" and isinstance(target, dict)
              and set(target) == {"title", "submitted"}
              and all(isinstance(v, str) and v.strip() for v in target.values())):
            continue
        else:
            raise ConfigError("%s.watch 条目须为稿号/UUID，EM 无稿号时可填 title/submitted" % platform)


def _address(value, field):
    if (not isinstance(value, str) or "\n" in value or "\r" in value
            or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value)):
        raise ConfigError("%s 邮箱格式无效" % field)
    return value


def load_config(path):
    file = Path(path).expanduser().resolve()
    try:
        cfg = json.loads(file.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigError("未找到配置文件，请先运行 --init 或指定 --config") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("配置文件无法读取或不是有效的 UTF-8 JSON；请勿在 JSON 中写注释") from None
    if not isinstance(cfg, dict):
        raise ConfigError("配置根节点必须为 JSON 对象")
    cfg = copy.deepcopy(cfg)
    for platform, env in PASSWORD_ENVS.items():
        if os.environ.get(env):
            if not isinstance(cfg.get(platform, {}), dict):
                raise ConfigError("%s 必须为配置对象" % platform)
            cfg.setdefault(platform, {})["password"] = os.environ[env]
    if os.environ.get("SNM_SMTP_AUTH_CODE"):
        if not isinstance(cfg.get("notify", {}), dict):
            raise ConfigError("notify 必须为配置对象")
        email = cfg.setdefault("notify", {}).setdefault("email", {})
        if not isinstance(email, dict):
            raise ConfigError("notify.email 必须为配置对象")
        email["smtp_auth_code"] = os.environ["SNM_SMTP_AUTH_CODE"]
    platforms = enabled_platforms(cfg)
    for platform in platforms:
        settings = cfg.get(platform)
        if not isinstance(settings, dict):
            raise ConfigError("缺少 %s 平台配置" % platform)
        for key in ("email", "password"):
            if not configured(settings.get(key)):
                raise ConfigError("%s.%s 未填写，可通过 %s 传入密码" %
                                  (platform, key, PASSWORD_ENVS[platform]))
        if platform in ("em", "mts"):
            settings["base"] = validate_base(settings.get("base"), platform)
        if platform == "em" and settings.get("sso") not in (None, "", "elsevier"):
            raise ConfigError("em.sso 仅支持 elsevier 或省略；ORCID/MFA 需人工处理")
        _watch(settings, platform)
    notify = cfg.setdefault("notify", {})
    if not isinstance(notify, dict):
        raise ConfigError("notify 必须为配置对象")
    if not isinstance(notify.setdefault("toast", True), bool):
        raise ConfigError("notify.toast 必须为 true 或 false")
    email = notify.get("email")
    if email is None:
        email = notify["email"] = {}
    if not isinstance(email, dict):
        raise ConfigError("notify.email 必须为对象，或省略以关闭邮件")
    enabled = email.get("enabled", bool(configured(email.get("smtp_auth_code"))))
    if not isinstance(enabled, bool):
        raise ConfigError("notify.email.enabled 必须为 true 或 false")
    email["enabled"] = enabled
    if enabled:
        if not configured(email.get("smtp_auth_code")):
            raise ConfigError("SMTP 授权码未填写，可使用 SNM_SMTP_AUTH_CODE")
        host = email.get("smtp_host", "")
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ConfigError("smtp_host 请只填写邮件服务器域名")
        _address(email.get("smtp_user"), "smtp_user")
        recipients = email.get("to")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list) or not recipients:
            raise ConfigError("notify.email.to 请填收件邮箱或非空邮箱列表")
        email["to"] = [_address(value, "to") for value in recipients]
        email["smtp_port"] = _integer(email.get("smtp_port", 465), "smtp_port", 1, 65535)
        security = email.setdefault("smtp_security", "ssl")
        if security not in ("ssl", "starttls"):
            raise ConfigError("smtp_security 仅支持 ssl（常用465）或 starttls（常用587）")
    cfg["interval_minutes"] = _integer(cfg.get("interval_minutes", 15), "interval_minutes", 5, 1440)
    cfg["fail_alert_after"] = _integer(cfg.get("fail_alert_after", 6), "fail_alert_after", 1, 100)
    network = cfg.setdefault("network", {})
    if not isinstance(network, dict):
        raise ConfigError("network 必须为配置对象")
    network["attempts"] = _integer(network.get("attempts", 3), "network.attempts", 1, 5)
    network["timeout_seconds"] = _integer(network.get("timeout_seconds", 45), "network.timeout_seconds", 5, 120)
    if "runtime_dir" in cfg and (not isinstance(cfg["runtime_dir"], str) or not cfg["runtime_dir"].strip()):
        raise ConfigError("runtime_dir 必须为非空目录路径")
    return cfg


def data_directory(cfg, config_file):
    parent = Path(config_file).expanduser().resolve().parent
    value = cfg.get("runtime_dir")
    if not value:
        return parent
    directory = Path(value).expanduser()
    return directory.resolve() if directory.is_absolute() else (parent / directory).resolve()


def default_config(root):
    root = Path(root)
    local = root / ".local" / "config.json"
    legacy = root / "config.json"
    return local if local.exists() or not legacy.exists() else legacy
