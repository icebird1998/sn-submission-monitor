"""Local manuscript status monitoring. No affiliation with the submission platforms."""

__version__ = "0.2.0"


def __getattr__(name):
    if name == "App":
        from .app import App
        return App
    raise AttributeError(name)
