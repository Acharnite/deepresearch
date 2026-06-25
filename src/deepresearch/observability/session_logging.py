"""Per-session log file support for debugging individual research sessions.

Each running session gets its own ``logs/session-<session_id>.log`` file
so that concurrent session logs are not interleaved in the global
``logs/deepresearch.log``.

Usage::

    handler = setup_session_logging(session_id, topic)
    try:
        ...  # session work
    finally:
        teardown_session_logging(handler)
"""

from __future__ import annotations

import logging
from pathlib import Path


class SessionFilter(logging.Filter):
    """Injects *session_id* into every log record so the formatter can
    use ``%(session_id)s``."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id
        return True


def _get_log_dir() -> Path:
    """Return the ``logs/`` directory relative to the project root.

    Uses the same resolution as ``server.py`` so that session log files
    land next to ``deepresearch.log``.
    """
    return Path(__file__).resolve().parent.parent.parent.parent / "logs"


def setup_session_logging(session_id: str, topic: str) -> logging.Handler:
    """Create a per-session log file at ``logs/session-<session_id>.log``.

    The handler is registered on the **root** logger so that **all**
    ``deepresearch.*`` loggers write to it.

    Parameters
    ----------
    session_id:
        8-character session identifier (e.g. ``"a1b2c3d4"``).
    topic:
        Research topic, written in the initial delimiter line.

    Returns
    -------
    logging.Handler
        The handler instance.  Keep a reference so it can be removed
        in :func:`teardown_session_logging`.

    Raises
    ------
    OSError
        If the ``logs/`` directory cannot be created or the log file
        cannot be opened (caller should catch and log a warning).
    """
    log_dir = _get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"session-{session_id}.log"

    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s [%(session_id)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    handler.addFilter(SessionFilter(session_id))

    root = logging.getLogger()
    root.addHandler(handler)

    # Write initial delimiter line so the file immediately has context.
    # Use handler.handle() instead of emit() so the SessionFilter runs
    # (StreamHandler.emit() in Python 3.13 bypasses filters).
    handler.handle(
        logging.LogRecord(
            name=__name__,
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=f"=== Session {session_id} started: {topic} ===",
            args=None,
            exc_info=None,
        )
    )

    return handler


def teardown_session_logging(handler: logging.Handler) -> None:
    """Remove *handler* from the root logger and close it.

    This flushes any buffered output and closes the underlying file.
    It is safe to call multiple times on the same handler.
    """
    root = logging.getLogger()
    root.removeHandler(handler)
    handler.close()
