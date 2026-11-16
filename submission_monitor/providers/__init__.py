"""Lazy provider dispatch; optional browser/HTTP dependencies stay optional."""


def collect(app, platform):
    """Collect one platform selected by the configuration helper."""
    if platform == "sn":
        from .sn import sn_run
        watch = app.cfg.get("sn", {}).get("watch", "all")
        return sn_run(app, None if watch == "all" else watch)
    if platform == "mts":
        from .mts import mts_run
        cfg = app.cfg.get("mts", {})
        watch = cfg.get("watch")
        only = None if watch == "all" else (watch if isinstance(watch, list) else cfg.get("ms_id"))
        return mts_run(app, only)
    if platform == "em":
        from .em import em_run
        return em_run(app)
    raise ValueError("collect expects one selected platform: sn, mts or em")


def sn_run(app, check_only_uuids=None):
    from .sn import sn_run as run
    return run(app, check_only_uuids)


def mts_run(app, check_only_ms=None):
    from .mts import mts_run as run
    return run(app, check_only_ms)


def em_run(app):
    from .em import em_run as run
    return run(app)


def select_em_manuscripts(items, watch, previous=None):
    from .em import select_em_manuscripts as select
    return select(items, watch, previous)


__all__ = ["collect", "sn_run", "mts_run", "em_run", "select_em_manuscripts"]
