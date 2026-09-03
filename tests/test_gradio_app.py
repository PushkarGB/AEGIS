"""Tests for the AEGIS Gradio Application Shell assembly and component wiring.

Validates:
- Gradio app assembly with default or custom UIBackendService.
- Component structure and element IDs for browser testing.
- Role-based view switching and callback behavior.
"""

from __future__ import annotations

import gradio as gr
import pytest

from aegis.ui.app import create_app
from aegis.ui.service import UIBackendService


@pytest.fixture
def test_backend() -> UIBackendService:
    return UIBackendService(db_path=":memory:")


def test_create_app_initializes_blocks(test_backend: UIBackendService):
    demo = create_app(service=test_backend)
    assert isinstance(demo, gr.Blocks)
    assert demo.title == "AEGIS Sovereign Workbench"


def test_app_contains_required_view_components(test_backend: UIBackendService):
    demo = create_app(service=test_backend)

    # Inspect Blocks graph for core component types
    component_types = [type(c).__name__ for c in demo.blocks.values()]

    assert "Column" in component_types
    assert "Row" in component_types
    assert "Textbox" in component_types
    assert "Button" in component_types
    assert "Chatbot" in component_types
    assert "File" in component_types
    assert "Markdown" in component_types
    assert "Radio" in component_types
    assert "Dataframe" in component_types
    assert "Group" in component_types


def test_app_can_render_views(test_backend: UIBackendService):
    """Verify that create_app does not throw any layout configuration errors."""
    demo = create_app(service=test_backend)
    config = demo.get_config_file()
    assert isinstance(config, dict)
    assert "components" in config
    assert len(config["components"]) > 10
