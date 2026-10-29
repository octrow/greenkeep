"""Types every stage of a run shares: options, the baseline, the job, a change."""

from __future__ import annotations

from dataclasses import dataclass

from .digest import Digest
from .findings import Finding, ruff_findings
from .state import State
from .workspace import TestRun, Workspace, ruff_json

# pinned models, not combos: a combo answers with a different model from run to run.
# Benchmark 2026-10-15: opus = sonnet > grok-4.7 as patcher; sonnet writes, opus reviews.
# Both run on Claude Code (claude -p): the reviewer reads the repo with tools.
# Called directly, not through OmniRoute: the account Claude Code is logged in to pays, so a
# bare id is that model (through OmniRoute a bare `claude-sonnet-*` went to a free combo, and
# `claude/...` to whichever account OmniRoute holds, not the owner's chosen one).
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_REVIEW_MODEL = "claude-opus-5-5"


class GateFailure(Exception):
    """The attempt's change did not pass the gate."""


@dataclass(frozen=True)
class Options:
    push: bool
    budget: int
    model: str
    keep: bool
    review_model: str = DEFAULT_REVIEW_MODEL
    planner: str = ""  # a boost: this model plans each hard change before the patcher writes it


@dataclass(frozen=True)
class Change:
    paths: list[str]
    model: str  # who produced it: an LLM id, or the tool for deterministic changes
    prompt_sha: str = "-"
    reviewer: str = ""
    deterministic: bool = False  # a tool, not a model, wrote it: no LLM review


@dataclass(frozen=True)
class Baseline:
    """The repo before any attempt: what the gate compares every change against."""
    tests: TestRun
    lint: frozenset[str]  # fingerprints of ruff findings already there
    findings: list[Finding]


@dataclass(frozen=True)
class Job:
    """Everything one repo's run shares: the clone, its baseline, the options, the history,
    and the digest it reports into."""
    ws: Workspace
    base: Baseline
    opts: Options
    state: State
    digest: Digest


def lint(ws: Workspace, *paths: str) -> list[Finding]:
    return ruff_findings(ruff_json(ws, *paths), root=str(ws.root), read_line=ws.read_line)
