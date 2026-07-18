"""Candidate source models and defense-in-depth static validation."""

from __future__ import annotations

import ast
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateProgram(BaseModel):
    """One immutable Python program proposed by an evolver."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: str = Field(min_length=1, max_length=131_072)
    filename: Literal["strategy.py"] = "strategy.py"
    entrypoint: Literal["Strategy"] = "Strategy"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()


class CandidateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sha256: str
    source_bytes: int = Field(ge=1)
    ast_nodes: int = Field(ge=1)
    imported_modules: tuple[str, ...]


class CandidateSourceError(ValueError):
    """A deterministic collection of candidate-source violations."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__("; ".join(findings))


class CandidateValidator(ast.NodeVisitor):
    """Fail early on unsupported APIs; container isolation remains mandatory."""

    ALLOWED_IMPORTS = frozenset(
        {
            "__future__",
            "collections",
            "dataclasses",
            "math",
            "numpy",
            "statistics",
            "typing",
        }
    )
    BLOCKED_CALLS = frozenset(
        {
            "__import__",
            "breakpoint",
            "compile",
            "delattr",
            "dir",
            "eval",
            "exec",
            "getattr",
            "globals",
            "input",
            "locals",
            "open",
            "setattr",
            "vars",
        }
    )
    MAX_AST_NODES = 12_000

    def __init__(self) -> None:
        self.findings: list[str] = []
        self.imported_modules: set[str] = set()

    @classmethod
    def validate(cls, program: CandidateProgram) -> CandidateMetadata:
        try:
            tree = ast.parse(program.source, filename=program.filename)
        except SyntaxError as exc:
            location = (
                f"line {exc.lineno}" if exc.lineno is not None else "unknown line"
            )
            raise CandidateSourceError(
                (f"invalid Python syntax at {location}",)
            ) from exc

        nodes = tuple(ast.walk(tree))
        validator = cls()
        if len(nodes) > cls.MAX_AST_NODES:
            validator.findings.append(
                f"AST has {len(nodes)} nodes; maximum is {cls.MAX_AST_NODES}"
            )
        validator.visit(tree)
        validator._check_entrypoint(tree, program.entrypoint)
        if validator.findings:
            raise CandidateSourceError(tuple(dict.fromkeys(validator.findings)))
        return CandidateMetadata(
            sha256=program.sha256,
            source_bytes=len(program.source.encode("utf-8")),
            ast_nodes=len(nodes),
            imported_modules=tuple(sorted(validator.imported_modules)),
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.findings.append(
                f"relative import is not allowed at line {node.lineno}"
            )
        self._record_import(node.module or "", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.BLOCKED_CALLS:
            self.findings.append(
                f"call to {node.func.id} is not allowed at line {node.lineno}"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            self.findings.append(
                f"dunder attribute access is not allowed at line {node.lineno}"
            )
        self.generic_visit(node)

    def _record_import(self, module: str, line: int) -> None:
        root = module.partition(".")[0]
        if not root or root not in self.ALLOWED_IMPORTS:
            self.findings.append(
                f"import of {module or '<unknown>'} is not allowed at line {line}"
            )
            return
        self.imported_modules.add(root)

    def _check_entrypoint(self, tree: ast.Module, entrypoint: str) -> None:
        classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == entrypoint
        ]
        if len(classes) != 1:
            self.findings.append(f"source must define exactly one {entrypoint} class")
            return
        methods = {
            node.name
            for node in classes[0].body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if "on_bar" not in methods:
            self.findings.append(f"{entrypoint} must define on_bar")
