"""Tests for Phase 6.2 computation workflow skill.

Covers:
- Prompt construction from ComputationContext
- Input preparation for generate_code and run_code
- Execution observation parsing
- Retry context building
- GenerateCodeCapability through MockModelProvider
- RunCodeCapability through MockSandboxRunner
- Capability registration and Broker resolution
- Controller integration for the computation workflow
- Error recovery path (sandbox failure → retry → success)
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    CapabilityKind,
    CapabilityRegistry,
    GenerateCodeCapability,
    InspectSpreadsheetCapability,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
    SandboxRunner,
)
from aegis.capabilities.base import Capability, CapabilityMetadata
from aegis.capabilities.run_code import SandboxResult as SandboxResultDirect
from aegis.config import load_config
from aegis.orchestration import ExecutionController, ExecutionEventKind, WorkflowName
from aegis.router import (
    MockModelProvider,
    ModelGenerationRequest,
    ModelGenerationResult,
    ModelRegistry,
    ModelRouter,
)
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
    TaskState,
)
from aegis.skills import (
    CodeGenerationPrompt,
    ComputationContext,
    ExecutionOutcome,
    build_code_generation_prompt,
    build_retry_context,
    parse_execution_observation,
    prepare_generate_code_inputs,
    prepare_run_code_inputs,
)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

def _sample_context(**overrides) -> ComputationContext:
    """Build a representative ComputationContext for testing."""
    defaults = {
        "user_goal": (
            "Calculate the average measured thickness for each equipment item "
            "and identify which equipment has fallen below its minimum acceptable thickness."
        ),
        "file_path": "/data/inspection_readings.xlsx",
        "sheet_names": ["Readings"],
        "columns": ["Equipment_ID", "Location", "Measured_Thickness", "Min_Acceptable_Thickness", "Date"],
        "numeric_fields": ["Measured_Thickness", "Min_Acceptable_Thickness"],
        "row_count": 150,
        "representative_values": {
            "Equipment_ID": ["EQ-001", "EQ-002", "EQ-003"],
            "Measured_Thickness": [4.5, 3.8, 5.1],
            "Min_Acceptable_Thickness": [4.0, 4.0, 4.0],
        },
    }
    defaults.update(overrides)
    return ComputationContext(**defaults)


def _mock_router_and_providers(code_response: str = "print('hello')"):
    """Build a MockModelProvider + ModelRouter for generate_code tests."""
    config = load_config()

    registry = ModelRegistry(config.models)
    router = ModelRouter(registry)

    # Find the coding model's provider ID
    coding_models = registry.get_models_for_role("coding")
    assert coding_models, "No coding model in config"
    coding_model = coding_models[0]

    provider = MockModelProvider(
        response_factory=lambda req: code_response,
    )
    providers = {coding_model.provider: provider}

    return router, providers, provider


def _mock_capability_registry(
    generate_code_cap: GenerateCodeCapability | None = None,
    run_code_cap: RunCodeCapability | None = None,
    inspect_cap: Capability | None = None,
    include_finish: bool = True,
) -> CapabilityRegistry:
    """Build a CapabilityRegistry with the computation workflow capabilities."""
    config = load_config()
    registry = CapabilityRegistry(config.capabilities)

    if inspect_cap is not None:
        registry.register(inspect_cap)
    if generate_code_cap is not None:
        registry.register(generate_code_cap)
    if run_code_cap is not None:
        registry.register(run_code_cap)

    if include_finish:
        # Minimal finish capability for workflow completion
        class FinishCapability(Capability):
            @property
            def metadata(self) -> CapabilityMetadata:
                return CapabilityMetadata(
                    name="finish",
                    kind=CapabilityKind.CONTROL,
                    description="Mark workflow as ready to finish.",
                )

            def execute(self, request: CapabilityRequest) -> CapabilityResult:
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[
                        Observation(
                            source="finish",
                            kind="workflow_finish",
                            summary="Workflow marked as finished.",
                        )
                    ],
                )

        registry.register(FinishCapability())

    return registry


# ────────────────────────────────────────────────────────────────────────────
# 1. Prompt construction
# ────────────────────────────────────────────────────────────────────────────

class TestPromptConstruction:
    """Verify build_code_generation_prompt produces correct prompts."""

    def test_prompt_includes_user_goal(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert prompt.computation_description == ctx.user_goal

    def test_prompt_includes_file_path(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert prompt.file_path == ctx.file_path
        assert ctx.file_path in prompt.constraints[0]

    def test_prompt_data_schema_contains_columns(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        for col in ctx.columns:
            assert col in prompt.data_schema

    def test_prompt_data_schema_contains_numeric_fields(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        for field in ctx.numeric_fields:
            assert field in prompt.data_schema

    def test_prompt_data_schema_contains_row_count(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert str(ctx.row_count) in prompt.data_schema

    def test_prompt_data_schema_contains_sample_values(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert "EQ-001" in prompt.data_schema
        assert "4.5" in prompt.data_schema

    def test_prompt_data_schema_contains_sheet_names(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert "Readings" in prompt.data_schema

    def test_prompt_has_safety_constraints(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        constraint_text = " ".join(prompt.constraints)
        assert "openpyxl" in constraint_text
        assert "stdout" in constraint_text
        assert "network" in constraint_text.lower()

    def test_prompt_no_correction_context_on_first_attempt(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        assert prompt.correction_context is None

    def test_prompt_includes_correction_context_on_retry(self):
        ctx = _sample_context(
            previous_code="import openpyxl\nprint(bad_var)",
            previous_error="NameError: name 'bad_var' is not defined",
            retry_attempt=1,
        )
        prompt = build_code_generation_prompt(ctx)
        assert prompt.correction_context is not None
        assert "bad_var" in prompt.correction_context
        assert "NameError" in prompt.correction_context
        assert "attempt 1" in prompt.correction_context


# ────────────────────────────────────────────────────────────────────────────
# 2. Input preparation
# ────────────────────────────────────────────────────────────────────────────

class TestInputPreparation:
    """Verify prepare_generate_code_inputs and prepare_run_code_inputs."""

    def test_generate_code_inputs_structure(self):
        ctx = _sample_context()
        prompt = build_code_generation_prompt(ctx)
        inputs = prepare_generate_code_inputs(prompt)

        assert inputs["computation_description"] == prompt.computation_description
        assert inputs["data_schema"] == prompt.data_schema
        assert inputs["file_path"] == prompt.file_path
        assert inputs["constraints"] == prompt.constraints
        assert "correction_context" not in inputs

    def test_generate_code_inputs_with_correction(self):
        ctx = _sample_context(
            previous_code="broken code",
            previous_error="SyntaxError",
            retry_attempt=1,
        )
        prompt = build_code_generation_prompt(ctx)
        inputs = prepare_generate_code_inputs(prompt)
        assert "correction_context" in inputs

    def test_run_code_inputs_structure(self):
        inputs = prepare_run_code_inputs("print('hi')", "/data/file.xlsx")
        assert inputs["code"] == "print('hi')"
        assert inputs["file_path"] == "/data/file.xlsx"


# ────────────────────────────────────────────────────────────────────────────
# 3. Execution observation parsing
# ────────────────────────────────────────────────────────────────────────────

class TestExecutionObservationParsing:
    """Verify parse_execution_observation extracts structured outcomes."""

    def test_successful_execution(self):
        result = CapabilityResult(
            request_id=uuid4(),
            status=CapabilityResultStatus.SUCCEEDED,
            output={"stdout": "Result: 42", "stderr": "", "exit_code": 0},
        )
        outcome = parse_execution_observation(result)
        assert outcome.succeeded is True
        assert outcome.stdout == "Result: 42"
        assert outcome.stderr == ""
        assert outcome.exit_code == 0
        assert outcome.error_summary is None

    def test_failed_execution(self):
        result = CapabilityResult(
            request_id=uuid4(),
            status=CapabilityResultStatus.FAILED,
            output={"stdout": "", "stderr": "NameError: x", "exit_code": 1},
            error="Code execution failed: exit code 1.",
        )
        outcome = parse_execution_observation(result)
        assert outcome.succeeded is False
        assert outcome.exit_code == 1
        assert outcome.error_summary is not None
        assert "NameError" in outcome.error_summary

    def test_missing_output_fields(self):
        result = CapabilityResult(
            request_id=uuid4(),
            status=CapabilityResultStatus.FAILED,
            output={},
            error="Sandbox crashed.",
        )
        outcome = parse_execution_observation(result)
        assert outcome.succeeded is False
        assert outcome.stdout == ""
        assert outcome.stderr == ""
        assert outcome.exit_code is None

    def test_non_zero_exit_code(self):
        result = CapabilityResult(
            request_id=uuid4(),
            status=CapabilityResultStatus.FAILED,
            output={"stdout": "", "stderr": "Traceback...", "exit_code": 2},
            error="exit code 2",
        )
        outcome = parse_execution_observation(result)
        assert outcome.exit_code == 2
        assert not outcome.succeeded


# ────────────────────────────────────────────────────────────────────────────
# 4. Retry context
# ────────────────────────────────────────────────────────────────────────────

class TestRetryContext:
    """Verify build_retry_context preserves original data and appends error info."""

    def test_retry_preserves_original_context(self):
        ctx = _sample_context()
        outcome = ExecutionOutcome(
            succeeded=False,
            stderr="KeyError: 'bad_col'",
            exit_code=1,
            error_summary="KeyError: 'bad_col'",
        )
        retry_ctx = build_retry_context(ctx, outcome, "print(bad_col)")

        # Original fields preserved
        assert retry_ctx.user_goal == ctx.user_goal
        assert retry_ctx.file_path == ctx.file_path
        assert retry_ctx.columns == ctx.columns
        assert retry_ctx.numeric_fields == ctx.numeric_fields
        assert retry_ctx.row_count == ctx.row_count

    def test_retry_appends_error_info(self):
        ctx = _sample_context()
        outcome = ExecutionOutcome(
            succeeded=False,
            stderr="KeyError: 'bad_col'",
            exit_code=1,
            error_summary="KeyError: 'bad_col'",
        )
        retry_ctx = build_retry_context(ctx, outcome, "print(bad_col)")

        assert retry_ctx.previous_code == "print(bad_col)"
        assert retry_ctx.previous_error == "KeyError: 'bad_col'"
        assert retry_ctx.retry_attempt == 1

    def test_retry_increments_attempt_counter(self):
        ctx = _sample_context(retry_attempt=1)
        outcome = ExecutionOutcome(
            succeeded=False,
            stderr="error",
            exit_code=1,
            error_summary="error",
        )
        retry_ctx = build_retry_context(ctx, outcome, "code")
        assert retry_ctx.retry_attempt == 2


# ────────────────────────────────────────────────────────────────────────────
# 5. GenerateCodeCapability
# ────────────────────────────────────────────────────────────────────────────

class TestGenerateCodeCapability:
    """Verify GenerateCodeCapability routes through ModelRouter → MockModelProvider."""

    def test_generates_code_via_model(self):
        router, providers, mock_provider = _mock_router_and_providers(
            code_response="import openpyxl\nprint('result')"
        )
        cap = GenerateCodeCapability(router=router, providers=providers)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={
                "computation_description": "Calculate averages",
                "data_schema": "Columns: A, B",
                "file_path": "/data/test.xlsx",
            },
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert "code" in result.output
        assert "openpyxl" in result.output["code"]
        assert len(result.observations) == 1
        assert result.observations[0].kind == "code_generated"

    def test_extracts_code_from_markdown_fences(self):
        fenced_code = "```python\nimport openpyxl\nprint('hello')\n```"
        router, providers, _ = _mock_router_and_providers(code_response=fenced_code)
        cap = GenerateCodeCapability(router=router, providers=providers)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={
                "computation_description": "Test",
                "data_schema": "cols",
                "file_path": "/data/t.xlsx",
            },
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.SUCCEEDED
        code = result.output["code"]
        assert "```" not in code
        assert "import openpyxl" in code

    def test_fails_without_description(self):
        router, providers, _ = _mock_router_and_providers()
        cap = GenerateCodeCapability(router=router, providers=providers)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={"data_schema": "cols", "file_path": "/data/t.xlsx"},
        )
        result = cap.invoke(request)
        assert result.status == CapabilityResultStatus.FAILED
        assert "computation_description" in result.error

    def test_fails_with_missing_provider(self):
        router, _, _ = _mock_router_and_providers()
        cap = GenerateCodeCapability(router=router, providers={})

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={
                "computation_description": "Test",
                "data_schema": "cols",
                "file_path": "/data/t.xlsx",
            },
        )
        result = cap.invoke(request)
        assert result.status == CapabilityResultStatus.FAILED
        assert "provider" in result.error.lower()

    def test_rejects_wrong_capability_name(self):
        router, providers, _ = _mock_router_and_providers()
        cap = GenerateCodeCapability(router=router, providers=providers)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"computation_description": "X", "data_schema": "Y", "file_path": "Z"},
        )
        result = cap.invoke(request)
        assert result.status == CapabilityResultStatus.REJECTED

    def test_observation_records_model_id(self):
        router, providers, _ = _mock_router_and_providers(code_response="print(1)")
        cap = GenerateCodeCapability(router=router, providers=providers)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={
                "computation_description": "Sum",
                "data_schema": "A",
                "file_path": "/f.xlsx",
            },
        )
        result = cap.invoke(request)
        assert result.status == CapabilityResultStatus.SUCCEEDED
        obs = result.observations[0]
        assert "model_id" in obs.data
        assert obs.data["model_id"] == result.output["model_id"]


# ────────────────────────────────────────────────────────────────────────────
# 6. RunCodeCapability
# ────────────────────────────────────────────────────────────────────────────

class TestRunCodeCapability:
    """Verify RunCodeCapability delegates to SandboxRunner correctly."""

    def test_successful_execution(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="42", stderr="", exit_code=0)
        )
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print(42)", "file_path": "/data/test.xlsx"},
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert result.output["stdout"] == "42"
        assert result.output["exit_code"] == 0
        assert sandbox.last_code == "print(42)"
        assert sandbox.last_data_file_path == "/data/test.xlsx"

    def test_failed_execution(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(
                stdout="", stderr="NameError: x", exit_code=1
            )
        )
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print(x)"},
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.FAILED
        assert "NameError" in result.error
        assert result.output["exit_code"] == 1

    def test_timeout_execution(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="", stderr="", exit_code=0, timed_out=True)
        )
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "while True: pass"},
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.FAILED
        assert "timed out" in result.error

    def test_missing_code_input(self):
        sandbox = MockSandboxRunner()
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={},
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.FAILED
        assert "code" in result.error.lower()
        assert sandbox.call_count == 0

    def test_sandbox_exception_handled(self):
        class CrashingSandbox(SandboxRunner):
            def run(self, code, data_file_path=None):
                raise RuntimeError("Docker not available")

        cap = RunCodeCapability(sandbox=CrashingSandbox())

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print(1)"},
        )
        result = cap.invoke(request)

        assert result.status == CapabilityResultStatus.FAILED
        assert "Docker not available" in result.error

    def test_observation_produced_on_success(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="ok", exit_code=0)
        )
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print('ok')"},
        )
        result = cap.invoke(request)

        assert len(result.observations) == 1
        obs = result.observations[0]
        assert obs.source == "run_code"
        assert obs.kind == "code_execution"
        assert "successfully" in obs.summary

    def test_observation_produced_on_failure(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="", stderr="err", exit_code=1)
        )
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "bad"},
        )
        result = cap.invoke(request)

        assert len(result.observations) == 1
        obs = result.observations[0]
        assert "failed" in obs.summary

    def test_mock_sandbox_call_count(self):
        sandbox = MockSandboxRunner()
        cap = RunCodeCapability(sandbox=sandbox)

        for i in range(3):
            request = CapabilityRequest(
                capability_name="run_code",
                inputs={"code": f"print({i})"},
            )
            cap.invoke(request)

        assert sandbox.call_count == 3

    def test_rejects_wrong_capability_name(self):
        sandbox = MockSandboxRunner()
        cap = RunCodeCapability(sandbox=sandbox)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={"code": "print(1)"},
        )
        result = cap.invoke(request)
        assert result.status == CapabilityResultStatus.REJECTED


# ────────────────────────────────────────────────────────────────────────────
# 7. Capability registration and Broker resolution
# ────────────────────────────────────────────────────────────────────────────

class TestCapabilityRegistration:
    """Verify both capabilities register and resolve through the Broker."""

    def test_generate_code_registers_in_registry(self):
        router, providers, _ = _mock_router_and_providers()
        cap = GenerateCodeCapability(router=router, providers=providers)

        config = load_config()
        registry = CapabilityRegistry(config.capabilities)
        registry.register(cap)

        assert registry.lookup("generate_code") is cap

    def test_run_code_registers_in_registry(self):
        sandbox = MockSandboxRunner()
        cap = RunCodeCapability(sandbox=sandbox)

        config = load_config()
        registry = CapabilityRegistry(config.capabilities)
        registry.register(cap)

        assert registry.lookup("run_code") is cap

    def test_broker_resolves_generate_code(self):
        router, providers, _ = _mock_router_and_providers(code_response="print(1)")
        gen_cap = GenerateCodeCapability(router=router, providers=providers)

        registry = _mock_capability_registry(generate_code_cap=gen_cap)
        broker = RegistryCapabilityBroker(registry)

        request = CapabilityRequest(
            capability_name="generate_code",
            inputs={
                "computation_description": "Sum column A",
                "data_schema": "A: numeric",
                "file_path": "/data/t.xlsx",
            },
        )
        result = broker.invoke(request)
        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert "code" in result.output

    def test_broker_resolves_run_code(self):
        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="result", exit_code=0)
        )
        run_cap = RunCodeCapability(sandbox=sandbox)

        registry = _mock_capability_registry(run_code_cap=run_cap)
        broker = RegistryCapabilityBroker(registry)

        request = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print('result')"},
        )
        result = broker.invoke(request)
        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert result.output["stdout"] == "result"


# ────────────────────────────────────────────────────────────────────────────
# 8. Controller integration
# ────────────────────────────────────────────────────────────────────────────

class TestControllerIntegration:
    """Verify the computation workflow steps through ExecutionController."""

    def _build_controller(self, code_response="print(1)", sandbox_result=None):
        """Build an ExecutionController with mock generate_code + run_code."""
        router, providers, _ = _mock_router_and_providers(code_response=code_response)
        gen_cap = GenerateCodeCapability(router=router, providers=providers)

        if sandbox_result is None:
            sandbox_result = SandboxResult(stdout="result", exit_code=0)
        sandbox = MockSandboxRunner(default_result=sandbox_result)
        run_cap = RunCodeCapability(sandbox=sandbox)

        # Mock inspect capability for the inspect step
        class MockInspectCapability(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="inspect_spreadsheet",
                    kind=CapabilityKind.TOOL,
                    description="Mock spreadsheet inspection.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    output={"sheet_names": ["Sheet1"], "columns": ["A", "B"]},
                    observations=[
                        Observation(
                            source="inspect_spreadsheet",
                            kind="spreadsheet_inspection",
                            summary="Inspected workbook.",
                        )
                    ],
                )

        # Mock verify_result capability
        class MockVerifyCapability(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="verify_result",
                    kind=CapabilityKind.TOOL,
                    description="Mock verification.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[
                        Observation(
                            source="verify_result",
                            kind="verification",
                            summary="Result verified.",
                        )
                    ],
                )

        # Mock generate_excel capability
        class MockGenerateExcelCapability(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="generate_excel",
                    kind=CapabilityKind.TOOL,
                    description="Mock excel generation.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[
                        Observation(
                            source="generate_excel",
                            kind="artifact_generated",
                            summary="Excel generated.",
                        )
                    ],
                )

        registry = _mock_capability_registry(
            generate_code_cap=gen_cap,
            run_code_cap=run_cap,
            inspect_cap=MockInspectCapability(),
        )
        registry.register(MockVerifyCapability())
        registry.register(MockGenerateExcelCapability())

        broker = RegistryCapabilityBroker(registry)

        state = TaskState(user_goal="Test computation")
        controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

        return controller, sandbox

    def test_inspect_then_generate_then_run(self):
        """Prove the first three steps of the computation workflow."""
        controller, sandbox = self._build_controller()

        # Step 1: inspect_spreadsheet
        event = controller.execute(
            AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/data/test.xlsx"})
        )
        assert event.kind == ExecutionEventKind.ACTION_COMPLETED
        assert controller.state.current_step == "generate"

        # Step 2: generate_code
        event = controller.execute(
            AgentDecision(
                action="generate_code",
                inputs={
                    "computation_description": "Sum col A",
                    "data_schema": "A: numeric",
                    "file_path": "/data/test.xlsx",
                },
            )
        )
        assert event.kind == ExecutionEventKind.ACTION_COMPLETED
        assert controller.state.current_step == "run"

        # Step 3: run_code
        event = controller.execute(
            AgentDecision(
                action="run_code",
                inputs={"code": "print(1)", "file_path": "/data/test.xlsx"},
            )
        )
        assert event.kind == ExecutionEventKind.ACTION_COMPLETED
        assert controller.state.current_step == "verify"

    def test_full_computation_workflow(self):
        """Prove the complete computation workflow through the Controller."""
        controller, _ = self._build_controller()

        steps = [
            AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/f.xlsx"}),
            AgentDecision(
                action="generate_code",
                inputs={
                    "computation_description": "Average",
                    "data_schema": "A",
                    "file_path": "/f.xlsx",
                },
            ),
            AgentDecision(action="run_code", inputs={"code": "print(1)", "file_path": "/f.xlsx"}),
            AgentDecision(action="verify_result", inputs={}),
            AgentDecision(action="generate_excel", inputs={}),
            AgentDecision(action="finish", done=True),
        ]

        for decision in steps:
            controller.execute(decision)

        assert controller.state.final_status.value == "completed"
        assert "inspect_spreadsheet" in controller.state.completed_steps
        assert "generate_code" in controller.state.completed_steps
        assert "run_code" in controller.state.completed_steps

    def test_observations_recorded_in_task_state(self):
        """Verify capability observations flow into controller-owned TaskState."""
        controller, _ = self._build_controller()

        controller.execute(
            AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/f.xlsx"})
        )
        controller.execute(
            AgentDecision(
                action="generate_code",
                inputs={
                    "computation_description": "Test",
                    "data_schema": "A",
                    "file_path": "/f.xlsx",
                },
            )
        )

        # Observations from both capabilities should be recorded
        sources = {obs.source for obs in controller.state.observations}
        assert "inspect_spreadsheet" in sources
        assert "generate_code" in sources


# ────────────────────────────────────────────────────────────────────────────
# 9. Error recovery path
# ────────────────────────────────────────────────────────────────────────────

class TestErrorRecoveryPath:
    """Verify sandbox failure → retry → success using the skill's retry context."""

    def test_sandbox_failure_triggers_retry_to_generate(self):
        """Prove the Controller sends run_code failure back to generate state."""
        router, providers, _ = _mock_router_and_providers(code_response="print(1)")
        gen_cap = GenerateCodeCapability(router=router, providers=providers)

        sandbox = MockSandboxRunner(
            default_result=SandboxResult(stdout="", stderr="KeyError: 'x'", exit_code=1)
        )
        run_cap = RunCodeCapability(sandbox=sandbox)

        class MockInspect(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="inspect_spreadsheet",
                    kind=CapabilityKind.TOOL,
                    description="Mock.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[
                        Observation(source="inspect_spreadsheet", kind="inspect", summary="ok")
                    ],
                )

        registry = _mock_capability_registry(
            generate_code_cap=gen_cap,
            run_code_cap=run_cap,
            inspect_cap=MockInspect(),
        )
        broker = RegistryCapabilityBroker(registry)
        state = TaskState(user_goal="Test computation")
        controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

        # inspect
        controller.execute(
            AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/f.xlsx"})
        )
        # generate
        controller.execute(
            AgentDecision(
                action="generate_code",
                inputs={
                    "computation_description": "T",
                    "data_schema": "A",
                    "file_path": "/f.xlsx",
                },
            )
        )
        # run → fails
        event = controller.execute(
            AgentDecision(action="run_code", inputs={"code": "bad", "file_path": "/f.xlsx"})
        )

        assert event.kind == ExecutionEventKind.ACTION_FAILED
        # Workflow should transition back to "generate" for correction
        assert controller.state.current_step == "generate"
        assert controller.state.retry_count == 1

    def test_skill_retry_context_feeds_corrected_generation(self):
        """Prove the skill's retry context produces a correction prompt."""
        ctx = _sample_context()
        failed_code = "import openpyxl\nprint(undefined_var)"
        outcome = ExecutionOutcome(
            succeeded=False,
            stderr="NameError: name 'undefined_var' is not defined",
            exit_code=1,
            error_summary="NameError: name 'undefined_var' is not defined",
        )

        retry_ctx = build_retry_context(ctx, outcome, failed_code)
        prompt = build_code_generation_prompt(retry_ctx)

        assert prompt.correction_context is not None
        assert "undefined_var" in prompt.correction_context
        assert "NameError" in prompt.correction_context
        assert prompt.computation_description == ctx.user_goal

    def test_full_failure_then_success_flow(self):
        """Prove ACT → OBSERVE ERROR → REASON → CORRECT → ACT → SUCCESS."""
        call_count = 0

        def sandbox_factory(code, data_file_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SandboxResult(stdout="", stderr="error", exit_code=1)
            return SandboxResult(stdout="42", exit_code=0)

        sandbox = MockSandboxRunner(result_factory=sandbox_factory)
        run_cap = RunCodeCapability(sandbox=sandbox)

        router, providers, _ = _mock_router_and_providers(code_response="print(42)")
        gen_cap = GenerateCodeCapability(router=router, providers=providers)

        class MockInspect(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="inspect_spreadsheet",
                    kind=CapabilityKind.TOOL,
                    description="Mock.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[
                        Observation(source="inspect_spreadsheet", kind="inspect", summary="ok")
                    ],
                )

        class MockVerify(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="verify_result",
                    kind=CapabilityKind.TOOL,
                    description="Mock.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[Observation(source="verify_result", kind="verify", summary="ok")],
                )

        class MockExcel(Capability):
            @property
            def metadata(self):
                return CapabilityMetadata(
                    name="generate_excel",
                    kind=CapabilityKind.TOOL,
                    description="Mock.",
                    input_modalities=("spreadsheet",),
                )

            def execute(self, request):
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    observations=[Observation(source="generate_excel", kind="artifact", summary="ok")],
                )

        registry = _mock_capability_registry(
            generate_code_cap=gen_cap,
            run_code_cap=run_cap,
            inspect_cap=MockInspect(),
        )
        registry.register(MockVerify())
        registry.register(MockExcel())

        broker = RegistryCapabilityBroker(registry)
        state = TaskState(user_goal="Compute average", max_iterations=12)
        controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

        # inspect
        controller.execute(AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/f.xlsx"}))
        # generate (first attempt)
        controller.execute(
            AgentDecision(
                action="generate_code",
                inputs={"computation_description": "Avg", "data_schema": "A", "file_path": "/f.xlsx"},
            )
        )
        # run → fails
        event = controller.execute(
            AgentDecision(action="run_code", inputs={"code": "bad", "file_path": "/f.xlsx"})
        )
        assert event.kind == ExecutionEventKind.ACTION_FAILED
        assert controller.state.current_step == "generate"

        # generate (corrected)
        controller.execute(
            AgentDecision(
                action="generate_code",
                inputs={"computation_description": "Avg fixed", "data_schema": "A", "file_path": "/f.xlsx"},
            )
        )
        # run → succeeds
        event = controller.execute(
            AgentDecision(action="run_code", inputs={"code": "print(42)", "file_path": "/f.xlsx"})
        )
        assert event.kind == ExecutionEventKind.ACTION_COMPLETED
        assert controller.state.current_step == "verify"

        # Complete remaining steps
        controller.execute(AgentDecision(action="verify_result", inputs={}))
        controller.execute(AgentDecision(action="generate_excel", inputs={}))
        controller.execute(AgentDecision(action="finish", done=True))

        assert controller.state.final_status.value == "completed"
        assert controller.state.retry_count == 1


# ────────────────────────────────────────────────────────────────────────────
# 10. Model and schema validation
# ────────────────────────────────────────────────────────────────────────────

class TestModelValidation:
    """Verify Pydantic model constraints on skill data types."""

    def test_computation_context_requires_user_goal(self):
        with pytest.raises(ValidationError):
            ComputationContext(user_goal="", file_path="/f.xlsx")

    def test_computation_context_requires_file_path(self):
        with pytest.raises(ValidationError):
            ComputationContext(user_goal="test", file_path="")

    def test_execution_outcome_error_summary_must_be_nonempty(self):
        with pytest.raises(ValidationError):
            ExecutionOutcome(succeeded=False, error_summary="")

    def test_code_generation_prompt_requires_description(self):
        with pytest.raises(ValidationError):
            CodeGenerationPrompt(
                computation_description="",
                data_schema="A",
                file_path="/f.xlsx",
                constraints=["c"],
            )

    def test_code_generation_prompt_requires_constraints(self):
        with pytest.raises(ValidationError):
            CodeGenerationPrompt(
                computation_description="X",
                data_schema="A",
                file_path="/f.xlsx",
                constraints=[],
            )
