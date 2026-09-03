#!/usr/bin/env python3
"""Manual integration check script for validating live Ollama endpoints with AEGIS.

Usage:
    python scripts/check_ollama_endpoint.py [--base-url URL] [--model MODEL] [--timeout SECONDS]

Environment variables:
    OLLAMA_BASE_URL: Base URL for Ollama (default: http://127.0.0.1:11434)
    OLLAMA_MODEL: Model tag to use (default: qwen2.5:7b)
    OLLAMA_TIMEOUT: Request timeout in seconds (default: 60)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aegis.config import AgentConfig, ModelConfig, ModelProviderConfig, ModelRegistryConfig, load_ollama_config
from aegis.agent import AttachmentDescriptor, IntentAnalysisRequest, RouterAgentRuntime
from aegis.router import (
    ModelGenerationRequest,
    ModelRegistry,
    ModelRouter,
    OllamaModelProvider,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AEGIS manual integration check for live Ollama HTTP endpoint."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Ollama base URL (defaults to OLLAMA_BASE_URL or http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model tag to test (defaults to OLLAMA_MODEL or qwen2.5:7b)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout in seconds (defaults to OLLAMA_TIMEOUT or 60.0)",
    )
    parser.add_argument(
        "--allow-thinking",
        action="store_true",
        help="Do not strip <think>...</think> reasoning tags",
    )
    args = parser.parse_args()

    config = load_ollama_config(
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout,
        non_thinking=not args.allow_thinking,
    )

    print("=" * 65)
    print("  AEGIS Sovereign Workbench — Ollama Endpoint Live Check")
    print("=" * 65)
    print(f"Base URL:       {config.base_url}")
    print(f"Target Model:   {config.model}")
    print(f"Timeout:        {config.timeout_seconds}s")
    print(f"Non-thinking:   {config.non_thinking}")
    print("-" * 65)

    provider = OllamaModelProvider(
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        non_thinking=config.non_thinking,
    )

    # 1. Health & Discovery Check
    print("[1/3] Checking Ollama endpoint health & discovering tags...")
    start_time = time.monotonic()
    health = provider.check_health()
    health_elapsed = time.monotonic() - start_time

    if not health.healthy:
        print(f"[-] Health check FAILED in {health_elapsed:.2f}s: {health.error_message}")
        print("    Ensure the Ollama server is running and accessible.")
        return 1

    print(f"[+] Endpoint is {health.status.value.upper()} ({health_elapsed:.2f}s)")
    if health.version:
        print(f"    Server version:    {health.version}")
    print(f"    Available models:  {', '.join(health.available_models) if health.available_models else 'None listed'}")
    print(f"    Resolved tag:      {health.resolved_model}")

    # 2. Generation & Non-thinking Check
    print("\n[2/3] Performing test generation via ModelProvider interface...")
    test_request = ModelGenerationRequest(
        model_id="agent_model",
        system_prompt="You are an industrial safety assistant. Answer concisely in one sentence.",
        prompt="Confirm you are ready to process sovereign industrial telemetry.",
    )

    gen_start = time.monotonic()
    try:
        gen_result = provider.generate(test_request)
        gen_elapsed = time.monotonic() - gen_start
        print(f"[+] Generation succeeded in {gen_elapsed:.2f}s:")
        print(f"    Response: {gen_result.text}")

        if "<think>" in gen_result.text:
            print("[-] WARNING: <think> tags detected in output despite non-thinking mode!")
        else:
            print("[+] Verified non-thinking mode (no reasoning tags in deliverable text).")
    except Exception as exc:
        print(f"[-] Generation FAILED: {exc}")
        return 1

    # 3. Agent → ModelRouter → ModelProvider pipeline check
    print("\n[3/3] Verifying Agent → ModelRouter → ModelProvider pipeline...")
    registry_config = ModelRegistryConfig(
        providers=[
            ModelProviderConfig(
                id="ollama_local",
                kind="ollama",
                enabled=True,
                endpoint=config.base_url,
                timeout_seconds=int(config.timeout_seconds),
            )
        ],
        models=[
            ModelConfig(
                id="agent_model_ollama",
                provider="ollama_local",
                provider_model_id=config.model,
                roles=["agent"],
                capabilities=["text_generation", "reasoning", "planning", "drafting"],
            )
        ],
        role_defaults={"agent": "agent_model_ollama"},
    )

    router = ModelRouter(ModelRegistry(registry_config))
    agent_config = AgentConfig(
        name="aegis-agent",
        description="AEGIS runtime agent",
        default_model_role="agent",
        allowed_modalities=["spreadsheet", "scanned_document", "image"],
    )
    agent = RouterAgentRuntime(
        config=agent_config,
        router=router,
        providers={"ollama_local": provider},
    )

    try:
        intent_start = time.monotonic()
        decision = agent.decide_intent(
            IntentAnalysisRequest(
                user_goal="Calculate average equipment wall thickness across rows.",
                attachments=[AttachmentDescriptor(name="readings.xlsx")],
            )
        )
        intent_elapsed = time.monotonic() - intent_start
        print(f"[+] Agent intent resolution succeeded ({intent_elapsed:.2f}s):")
        print(f"    Intent:    {decision.intent.value}")
        print(f"    Modality:  {decision.modality.value}")
        print(f"    Summary:   {decision.summary}")
    except Exception as exc:
        print(f"[-] Agent routing through ModelProvider FAILED: {exc}")
        return 1

    print("\n" + "=" * 65)
    print("  ALL CHECKS PASSED: AEGIS is fully connected to Ollama endpoint.")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
