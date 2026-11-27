"""Docker-based ephemeral sandbox for safely running untrusted PR tests or linters."""

import asyncio
from pathlib import Path

from pydantic import BaseModel

_MAX_OUTPUT_CHARS = 8000


class SandboxResult(BaseModel):
    """Bounded result of running a command inside an isolated sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxRunner:
    """Runs repository commands in a network-disabled, secret-free ephemeral container."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._image = image
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._timeout = timeout_seconds

    def build_docker_command(self, workspace_root: Path, command: list[str]) -> list[str]:
        """Construct a hardened docker run command with zero host secrets or network."""
        resolved = str(workspace_root.resolve())
        return [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            f"--memory={self._memory_limit}",
            f"--cpus={self._cpu_limit}",
            "--pids-limit=128",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=128m",
            "-v",
            f"{resolved}:/workspace:ro",
            "-w",
            "/workspace",
            self._image,
            *command,
        ]

    async def run(self, workspace_root: Path, command: list[str]) -> SandboxResult:
        """Execute a command inside the hardened container with a strict timeout."""
        docker_cmd = self.build_docker_command(workspace_root, command)
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
            return SandboxResult(
                exit_code=proc.returncode or 0,
                stdout=stdout_b.decode("utf-8", errors="replace")[:_MAX_OUTPUT_CHARS],
                stderr=stderr_b.decode("utf-8", errors="replace")[:_MAX_OUTPUT_CHARS],
                timed_out=False,
            )
        except TimeoutError:
            return SandboxResult(
                exit_code=124,
                stdout="",
                stderr=f"Sandbox execution timed out after {self._timeout}s.",
                timed_out=True,
            )
        except FileNotFoundError:
            return SandboxResult(
                exit_code=127,
                stdout="",
                stderr="Docker binary not found; sandbox execution unavailable.",
                timed_out=False,
            )

