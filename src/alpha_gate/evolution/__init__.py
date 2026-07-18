"""Pluggable candidate-proposal backends."""

from alpha_gate.evolution.base import (
    Evolver,
    ParentCandidate,
    Proposal,
    ProposalRequest,
)
from alpha_gate.evolution.local import LocalEvolver

__all__ = [
    "Evolver",
    "LocalEvolver",
    "ParentCandidate",
    "Proposal",
    "ProposalRequest",
]
