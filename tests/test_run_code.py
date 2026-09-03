"""Comprehensive tests for run_code and DockerSandboxRunner.

Verifies:
1. Docker command construction & security invariants:
   - Container execution via container runtime (--network none).
   - Network access is disabled; network_enabled=True is rejected.
   - Host filesystem access is restricted strictly to the required workspace.
   - Resource limits (--memory, --cpus, --pids-limit) and security flags (--security-opt no-new-privileges).
2. Capturing execution streams:
   - stdout is captured.
   - stderr is captured.
   - exit status is captured.
3. Timeout enforcement:
   - Subprocess timeout is enforced.
   - Container is forcefully killed and removed upon timeout.
   - timed_out flag is set to True.
4. Host execution prevention:
   - Generated code is NEVER executed directly on the host.
5. Graceful Docker service error handling:
   - Missing Docker runtime executable (docker_not_found).
   - Stopped / unreachable Docker daemon (docker_daemon_unavailable).
   - Never falls back to host execution on infrastructure failures.
   - Error types are captured for audit logging.
6. Capability & Broker integration:
   - RunCodeCapability defaults to DockerSandboxRunner.
   - Capability contract and Observation schema.
   - Standalone run_code() function.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aegis.config import load_config
from aegis.capabilities import (
    CapabilityKind,
    CapabilityRegistry,
    DockerSandboxRunner,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
    SandboxRunner,
    run_code,
)
from aegis.schemas import CapabilityRequest, CapabilityResultStatus


class TestDockerCommandConstructionAndSecurity:
    """Verify Docker command construction and isolation invariants."""

    def test_network_none_always_present(self):
        runner = DockerSandboxRunner()
        cmd = runner.build_command("test-container", Path("/tmp/fake_ws"))
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_network_enabled_true_raises_error(self):
        with pytest.raises(ValueError, match="Sandboxed execution must keep networking disabled"):
            DockerSandboxRunner(network_enabled=True)

    def test_rm_flag_present_for_automatic_cleanup(self):
        runner = DockerSandboxRunner()
        cmd = runner.build_command("test-container", Path("/tmp/fake_ws"))
        assert "--rm" in cmd

    def test_security_opt_no_new_privileges_present(self):
        runner = DockerSandboxRunner()
        cmd = runner.build_command("test-container", Path("/tmp/fake_ws"))
        assert "--security-opt" in cmd
        idx = cmd.index("--security-opt")
        assert cmd[idx + 1] == "no-new-privileges"

    def test_resource_limits_configured(self):
        runner = DockerSandboxRunner(
            memory_limit="256m",
            cpu_limit="0.5",
            pids_limit=50,
        )
        cmd = runner.build_command("test-container", Path("/tmp/fake_ws"))
        assert "--memory" in cmd
        assert cmd[cmd.index("--memory") + 1] == "256m"
        assert "--cpus" in cmd
        assert cmd[cmd.index("--cpus") + 1] == "0.5"
        assert "--pids-limit" in cmd
        assert cmd[cmd.index("--pids-limit") + 1] == "50"

    def test_filesystem_mount_restricted_to_workspace(self, tmp_path):
        runner = DockerSandboxRunner()
        cmd = runner.build_command("test-container", tmp_path)

        # Ensure -v flag mounts strictly tmp_path to /workspace
        assert "-v" in cmd
        mount_arg = cmd[cmd.index("-v") + 1]
        normalized_ws = str(tmp_path.resolve()).replace("\\", "/")
        assert mount_arg == f"{normalized_ws}:/workspace:rw"

        # Check working directory is /workspace
        assert "--workdir" in cmd
        assert cmd[cmd.index("--workdir") + 1] == "/workspace"

        # Ensure no other -v mounts exist
        mount_indices = [i for i, arg in enumerate(cmd) if arg == "-v"]
        assert len(mount_indices) == 1

    def test_read_only_workspace_mount(self, tmp_path):
        runner = DockerSandboxRunner(read_only_workspace=True)
        cmd = runner.build_command("test-container", tmp_path)
        normalized_ws = str(tmp_path.resolve()).replace("\\", "/")
        mount_arg = cmd[cmd.index("-v") + 1]
        assert mount_arg == f"{normalized_ws}:/workspace:ro"

    def test_container_executes_script_with_unbuffered_python(self, tmp_path):
        runner = DockerSandboxRunner(image="python:3.11-slim")
        cmd = runner.build_command("test-container", tmp_path)
        assert "python:3.11-slim" in cmd
        img_idx = cmd.index("python:3.11-slim")
        assert cmd[img_idx:] == [
            "python:3.11-slim",
            "python",
            "-u",
            "/workspace/_execution_script.py",
        ]


class TestExecutionAndStreamCapture:
    """Verify stdout, stderr, and exit status capturing."""

    @patch("subprocess.run")
    def test_successful_execution_captures_stdout(self, mock_run, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Calculation result: 42.5\n"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner(workspace_dir=tmp_path)
        result = runner.run("print('Calculation result: 42.5')")

        assert result.exit_code == 0
        assert result.stdout == "Calculation result: 42.5\n"
        assert result.stderr == ""
        assert result.timed_out is False
        assert result.error_type is None

        # Verify code was written to workspace
        script_file = tmp_path / "_execution_script.py"
        assert script_file.exists()
        assert script_file.read_text(encoding="utf-8") == "print('Calculation result: 42.5')"

    @patch("subprocess.run")
    def test_failed_execution_captures_stderr_and_exit_code(self, mock_run, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "ZeroDivisionError: division by zero\n"
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner(workspace_dir=tmp_path)
        result = runner.run("x = 1 / 0")

        assert result.exit_code == 1
        assert "ZeroDivisionError" in result.stderr
        assert result.timed_out is False
        assert result.error_type == "execution_failed"

    @patch("subprocess.run")
    def test_data_file_is_copied_into_isolated_workspace(self, mock_run, tmp_path):
        # Create external data file
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        data_file = ext_dir / "readings.xlsx"
        data_file.write_bytes(b"dummy-excel-data")

        # Isolated workspace
        ws_dir = tmp_path / "sandbox_ws"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "read data"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner(workspace_dir=ws_dir)
        result = runner.run("import openpyxl", data_file_path=str(data_file))

        assert result.exit_code == 0
        # Verify the file was copied into the sandbox workspace
        copied_file = ws_dir / "readings.xlsx"
        assert copied_file.exists()
        assert copied_file.read_bytes() == b"dummy-excel-data"


class TestTimeoutEnforcement:
    """Verify execution timeout enforcement and container killing."""

    @patch("subprocess.run")
    def test_timeout_kills_container_and_returns_timed_out_result(self, mock_run):
        # First call is the docker run which times out
        # Second call is the _kill_container docker rm -f
        kill_mock = MagicMock()
        kill_mock.returncode = 0

        def side_effect(cmd, *args, **kwargs):
            if "rm" in cmd:
                return kill_mock
            raise subprocess.TimeoutExpired(
                cmd=cmd,
                timeout=5.0,
                output="partial stdout",
                stderr="partial stderr",
            )

        mock_run.side_effect = side_effect

        runner = DockerSandboxRunner(timeout_seconds=5.0)
        result = runner.run("while True: pass")

        assert result.timed_out is True
        assert result.exit_code == -1
        assert result.error_type == "timeout"
        assert "Execution timed out after 5.0 seconds" in result.stderr
        assert result.stdout == "partial stdout"

        # Verify docker rm -f was called to kill the container
        rm_calls = [call for call in mock_run.call_args_list if "rm" in call[0][0]]
        assert len(rm_calls) == 1
        assert "-f" in rm_calls[0][0][0]


class TestGracefulDockerServiceErrorHandling:
    """Verify graceful handling when Docker service/daemon cannot run."""

    @patch("subprocess.run")
    def test_docker_daemon_not_running_error_categorized(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = (
            "docker: Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?."
        )
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner()
        result = runner.run("print(1)")

        assert result.exit_code == 1
        assert result.error_type == "docker_daemon_unavailable"
        assert "Docker service unavailable" in result.stderr
        assert result.timed_out is False

    @patch("subprocess.run")
    def test_docker_windows_pipe_error_categorized(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = (
            "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine: "
            "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
        )
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner()
        result = runner.run("print(1)")

        assert result.exit_code == 1
        assert result.error_type == "docker_daemon_unavailable"
        assert "Docker service unavailable" in result.stderr

    @patch("subprocess.run")
    def test_docker_runtime_not_found_error_categorized(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'docker'")

        runner = DockerSandboxRunner(container_runtime="docker")
        result = runner.run("print(1)")

        assert result.exit_code == 127
        assert result.error_type == "docker_not_found"
        assert "Container runtime executable 'docker' not found on PATH" in result.stderr

    def test_never_executes_code_on_host_on_failure(self):
        """Invariant: Even if Docker is dead, code is NEVER executed on host."""
        dangerous_code = "import os; os.environ['AEGIS_HOST_COMPROMISED'] = '1'"

        with patch("subprocess.run", side_effect=FileNotFoundError):
            runner = DockerSandboxRunner()
            runner.run(dangerous_code)

        import os

        assert "AEGIS_HOST_COMPROMISED" not in os.environ

    @patch("subprocess.run")
    def test_is_available_method(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        runner = DockerSandboxRunner()
        assert runner.is_available() is True

        mock_proc.returncode = 1
        assert runner.is_available() is False

        mock_run.side_effect = FileNotFoundError
        assert runner.is_available() is False


class TestRunCodeCapabilityAndBrokerIntegration:
    """Verify RunCodeCapability integration with DockerSandboxRunner."""

    def test_run_code_capability_defaults_to_docker_sandbox_runner(self):
        cap = RunCodeCapability()
        assert isinstance(cap._sandbox, DockerSandboxRunner)

    def test_run_code_capability_accepts_custom_mock_runner(self):
        mock_sandbox = MockSandboxRunner(default_result=SandboxResult(stdout="mocked", exit_code=0))
        cap = RunCodeCapability(sandbox=mock_sandbox)
        assert cap._sandbox is mock_sandbox

        req = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print('test')"},
        )
        res = cap.invoke(req)
        assert res.status == CapabilityResultStatus.SUCCEEDED
        assert res.output["stdout"] == "mocked"
        assert mock_sandbox.call_count == 1

    @patch("subprocess.run")
    def test_run_code_capability_surfaces_daemon_failure_in_observation(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error during connect: Cannot connect to the Docker daemon"
        mock_run.return_value = mock_proc

        cap = RunCodeCapability()
        req = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "print(1)"},
        )
        res = cap.invoke(req)

        assert res.status == CapabilityResultStatus.FAILED
        assert res.output["error_type"] == "docker_daemon_unavailable"
        assert len(res.observations) == 1
        obs = res.observations[0]
        assert obs.source == "run_code"
        assert obs.data["error_type"] == "docker_daemon_unavailable"
        assert "Docker service unavailable" in obs.summary

    def test_run_code_capability_rejects_empty_code(self):
        cap = RunCodeCapability()
        req = CapabilityRequest(
            capability_name="run_code",
            inputs={"code": "   "},
        )
        res = cap.invoke(req)
        assert res.status == CapabilityResultStatus.FAILED
        assert "Missing or empty required input: code" in (res.error or "")

    def test_run_code_capability_registers_and_resolves_in_registry(self):
        config = load_config()
        registry = CapabilityRegistry(config.capabilities)
        cap = RunCodeCapability()
        registry.register(cap)
        assert registry.lookup("run_code") is cap

    def test_standalone_run_code_function_delegates_to_runner(self):
        mock_runner = MockSandboxRunner(default_result=SandboxResult(stdout="standalone", exit_code=0))
        res = run_code("print(1)", runner=mock_runner)
        assert res.stdout == "standalone"
        assert mock_runner.call_count == 1
