"""Cloud Run nested-sandbox implementation used by the trusted broker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

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

_CLEANUP_ATTEMPTS = 3
_CLEANUP_TIMEOUT_SECONDS = 5.0


class CloudRunSandboxConfig(BaseModel):
    """Fixed broker envelope for one nested Cloud Run sandbox."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    launcher_path: str = "/usr/local/gcp/bin/sandbox"
    python_path: str = "/usr/local/bin/python"
    worker_path: str = "/opt/alpha-gate/worker.py"
    cpu_ceiling: float = Field(default=1.0, gt=0.0, le=4.0)
    memory_ceiling_mb: int = Field(default=512, ge=64, le=4096)


class CloudRunSandboxExecutor(SandboxExecutor):
    """Launch a networkless, metadata-less nested sandbox inside Cloud Run."""

    def __init__(self, config: CloudRunSandboxConfig | None = None) -> None:
        self.config = config or CloudRunSandboxConfig()
        self._process_driver = JsonlProcessDriver()

    def launcher_path(self) -> str | None:
        configured = self.config.launcher_path
        if os.path.sep in configured:
            return configured if os.access(configured, os.X_OK) else None
        return shutil.which(configured)

    def build_command(
        self,
        launcher_path: str,
        candidate_path: Path,
        _limits: ExecutionLimits,
        sandbox_name: str,
    ) -> tuple[str, ...]:
        """Build a launcher command without enabling egress or writable mounts."""

        return (
            launcher_path,
            "do",
            "--sandbox-name",
            sandbox_name,
            "--env",
            "PYTHONHASHSEED=0",
            "--",
            self.config.python_path,
            "-s",
            "-B",
            self.config.worker_path,
            str(candidate_path),
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

        envelope_error = self._envelope_error(request.limits)
        if envelope_error:
            return SandboxResult(
                status=SandboxStatus.UNAVAILABLE,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=envelope_error,
            )

        launcher_path = self.launcher_path()
        if launcher_path is None:
            return SandboxResult(
                status=SandboxStatus.UNAVAILABLE,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error="Cloud Run sandbox launcher was not found",
            )

        with tempfile.TemporaryDirectory(prefix="alpha-gate-") as temporary:
            Path(temporary).chmod(0o755)
            candidate_path = Path(temporary) / request.program.filename
            sandbox_name = f"alpha-gate-{uuid.uuid4().hex}"
            await asyncio.to_thread(
                candidate_path.write_text,
                request.program.source,
                encoding="utf-8",
            )
            candidate_path.chmod(0o444)
            command = self.build_command(
                launcher_path,
                candidate_path,
                request.limits,
                sandbox_name,
            )
            try:
                return await self._process_driver.execute(
                    command,
                    request,
                    started=started,
                    unavailable_label="Cloud Run sandbox launcher",
                )
            finally:
                cleanup = asyncio.create_task(
                    self._delete_sandbox(
                        launcher_path,
                        sandbox_name,
                    )
                )
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise

    def _envelope_error(self, limits: ExecutionLimits) -> str:
        if limits.cpu_cores > self.config.cpu_ceiling:
            return "requested CPU exceeds the Cloud Run broker ceiling"
        if limits.memory_mb > self.config.memory_ceiling_mb:
            return "requested memory exceeds the Cloud Run broker ceiling"
        return ""

    @staticmethod
    async def _delete_sandbox(launcher_path: str, sandbox_name: str) -> None:
        """Delete a named sandbox with bounded retries after every execution path."""

        for attempt in range(_CLEANUP_ATTEMPTS):
            process: asyncio.subprocess.Process | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    launcher_path,
                    "delete",
                    sandbox_name,
                    "--force",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                returncode = await asyncio.wait_for(
                    process.wait(),
                    timeout=_CLEANUP_TIMEOUT_SECONDS,
                )
                if returncode == 0:
                    return
            except (OSError, TimeoutError):
                if process is not None and process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            if attempt + 1 < _CLEANUP_ATTEMPTS:
                await asyncio.sleep(0.1 * (2**attempt))

        logging.error(
            "failed to clean up Cloud Run sandbox %s after %d attempts",
            sandbox_name,
            _CLEANUP_ATTEMPTS,
        )
