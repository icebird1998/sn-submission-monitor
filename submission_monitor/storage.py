"""Atomic private JSON files and an OS-backed, crash-safe single-run lock."""
import copy
import errno
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .errors import AlreadyRunning, StateError


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def new_state():
    return {"schema_version": 2, "sn": {}, "mts": {}, "em": {}, "_scope": {},
            "_outbox": [], "_meta": {"consecutive_failures": 0, "alert_sent": False,
                                       "providers": {}}}


def read_state(path):
    path = Path(path)
    if not path.exists():
        return new_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise StateError("状态文件无法读取或损坏；已停止检查，未重置历史或通知队列。请先备份修复。") from None
    if not isinstance(state, dict) or state.get("schema_version", 1) not in (1, 2):
        raise StateError("状态文件格式/版本不受支持，未覆盖原文件")
    defaults = new_state()
    for field in ("sn", "mts", "em", "_scope", "_meta"):
        state.setdefault(field, copy.deepcopy(defaults[field]))
        if not isinstance(state[field], dict):
            raise StateError("状态字段 %s 必须为对象，未重置原文件" % field)
    for platform in ("sn", "mts", "em"):
        if any(not isinstance(item, dict) for item in state[platform].values()):
            raise StateError("状态中的稿件记录损坏，未重置原文件")
    state.setdefault("_outbox", [])
    if not isinstance(state["_outbox"], list):
        raise StateError("通知队列损坏，未重置原文件")
    for event in state["_outbox"]:
        if (not isinstance(event, dict) or not isinstance(event.get("channels"), dict)
                or not isinstance(event.get("subject"), str) or not isinstance(event.get("body"), str)):
            raise StateError("通知队列条目损坏，未丢弃任何通知")
        for delivery in event["channels"].values():
            if (not isinstance(delivery, dict) or not isinstance(delivery.get("done"), bool)
                    or not isinstance(delivery.get("attempts", 0), int)):
                raise StateError("通知投递状态损坏，未丢弃任何通知")
    meta = state["_meta"]
    meta.setdefault("providers", {})
    if not isinstance(meta["providers"], dict):
        raise StateError("平台检查记录损坏")
    meta.setdefault("consecutive_failures", 0)
    meta.setdefault("alert_sent", False)
    if (not isinstance(meta["consecutive_failures"], int) or meta["consecutive_failures"] < 0
            or any(not isinstance(value, dict) for value in meta["providers"].values())):
        raise StateError("检查失败计数损坏")
    for value in meta["providers"].values():
        count = value.get("consecutive_failures", 0)
        if not isinstance(count, int) or count < 0:
            raise StateError("平台失败计数损坏")
    state["schema_version"] = 2
    return state


@contextmanager
def run_lock(path):
    """The OS releases the lock on process exit; do not unlink a shared lock file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(descriptor, "r+b")
    acquired = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise AlreadyRunning("同一运行目录已有监控进程在检查，本次未重复运行") from None
            raise
        stream.seek(1)
        stream.write((" pid=%d\n" % os.getpid()).encode("ascii"))
        stream.truncate()
        stream.flush()
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
