#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entry point; implementation lives in submission_monitor."""
import sys

from submission_monitor.app import App, norm
from submission_monitor.cli import main
from submission_monitor.config import enabled_platforms


def check_once(app):
    return app.check()


def __getattr__(name):
    if name in ("em_run", "select_em_manuscripts"):
        from submission_monitor.providers import em
        return getattr(em, name)
    if name == "sn_run":
        from submission_monitor.providers.sn import sn_run
        return sn_run
    if name == "mts_run":
        from submission_monitor.providers.mts import mts_run
        return mts_run
    raise AttributeError(name)


if __name__ == "__main__":
    sys.exit(main())
