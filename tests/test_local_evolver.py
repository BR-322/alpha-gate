from __future__ import annotations

from pathlib import Path

import pytest

from alpha_gate.candidate import CandidateProgram, CandidateValidator
from alpha_gate.evolution import LocalEvolver, ParentCandidate, ProposalRequest

SEED_PATH = Path(__file__).parents[1] / "examples" / "seed_strategy.py"


def _seed() -> CandidateProgram:
    return CandidateProgram(source=SEED_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_local_evolver_is_deterministic_unique_and_block_scoped() -> None:
    seed = _seed()
    request = ProposalRequest(
        generation=0,
        batch_size=6,
        seed=41,
        seed_programs=(seed,),
    )

    first = await LocalEvolver().propose(request)
    second = await LocalEvolver().propose(request)

    assert first == second
    assert len(first) == 6
    assert first[0].program == seed
    assert first[0].origin == "seed"
    assert len({proposal.program.sha256 for proposal in first}) == 6
    prefix = seed.source.partition(LocalEvolver.START_MARKER)[0]
    suffix = seed.source.partition(LocalEvolver.END_MARKER)[2]
    for proposal in first:
        CandidateValidator.validate(proposal.program)
        assert proposal.program.source.partition(LocalEvolver.START_MARKER)[0] == prefix
        assert proposal.program.source.partition(LocalEvolver.END_MARKER)[2] == suffix


@pytest.mark.asyncio
async def test_later_generation_uses_parents_and_avoids_seen_sources() -> None:
    seed = _seed()
    evolver = LocalEvolver()
    first = await evolver.propose(
        ProposalRequest(
            generation=0,
            batch_size=4,
            seed=7,
            seed_programs=(seed,),
        )
    )
    parent = ParentCandidate(
        program=first[1].program,
        reward=0.5,
        passed=False,
        validity=1.0,
    )
    seen = frozenset(proposal.program.sha256 for proposal in first)

    second = await evolver.propose(
        ProposalRequest(
            generation=1,
            batch_size=3,
            seed=8,
            seed_programs=(seed,),
            parents=(parent,),
            seen_program_sha256=seen,
        )
    )

    assert len(second) == 3
    assert all(
        proposal.parent_sha256 == (parent.program.sha256,) for proposal in second
    )
    assert not seen.intersection(proposal.program.sha256 for proposal in second)


@pytest.mark.asyncio
async def test_local_evolver_requires_evolve_markers() -> None:
    source = """
class Strategy:
    def on_bar(self, bar):
        return [0.0, 0.0]
"""

    with pytest.raises(ValueError, match="one ordered EVOLVE-BLOCK"):
        await LocalEvolver().propose(
            ProposalRequest(
                generation=0,
                batch_size=1,
                seed=1,
                seed_programs=(CandidateProgram(source=source),),
            )
        )


@pytest.mark.asyncio
async def test_local_evolver_rejects_empty_seeds_and_invalid_batch() -> None:
    evolver = LocalEvolver()
    with pytest.raises(ValueError, match="batch_size must be positive"):
        await evolver.propose(
            ProposalRequest(
                generation=0,
                batch_size=0,
                seed=1,
                seed_programs=(_seed(),),
            )
        )
    with pytest.raises(ValueError, match="seed_programs must not be empty"):
        await evolver.propose(
            ProposalRequest(
                generation=0,
                batch_size=1,
                seed=1,
                seed_programs=(),
            )
        )
