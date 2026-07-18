"""Backend-neutral batch proposal contract for local and cloud evolvers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from alpha_gate.candidate import CandidateProgram


@dataclass(frozen=True)
class ParentCandidate:
    program: CandidateProgram
    reward: float
    passed: bool
    validity: float


@dataclass(frozen=True)
class ProposalRequest:
    generation: int
    batch_size: int
    seed: int
    seed_programs: tuple[CandidateProgram, ...]
    parents: tuple[ParentCandidate, ...] = ()
    seen_program_sha256: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Proposal:
    program: CandidateProgram
    parent_sha256: tuple[str, ...]
    origin: str
    mutation: str


class Evolver(ABC):
    """Propose one finite batch without evaluating candidates itself."""

    @abstractmethod
    async def propose(self, request: ProposalRequest) -> tuple[Proposal, ...]:
        """Return exactly request.batch_size proposals in deterministic order."""
