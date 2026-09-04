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
from aegis.ui.runner import DeterministicTaskRunner, RuntimeTaskRunner
from aegis.ui.service import UIBackendService, UIStreamUpdate, UITaskResult

__all__ = [
    "DeterministicTaskRunner",
    "MOCK_EVENT_PACE_SECONDS",
    "RuntimeTaskRunner",
    "SessionEventCollector",
    "UIBackendService",
    "UIStreamUpdate",
    "UITaskResult",
    "create_app",
    "event_label",
    "format_progressive_events",
]
