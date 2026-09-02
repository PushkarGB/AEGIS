"""Configuration loading and validation tests for Phase 0.2."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis.config import (
    DEFAULT_CONFIG_DIR,
    CapabilityRegistryConfig,
    RuntimeSettings,
    load_config,
)


def test_load_config_uses_repository_defaults():
    config = load_config()

    assert config.agent.default_model_role == "agent"
    assert config.models.role_defaults["coding"] == "coding_model_placeholder"
    assert any(
        capability.name == "generate_code"
        for capability in config.capabilities.capabilities
    )
    assert config.runtime.sandbox.network_enabled is False
    assert config.runtime.allow_temporary_api_provider is False


def test_load_config_accepts_json_override_via_environment(monkeypatch):
    scratch_dir = Path("tests/.tmp_config")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True)

    override_path = scratch_dir / "agent.json"
    override_path.write_text(
        json.dumps(
            {
                "name": "test-agent",
                "description": "JSON override for the agent config.",
                "default_model_role": "agent",
                "planning": {
                    "max_plan_steps": 3,
                    "max_observation_chars": 2048,
                },
                "allowed_modalities": ["document"],
                "default_capabilities": ["finish"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AEGIS_CONFIG_DIR", str(DEFAULT_CONFIG_DIR))
    monkeypatch.setenv("AEGIS_AGENT_CONFIG", str(override_path))

    try:
        config = load_config()
    finally:
        shutil.rmtree(scratch_dir)

    assert config.agent.name == "test-agent"
    assert config.agent.planning.max_plan_steps == 3
    assert config.agent.default_capabilities == ["finish"]


def test_runtime_settings_reject_networked_sandbox():
    with pytest.raises(ValidationError):
        RuntimeSettings.model_validate(
            {
                "environment": "development",
                "session_db_path": "data/sessions.sqlite3",
                "audit_log_path": "data/audit/events.jsonl",
                "artifacts_dir": "artifacts",
                "sandbox": {
                    "enabled": True,
                    "network_enabled": True,
                    "container_runtime": "docker",
                },
                "controller": {
                    "max_retries": 2,
                    "max_iterations": 6,
                },
                "ui": {
                    "stream_execution_events": True,
                    "show_chain_of_thought": False,
                },
                "allow_temporary_api_provider": False,
            }
        )


def test_capability_registry_rejects_duplicate_capabilities():
    with pytest.raises(ValidationError):
        CapabilityRegistryConfig.model_validate(
            {
                "capabilities": [
                    {
                        "name": "finish",
                        "kind": "control",
                        "enabled": True,
                        "description": "Finish workflow.",
                        "handler_key": "workflow.finish",
                    },
                    {
                        "name": "finish",
                        "kind": "control",
                        "enabled": True,
                        "description": "Duplicate finish workflow.",
                        "handler_key": "workflow.finish",
                    },
                ]
            }
        )
