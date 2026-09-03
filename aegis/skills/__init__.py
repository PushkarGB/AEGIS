"""Workflow skills (computation, inspection drafting, multimodal).

Skills sit between Agent semantic understanding and capability invocations.
They prepare inputs, construct prompts, and parse observations — they do
not execute capabilities directly.
"""

from .computation import (
    CodeGenerationPrompt,
    ComputationContext,
    ExecutionOutcome,
    build_code_generation_prompt,
    build_retry_context,
    parse_execution_observation,
    prepare_generate_code_inputs,
    prepare_run_code_inputs,
)

__all__ = [
    "CodeGenerationPrompt",
    "ComputationContext",
    "ExecutionOutcome",
    "build_code_generation_prompt",
    "build_retry_context",
    "parse_execution_observation",
    "prepare_generate_code_inputs",
    "prepare_run_code_inputs",
]
