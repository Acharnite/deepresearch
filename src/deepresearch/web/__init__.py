"""Web dashboard package for DeepeResearch."""

from deepresearch.web.event_bus import EventBus, event_bus
from deepresearch.web.server import app
from deepresearch.web.sessions import MultiSessionManager, SessionInfo, multi_session_manager
from deepresearch.web.session_manager import SessionManager, session_manager
from deepresearch.web.settings_manager import SettingsManager, settings_manager
from deepresearch.web.state import update_status

__all__ = [
    "EventBus",
    "event_bus",
    "MultiSessionManager",
    "SessionInfo",
    "multi_session_manager",
    "app",
    "SessionManager",
    "session_manager",
    "SettingsManager",
    "settings_manager",
    "update_status",
]
