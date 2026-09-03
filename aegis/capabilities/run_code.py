"""RunCode capability: delegate code execution to a sandbox interface.

This capability never executes code directly. It delegates to a SandboxRunner
protocol, which will be backed by Docker (--network none) in Phase 6.4.
For testing, a MockSandboxRunner is provided.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
)


@dataclass(frozen=True)
class SandboxResult:
    """Structured output from sandbox code execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False


class SandboxRunner(ABC):
    """Abstract sandbox execution interface.

    Implementations must execute the provided Python code in isolation
    without network access and return captured stdout/stderr/exit status.
    """

    @abstractmethod
    def run(self, code: str, data_file_path: str | None = None) -> SandboxResult:
        """Execute code in a sandbox and return the captured result."""


class MockSandboxRunner(SandboxRunner):
    """Test-only sandbox runner that returns configurable results.

    The default produces a successful execution with empty output.
    Pass a custom ``result_factory`` for scenario-specific test responses.
    """

    def __init__(
        self,
        default_result: SandboxResult | None = None,
        result_factory=None,
    ) -> None:
        self._default_result = default_result or SandboxResult(
            stdout="mock execution output", exit_code=0
        )
        self._result_factory = result_factory
        self.last_code: str | None = None
        self.last_data_file_path: str | None = None
        self.call_count: int = 0

    def run(self, code: str, data_file_path: str | None = None) -> SandboxResult:
        """Return the configured mock result and record the invocation."""

        self.last_code = code
        self.last_data_file_path = data_file_path
        self.call_count += 1

        if self._result_factory is not None:
            return self._result_factory(code, data_file_path)
        return self._default_result


class RunCodeCapability(Capability):
    """Execute generated code through a sandbox interface, never directly."""

    def __init__(self, sandbox: SandboxRunner) -> None:
        self._sandbox = sandbox
        self._metadata = CapabilityMetadata(
            name="run_code",
            kind=CapabilityKind.TOOL,
            description="Execute generated code in a sandbox with networking disabled.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute in the sandbox.",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the data file available inside the sandbox.",
                        },
                    },
                    "required": ["code"],
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "stdout": {"type": "string"},
                        "stderr": {"type": "string"},
                        "exit_code": {"type": "integer"},
                        "timed_out": {"type": "boolean"},
                    },
                    "required": ["stdout", "stderr", "exit_code"],
                }
            ),
            input_modalities=("spreadsheet",),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        """Delegate code execution to the sandbox runner."""

        inputs = request.inputs
        code = inputs.get("code")

        if not isinstance(code, str) or not code.strip():
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Missing or empty required input: code.",
            )

        file_path = inputs.get("file_path")
        if isinstance(file_path, str):
            file_path = file_path.strip() or None
        else:
            file_path = None

        try:
            sandbox_result = self._sandbox.run(code, data_file_path=file_path)
        except Exception as exc:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Sandbox execution raised {type(exc).__name__}: {exc}",
            )

        output = {
            "stdout": sandbox_result.stdout,
            "stderr": sandbox_result.stderr,
            "exit_code": sandbox_result.exit_code,
            "timed_out": sandbox_result.timed_out,
        }

        succeeded = sandbox_result.exit_code == 0 and not sandbox_result.timed_out

        if succeeded:
            obs_summary = (
                f"Code executed successfully (exit code 0). "
                f"stdout: {len(sandbox_result.stdout)} chars."
            )
        else:
            reasons: list[str] = []
            if sandbox_result.timed_out:
                reasons.append("execution timed out")
            if sandbox_result.exit_code != 0:
                reasons.append(f"exit code {sandbox_result.exit_code}")
            obs_summary = f"Code execution failed: {', '.join(reasons)}."

        observation = Observation(
            source="run_code",
            kind="code_execution",
            summary=obs_summary,
            data=output,
            request_id=request.request_id,
        )

        if succeeded:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.SUCCEEDED,
                output=output,
                observations=[observation],
            )

        error_msg = obs_summary
        if sandbox_result.stderr:
            error_msg = f"{error_msg} stderr: {sandbox_result.stderr}"

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.FAILED,
            error=error_msg,
            output=output,
            observations=[observation],
        )
