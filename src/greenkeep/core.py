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
# Called directly by default: the account Claude Code is logged in to pays, so a bare id is
# that model. Outside the config's `login_hours` it goes through OmniRoute as `claude/<id>`,
# pinned to its `claude` connection (a bare `claude-sonnet-*` there went to a free combo).
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
AGENT_MODEL = "claude-opus-5-5"  # escalation, at medium effort (owner, 2026-11-05)
MAX_ESCALATIONS = 2  # agent runs per greenkeep run: up to 30 minutes each
# what the agent's own pytest, ruff and `uv run` leave behind, ignored by the repo or not;
# `git clean` never touches a tracked file, so a repo's own uv.lock stays
CACHES = (
    ":(glob)**/__pycache__/**",
    ":(glob)**/.pytest_cache/**",
    ":(glob)**/.ruff_cache/**",
    "uv.lock",
)
TRAILER = "Co-Authored-By: greenkeep <greenkeep@users.noreply.github.com>"


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
