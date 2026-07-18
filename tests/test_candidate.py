from __future__ import annotations

import pytest

from alpha_gate.candidate import (
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)

from .conftest import SAFE_SOURCE


def test_safe_candidate_has_reproducible_metadata() -> None:
    program = CandidateProgram(source=SAFE_SOURCE)

    first = CandidateValidator.validate(program)
    second = CandidateValidator.validate(program)

    assert first == second
    assert first.sha256 == program.sha256
    assert first.source_bytes == len(SAFE_SOURCE.encode())
    assert first.ast_nodes > 0
    assert first.imported_modules == ()


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        ("import os", "import of os is not allowed"),
        ("from pathlib import Path", "import of pathlib is not allowed"),
        ("open('/etc/passwd')", "call to open is not allowed"),
        ("eval('1 + 1')", "call to eval is not allowed"),
        ("self.__class__", "dunder attribute access is not allowed"),
    ],
)
def test_rejects_host_interaction_primitives(fragment: str, message: str) -> None:
    source = f"""
class Strategy:
    def on_bar(self, bar):
        {fragment}
        return [0.0, 0.0]
"""

    with pytest.raises(CandidateSourceError, match=message):
        CandidateValidator.validate(CandidateProgram(source=source))


def test_rejects_relative_import() -> None:
    source = """
from .signals import signal

class Strategy:
    def on_bar(self, bar):
        return [signal(bar)]
"""

    with pytest.raises(CandidateSourceError, match="relative import is not allowed"):
        CandidateValidator.validate(CandidateProgram(source=source))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("def on_bar(bar):\n    return []", "exactly one Strategy class"),
        ("class Strategy:\n    pass", "Strategy must define on_bar"),
        ("class Strategy(:\n    pass", "invalid Python syntax"),
    ],
)
def test_rejects_invalid_entrypoint(source: str, message: str) -> None:
    with pytest.raises(CandidateSourceError, match=message):
        CandidateValidator.validate(CandidateProgram(source=source))
