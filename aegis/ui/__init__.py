"""AEGIS Gradio UI package.

Exposes the application factory and UI backend service facade.
"""

from aegis.ui.app import create_app
from aegis.ui.event_stream import (
    MOCK_EVENT_PACE_SECONDS,
    SessionEventCollector,
    event_label,
    format_progressive_events,
)
from aegis.ui.service import UIBackendService, UIStreamUpdate, UITaskResult

__all__ = [
    "MOCK_EVENT_PACE_SECONDS",
    "SessionEventCollector",
    "UIBackendService",
    "UIStreamUpdate",
    "UITaskResult",
    "create_app",
    "event_label",
    "format_progressive_events",
]
