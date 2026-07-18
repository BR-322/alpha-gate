"""Append-only audit ledgers for candidate evaluation events."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrialRecord(BaseModel):
    """Exactly one durable audit event for one proposed program."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    experiment_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=256)
    evaluation_index: int = Field(ge=0)
    generation: int = Field(ge=0)
    program_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    program_source: str = Field(min_length=1, max_length=131_072)
    parent_sha256: tuple[str, ...]
    origin: str = Field(min_length=1, max_length=128)
    mutation: str = Field(min_length=1, max_length=512)
    reward: float
    passed: bool
    validity: float = Field(ge=0.0, le=1.0)
    trial_count_at_evaluation: int = Field(ge=1)
    source_bytes: int = Field(ge=0)
    ast_nodes: int = Field(ge=0)
    metrics: dict[str, float]
    error: str = ""
    executor_window_statuses: tuple[str, ...]
    executor_window_durations_seconds: tuple[float, ...]
    executor_total_duration_seconds: float = Field(ge=0.0)
    executor_stdout_bytes: int = Field(ge=0)
    executor_stderr_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def finite_metrics_and_valid_parents(self) -> TrialRecord:
        numeric = (
            self.reward,
            self.validity,
            self.executor_total_duration_seconds,
            *self.executor_window_durations_seconds,
            *self.metrics.values(),
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("trial ledger metrics must be finite")
        if any(value < 0.0 for value in self.executor_window_durations_seconds):
            raise ValueError("executor window durations must be non-negative")
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.parent_sha256
        ):
            raise ValueError("parent_sha256 values must be lowercase SHA-256 digests")
        observed_sha256 = hashlib.sha256(
            self.program_source.encode("utf-8")
        ).hexdigest()
        if observed_sha256 != self.program_sha256:
            raise ValueError("program_sha256 does not match program_source")
        if len(self.executor_window_statuses) != len(
            self.executor_window_durations_seconds
        ):
            raise ValueError("executor window statuses and durations must align")
        if not math.isclose(
            self.executor_total_duration_seconds,
            math.fsum(self.executor_window_durations_seconds),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("executor total duration does not match window durations")
        if self.metrics.get("reward", self.reward) != self.reward:
            raise ValueError("record reward does not match metrics")
        if self.metrics.get(
            "trial_count", float(self.trial_count_at_evaluation)
        ) != float(self.trial_count_at_evaluation):
            raise ValueError("record trial count does not match metrics")
        return self


class TrialLedger(Protocol):
    def append(self, record: TrialRecord) -> None: ...


class MemoryLedger:
    """In-memory ledger with the same duplicate protection as JSONL."""

    def __init__(self) -> None:
        self.records: list[TrialRecord] = []
        self._keys: set[tuple[str, int]] = set()

    def append(self, record: TrialRecord) -> None:
        key = (record.experiment_id, record.evaluation_index)
        if key in self._keys:
            raise ValueError("ledger already contains this evaluation index")
        self._keys.add(key)
        self.records.append(record)


class JsonlLedger:
    """Durable JSONL ledger that never truncates an existing run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._keys: set[tuple[str, int]] = set()
        if self.path.exists():
            self._load_existing()

    def has_experiment(self, experiment_id: str) -> bool:
        return any(
            key_experiment == experiment_id for key_experiment, _index in self._keys
        )

    def _load_existing(self) -> None:
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = TrialRecord.model_validate_json(line)
                except ValueError as exc:
                    raise ValueError(
                        f"invalid trial ledger record at line {line_number}"
                    ) from exc
                key = (record.experiment_id, record.evaluation_index)
                if key in self._keys:
                    raise ValueError(
                        "trial ledger contains a duplicate evaluation index"
                    )
                self._keys.add(key)

    def append(self, record: TrialRecord) -> None:
        key = (record.experiment_id, record.evaluation_index)
        if key in self._keys:
            raise ValueError("ledger already contains this evaluation index")
        payload = record.model_dump_json() + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._keys.add(key)
