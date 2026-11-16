"""Errors safe to report without a traceback or authentication data."""


class MonitorError(Exception):
    pass


class ConfigError(MonitorError):
    pass


class StateError(MonitorError):
    pass


class AlreadyRunning(MonitorError):
    pass
