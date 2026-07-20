"""Docker/Podman implementation of the lockstep sandbox protocol."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from alpha_gate.candidate import CandidateSourceError, CandidateValidator
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from alpha_gate.executors.process import JsonlProcessDriver


class ContainerExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    runtime: Literal["docker", "podman"] = "docker"
    runtime_path: str | None = None
    image: str = Field(default="alpha-gate-sandbox:dev", min_length=1)
    worker_path: str = "/opt/alpha-gate/worker.py"


class ContainerExecutor(SandboxExecutor):
    """Run one candidate in a networkless, read-only Linux container."""

    def __init__(self, config: ContainerExecutorConfig | None = None) -> None:
        self.config = config or ContainerExecutorConfig()
        self._process_driver = JsonlProcessDriver()

    def runtime_path(self) -> str | None:
        if self.config.runtime_path is not None:
            return self.config.runtime_path
        return shutil.which(self.config.runtime)

    def build_command(
        self,
        runtime_path: str,
        candidate_path: Path,
        limits: ExecutionLimits,
        container_name: str,
    ) -> tuple[str, ...]:
        mount = (
            f"type=bind,source={candidate_path.resolve()},"
            "target=/candidate/strategy.py,readonly"
        )
        return (
            runtime_path,
            "run",
            "--rm",
            "--name",
            container_name,
            "--interactive",
            "--pull=never",
            "--network=none",
            "--read-only",
            f"--pids-limit={limits.pids}",
            f"--memory={limits.memory_mb}m",
            f"--cpus={limits.cpu_cores}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65532:65532",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={limits.tmpfs_mb}m",
            "--log-driver=none",
            "--env=PYTHONHASHSEED=0",
            "--mount",
            mount,
            self.config.image,
            "python",
            "-s",
            "-B",
            self.config.worker_path,
            "/candidate/strategy.py",
        )

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        try:
            CandidateValidator.validate(request.program)
        except CandidateSourceError as exc:
            return SandboxResult(
                status=SandboxStatus.INVALID,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=str(exc),
            )

        runtime_path = self.runtime_path()
        if runtime_path is None:
            return SandboxResult(
                status=SandboxStatus.UNAVAILABLE,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=f"{self.config.runtime} executable was not found",
            )

        with tempfile.TemporaryDirectory(prefix="alpha-gate-") as temporary:
            candidate_path = Path(temporary) / request.program.filename
            container_name = f"alpha-gate-{uuid.uuid4().hex}"
            await asyncio.to_thread(
                candidate_path.write_text,
                request.program.source,
                encoding="utf-8",
            )
            command = self.build_command(
                runtime_path,
                candidate_path,
                request.limits,
                container_name,
            )
            try:
                return await self._process_driver.execute(
                    command,
                    request,
                    started=started,
                    unavailable_label="container runtime",
                )
            finally:
                cleanup = asyncio.create_task(
                    self._delete_container(runtime_path, container_name)
                )
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise

    @staticmethod
    async def _delete_container(runtime_path: str, container_name: str) -> None:
        """Force-remove the named container after client timeout or cancellation."""

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                runtime_path,
                "rm",
                "--force",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (OSError, TimeoutError):
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            logging.exception("failed to clean up container %s", container_name)
