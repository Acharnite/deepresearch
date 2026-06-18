"""Multi-agent AI research system."""

from pathlib import Path

try:
    _version_path = Path(__file__).resolve().parent.parent.parent / "VERSION.md"
    __version__ = _version_path.read_text(encoding="utf-8").strip()
except Exception:
    __version__ = "0.0.0-dev"
