"""RunCode capability: delegate code execution to an isolated Docker sandbox.

This capability never executes code directly on the host. It delegates to
DockerSandboxRunner (or MockSandboxRunner for testing) which enforces:
- Container execution via Docker CLI (--network none).
- Zero network access.
- Strict host filesystem restriction to the designated workspace.
- Captured stdout, stderr, and exit status.
- Timeout enforcement with automatic container cleanup.
- Graceful error handling and structured failure typing when Docker is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

_DOCKER_DAEMON_ERROR_PATTERNS = (
    "cannot connect to the docker daemon",
    "failed to connect to the docker api",
    "is the docker daemon running",
    "error during connect",
    "dockerdesktoplinuxengine",
    "the system cannot find the file specified",
    "connection refused",
    "pipe/docker",
)


def _is_docker_daemon_error(stderr: str) -> bool:
    """Check if stderr indicates Docker service / daemon connectivity failure."""
    if not stderr:
        return False
    lower = stderr.lower()
    return any(pattern in lower for pattern in _DOCKER_DAEMON_ERROR_PATTERNS)


@dataclass(frozen=True)
class SandboxResult:
    """Structured output from sandbox code execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    error_type: str | None = None


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


class DockerSandboxRunner(SandboxRunner):
    """Executes code in an isolated Docker container with zero network access.

    Guarantees:
    - Generated code executes inside the container, NEVER directly on the host.
    - Network access is disabled (--network none).
    - Captured stdout, stderr, and exit status.
    - Timeout enforcement with automatic container cleanup.
    - Host filesystem access strictly restricted to the required workspace.
    - Graceful error handling for Docker service unavailability with structured
      error categorization for audit logging.
    """

    DEFAULT_IMAGE: str = "python:3.11-slim"
    DEFAULT_TIMEOUT_SECONDS: float = 30.0
    DEFAULT_MEMORY_LIMIT: str = "512m"
    DEFAULT_CPU_LIMIT: str = "1.0"
    DEFAULT_PIDS_LIMIT: int = 100

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        container_runtime: str = "docker",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        cpu_limit: float | str | None = DEFAULT_CPU_LIMIT,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
        network_enabled: bool = False,
        read_only_workspace: bool = False,
        workspace_dir: str | Path | None = None,
    ) -> None:
        if network_enabled:
            raise ValueError("Sandboxed execution must keep networking disabled.")
        self.image = image
        self.container_runtime = container_runtime
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.network_enabled = network_enabled
        self.read_only_workspace = read_only_workspace
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir is not None else None

    def is_available(self) -> bool:
        """Check whether the container runtime and daemon are active and reachable."""
        try:
            proc = subprocess.run(
                [self.container_runtime, "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def build_command(self, container_name: str, workspace_path: Path) -> list[str]:
        """Construct the docker run command line enforcing all isolation constraints."""
        normalized_workspace = str(workspace_path.resolve()).replace("\\", "/")
        mount_mode = "ro" if self.read_only_workspace else "rw"
        mount_spec = f"{normalized_workspace}:/workspace:{mount_mode}"

        cmd = [
            self.container_runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
        ]

        if self.memory_limit:
            cmd.extend(["--memory", self.memory_limit])
        if self.cpu_limit is not None:
            cmd.extend(["--cpus", str(self.cpu_limit)])
        if self.pids_limit is not None:
            cmd.extend(["--pids-limit", str(self.pids_limit)])

        cmd.extend([
            "--security-opt",
            "no-new-privileges",
            "-v",
            mount_spec,
            "--workdir",
            "/workspace",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "PYTHONUNBUFFERED=1",
            self.image,
            "python",
            "-u",
            "/workspace/_execution_script.py",
        ])
        return cmd

    def run(self, code: str, data_file_path: str | None = None) -> SandboxResult:
        """Execute code in Docker container and return captured results.

        Never executes generated code on the host. If container execution fails
        due to missing daemon, timeout, or runtime error, structured error
        information is returned for audit logging.
        """
        if not isinstance(code, str) or not code.strip():
            return SandboxResult(
                stdout="",
                stderr="Missing or empty code to execute.",
                exit_code=1,
                timed_out=False,
                error_type="invalid_input",
            )

        container_name = f"aegis-sandbox-{uuid.uuid4().hex[:12]}"

        if self.workspace_dir is not None:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            return self._execute_in_workspace(code, data_file_path, self.workspace_dir, container_name)
        else:
            with tempfile.TemporaryDirectory(prefix="aegis_sandbox_") as tmp_dir:
                workspace_path = Path(tmp_dir).resolve()
                return self._execute_in_workspace(code, data_file_path, workspace_path, container_name)

    def _execute_in_workspace(
        self,
        code: str,
        data_file_path: str | None,
        workspace_path: Path,
        container_name: str,
    ) -> SandboxResult:
        # Write generated code into the workspace
        script_path = workspace_path / "_execution_script.py"
        script_path.write_text(code, encoding="utf-8")

        # Copy data file into workspace if provided and exists
        if data_file_path:
            src_file = Path(data_file_path).resolve()
            if src_file.exists() and src_file.is_file():
                try:
                    src_file.relative_to(workspace_path)
                except ValueError:
                    dest_file = workspace_path / src_file.name
                    if not dest_file.exists():
                        shutil.copy2(src_file, dest_file)

        cmd = self.build_command(container_name, workspace_path)

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            self._kill_container(container_name)
            raw_stdout = exc.stdout or ""
            raw_stderr = exc.stderr or ""
            stdout = raw_stdout if isinstance(raw_stdout, str) else raw_stdout.decode("utf-8", "replace")
            stderr = raw_stderr if isinstance(raw_stderr, str) else raw_stderr.decode("utf-8", "replace")
            timeout_msg = f"Execution timed out after {self.timeout_seconds} seconds."
            full_stderr = f"{stderr}\n{timeout_msg}".strip() if stderr else timeout_msg
            return SandboxResult(
                stdout=stdout,
                stderr=full_stderr,
                exit_code=-1,
                timed_out=True,
                error_type="timeout",
            )
        except FileNotFoundError:
            err = f"Container runtime executable '{self.container_runtime}' not found on PATH."
            return SandboxResult(
                stdout="",
                stderr=err,
                exit_code=127,
                timed_out=False,
                error_type="docker_not_found",
            )
        except Exception as exc:
            err = f"Sandbox execution error: {type(exc).__name__}: {exc}"
            return SandboxResult(
                stdout="",
                stderr=err,
                exit_code=1,
                timed_out=False,
                error_type="execution_exception",
            )

        # Process finished — check for Docker daemon / service connection failure
        if proc.returncode != 0 and _is_docker_daemon_error(proc.stderr):
            return SandboxResult(
                stdout=proc.stdout,
                stderr=f"Docker service unavailable: {proc.stderr.strip()}",
                exit_code=proc.returncode,
                timed_out=False,
                error_type="docker_daemon_unavailable",
            )

        error_type = None
        if proc.returncode != 0:
            error_type = "execution_failed"

        return SandboxResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            timed_out=False,
            error_type=error_type,
        )

    def _kill_container(self, container_name: str) -> None:
        """Forcefully remove a timed-out container."""
        try:
            subprocess.run(
                [self.container_runtime, "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
            )
        except Exception:
            pass


def run_code(
    code: str,
    data_file_path: str | None = None,
    *,
    runner: SandboxRunner | None = None,
    **runner_kwargs: Any,
) -> SandboxResult:
    """Execute Python code in an isolated sandbox and return the result.

    Generated code is strictly executed inside an isolated container and is NEVER
    executed directly on the host. If runner is not provided, DockerSandboxRunner is used.
    """
    if runner is None:
        runner = DockerSandboxRunner(**runner_kwargs)
    return runner.run(code, data_file_path=data_file_path)


class RunCodeCapability(Capability):
    """Execute generated code through a sandbox interface, never directly."""

    def __init__(self, sandbox: SandboxRunner | None = None) -> None:
        self._sandbox = sandbox if sandbox is not None else DockerSandboxRunner()
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
                        "error_type": {"type": ["string", "null"]},
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

        output: dict[str, Any] = {
            "stdout": sandbox_result.stdout,
            "stderr": sandbox_result.stderr,
            "exit_code": sandbox_result.exit_code,
            "timed_out": sandbox_result.timed_out,
        }
        if sandbox_result.error_type is not None:
            output["error_type"] = sandbox_result.error_type

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
            if sandbox_result.error_type == "docker_daemon_unavailable":
                reasons.append("Docker service unavailable")
            elif sandbox_result.error_type == "docker_not_found":
                reasons.append("Docker runtime not found")
            if sandbox_result.exit_code != 0 and not sandbox_result.timed_out:
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
