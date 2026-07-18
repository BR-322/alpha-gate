"""Deterministic numeric source mutator used as the cloud-free baseline."""

from __future__ import annotations

import ast
import io
import math
import random
import tokenize

from alpha_gate.candidate import (
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.evolution.base import Evolver, Proposal, ProposalRequest


class LocalEvolver(Evolver):
    """Mutate numeric literals inside AlphaEvolve-compatible evolve blocks."""

    START_MARKER = "# EVOLVE-BLOCK-START"
    END_MARKER = "# EVOLVE-BLOCK-END"
    MAX_ATTEMPTS_PER_PROPOSAL = 200

    async def propose(self, request: ProposalRequest) -> tuple[Proposal, ...]:
        if request.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not request.seed_programs:
            raise ValueError("seed_programs must not be empty")

        rng = random.Random(request.seed)
        seen = set(request.seen_program_sha256)
        proposals: list[Proposal] = []
        if request.generation == 0:
            for program in request.seed_programs:
                if len(proposals) >= request.batch_size:
                    break
                if program.sha256 in seen:
                    continue
                CandidateValidator.validate(program)
                proposals.append(
                    Proposal(
                        program=program,
                        parent_sha256=(),
                        origin="seed",
                        mutation="unmodified seed",
                    )
                )
                seen.add(program.sha256)

        parent_programs = (
            tuple(parent.program for parent in request.parents) or request.seed_programs
        )
        for parent in parent_programs:
            self._evolve_block(parent.source)
        attempts = 0
        attempt_limit = request.batch_size * self.MAX_ATTEMPTS_PER_PROPOSAL
        while len(proposals) < request.batch_size and attempts < attempt_limit:
            attempts += 1
            parent = parent_programs[rng.randrange(len(parent_programs))]
            try:
                program, mutation = self._mutate(parent, rng)
                CandidateValidator.validate(program)
            except (CandidateSourceError, ValueError, SyntaxError, tokenize.TokenError):
                continue
            if program.sha256 in seen:
                continue
            proposals.append(
                Proposal(
                    program=program,
                    parent_sha256=(parent.sha256,),
                    origin="local_numeric_mutation",
                    mutation=mutation,
                )
            )
            seen.add(program.sha256)

        if len(proposals) != request.batch_size:
            raise ValueError(
                "local evolver exhausted unique numeric mutations before filling batch"
            )
        return tuple(proposals)

    def _mutate(
        self,
        parent: CandidateProgram,
        rng: random.Random,
    ) -> tuple[CandidateProgram, str]:
        start_line, end_line = self._evolve_block(parent.source)
        tokens = list(tokenize.generate_tokens(io.StringIO(parent.source).readline))
        mutable: list[int] = []
        for index, token in enumerate(tokens):
            if token.type != tokenize.NUMBER:
                continue
            if not start_line < token.start[0] < end_line:
                continue
            value = ast.literal_eval(token.string)
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            if math.isfinite(float(value)):
                mutable.append(index)
        if not mutable:
            raise ValueError("evolve block contains no mutable numeric literals")

        token_index = mutable[rng.randrange(len(mutable))]
        token = tokens[token_index]
        old_value = ast.literal_eval(token.string)
        replacements = self._replacements(old_value)
        new_value = replacements[rng.randrange(len(replacements))]
        new_literal = self._literal(new_value, isinstance(old_value, int))
        tokens[token_index] = tokenize.TokenInfo(
            type=token.type,
            string=new_literal,
            start=token.start,
            end=token.end,
            line=token.line,
        )
        source = tokenize.untokenize(tokens)
        return (
            CandidateProgram(
                source=source,
                filename=parent.filename,
                entrypoint=parent.entrypoint,
            ),
            f"line {token.start[0]} numeric literal {token.string} -> {new_literal}",
        )

    @classmethod
    def _evolve_block(cls, source: str) -> tuple[int, int]:
        lines = source.splitlines()
        starts = [
            index + 1
            for index, line in enumerate(lines)
            if line.strip() == cls.START_MARKER
        ]
        ends = [
            index + 1
            for index, line in enumerate(lines)
            if line.strip() == cls.END_MARKER
        ]
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("source must contain one ordered EVOLVE-BLOCK")
        return starts[0], ends[0]

    @staticmethod
    def _replacements(value: int | float) -> tuple[int | float, ...]:
        if isinstance(value, int):
            values: set[int | float] = {
                max(0, value - 1),
                value + 1,
                max(0, value // 2),
                value * 2,
            }
        else:
            step = 0.05 if abs(value) < 1.0 else 1.0
            values = {
                value * 0.5,
                value * 0.8,
                value * 1.25,
                value * 2.0,
                value - step,
                value + step,
            }
        values.discard(value)
        finite = sorted(candidate for candidate in values if math.isfinite(candidate))
        if not finite:
            raise ValueError("numeric literal has no finite mutation")
        return tuple(finite)

    @staticmethod
    def _literal(value: int | float, was_integer: bool) -> str:
        if was_integer:
            return str(int(value))
        return repr(float(value))
