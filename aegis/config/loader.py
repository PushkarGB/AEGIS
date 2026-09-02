"""Load validated AEGIS configuration from external YAML or JSON files."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from .schemas import AegisConfig, AgentConfig, CapabilityRegistryConfig, ModelRegistryConfig, RuntimeSettings

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

DEFAULT_FILE_NAMES = {
    "agent": "agent.yaml",
    "models": "models.yaml",
    "capabilities": "capabilities.yaml",
    "runtime": "runtime.yaml",
}

CONFIG_ENV_VARS = {
    "config_dir": "AEGIS_CONFIG_DIR",
    "agent": "AEGIS_AGENT_CONFIG",
    "models": "AEGIS_MODELS_CONFIG",
    "capabilities": "AEGIS_CAPABILITIES_CONFIG",
    "runtime": "AEGIS_RUNTIME_CONFIG",
}


@dataclass(frozen=True)
class AegisConfigPaths:
    """Resolved file paths for each config section."""

    agent: Path
    models: Path
    capabilities: Path
    runtime: Path


def _coerce_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def resolve_config_paths(
    config_dir: str | Path | None = None,
    overrides: Mapping[str, str | Path] | None = None,
) -> AegisConfigPaths:
    """Resolve config file locations from arguments and environment overrides."""

    overrides = overrides or {}
    env_config_dir = os.getenv(CONFIG_ENV_VARS["config_dir"])
    base_dir = _coerce_path(env_config_dir or config_dir or DEFAULT_CONFIG_DIR)

    resolved = {}
    for section in DEFAULT_FILE_NAMES:
        override_value = overrides.get(section) or os.getenv(CONFIG_ENV_VARS[section])
        resolved[section] = _coerce_path(override_value) if override_value else base_dir / DEFAULT_FILE_NAMES[section]

    return AegisConfigPaths(**resolved)


def _load_mapping(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    suffix = path.suffix.lower()
    raw_text = path.read_text(encoding="utf-8")
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(raw_text) or {}
    elif suffix == ".json":
        data = json.loads(raw_text)
    else:
        raise ValueError(f"Unsupported config format for '{path}': use .yaml, .yml, or .json")

    if not isinstance(data, dict):
        raise ValueError(f"Configuration file '{path}' must contain an object mapping")

    return data


def load_config(
    config_dir: str | Path | None = None,
    overrides: Mapping[str, str | Path] | None = None,
) -> AegisConfig:
    """Load and validate the complete AEGIS configuration."""

    paths = resolve_config_paths(config_dir=config_dir, overrides=overrides)
    return AegisConfig(
        agent=AgentConfig.model_validate(_load_mapping(paths.agent)),
        models=ModelRegistryConfig.model_validate(_load_mapping(paths.models)),
        capabilities=CapabilityRegistryConfig.model_validate(
            _load_mapping(paths.capabilities)
        ),
        runtime=RuntimeSettings.model_validate(_load_mapping(paths.runtime)),
    )
