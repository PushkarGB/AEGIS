#!/usr/bin/env python3
"""Launch AEGIS UI wired to a remote Ollama endpoint with persistent audit logs.

The AEGIS business logic layer is fully provider-agnostic. This script is an
operational convenience that overrides the default local_ollama provider endpoint
to point at any remote Ollama instance (e.g. Colab+ngrok, cloud VM, LAN server).

Usage:
    python scripts/launch_colab.py --colab-url http://your-ollama-host:11434
    python scripts/launch_colab.py  # uses OLLAMA_BASE_URL or default

Environment variables:
    AEGIS_COLAB_OLLAMA_URL: Remote Ollama URL (highest priority).
    OLLAMA_BASE_URL:        Standard Ollama base URL (fallback).
    PORT:                   Web UI port (default: 7860)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aegis.audit import PersistentAuditService
from aegis.capabilities import DockerSandboxRunner
from aegis.config import load_config
from aegis.orchestration import RuntimeTaskRunner
from aegis.router import OllamaModelProvider
from aegis.ui.app import create_app
from aegis.ui.service import UIBackendService


DEFAULT_COLAB_URL = "https://diagnosis-credibly-scoring.ngrok-free.dev"


def clean_url(url: str) -> str:
    """Normalize base URL by stripping trailing slash and /v1 suffix."""
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3].rstrip("/")
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch AEGIS connected to Colab Ollama runtime with persistent audit logging."
    )
    parser.add_argument(
        "--colab-url",
        default=os.getenv("AEGIS_COLAB_OLLAMA_URL") or os.getenv("OLLAMA_BASE_URL") or DEFAULT_COLAB_URL,
        help=f"Public URL for Colab Ollama runtime (default: {DEFAULT_COLAB_URL})",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind Gradio UI (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "7860")),
        help="Port to bind Gradio UI (default: 7860)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Inference timeout in seconds (default: 120.0)",
    )
    args = parser.parse_args()

    colab_url = clean_url(args.colab_url)
    audit_file = Path("data/audit/events.jsonl")

    print("=" * 68)
    print("   AEGIS Sovereign Workbench — Colab Ollama Integration")
    print("=" * 68)
    print(f"Colab Endpoint   : {colab_url}")
    print(f"Audit Log File   : {audit_file.resolve()}")
    print(f"Sandbox Runner   : DockerSandboxRunner (python:3.11-slim, timeout={args.timeout}s)")
    print(f"Inference Timeout: {args.timeout}s")
    print(f"UI Address       : http://{args.host}:{args.port}")
    print("=" * 68)

    # 1. Load system config
    config = load_config()

    # 2. Wire Ollama provider pointed to the remote endpoint
    remote_provider = OllamaModelProvider(
        base_url=colab_url,
        model="qwen3:8b",
        timeout_seconds=args.timeout,
        non_thinking=True,
        model_configs=config.models.models,
    )
    # Map AEGIS role models to the models available on the remote Ollama server
    remote_provider._provider_models.update({
        "agent_model": "qwen3:8b",
        "coding_model": "qwen2.5-coder:7b",
        "vision_model": "qwen3.5:latest",
    })

    # Override the local_ollama provider to point at the remote Ollama endpoint
    providers = {
        "local_ollama": remote_provider,
    }

    # Verify connection to remote Ollama
    health = remote_provider.check_health()
    if health.healthy:
        print(f"Colab Connection : CONNECTED (Server models: {health.available_models})")
    else:
        print(f"Colab Connection : WARNING — {health.error_message}")

    # 3. Build persistent audit store
    audit_service = PersistentAuditService(log_path=audit_file)

    # 4. Wire RuntimeTaskRunner with real Agent Runtime + Docker Sandbox
    runner = RuntimeTaskRunner(
        providers=providers,
        sandbox_runner=DockerSandboxRunner(
            timeout_seconds=args.timeout,
        ),
        config=config,
    )

    # 5. Initialize backend service and Gradio app
    backend = UIBackendService(
        runner=runner,
        audit_service=audit_service,
    )
    app = create_app(backend)

    print("\n[+] Starting AEGIS Gradio interface...\n")
    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
