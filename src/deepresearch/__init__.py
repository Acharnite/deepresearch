"""Multi-agent AI research system."""

from importlib.metadata import version as _get_version

try:
    __version__ = _get_version("deepresearch")
except Exception:
    __version__ = "0.0.0-dev"
