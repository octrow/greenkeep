"""Repairs by the agent where there is no finding to fix (PLAN.md E5): a suite red on a
clean clone of the base branch, and a Dependabot PR whose suite is red here (a major upgrade
most often). The change must keep every test and make the full suite green, add no lint and
no suppression, and pass the review; nobody looks at it after that (ADR 0004)."""

from __future__ import annotations

import logging
import re

from claude_cli import ClaudeCliError

from . import github, llm
from .core import AGENT_MODEL, CACHES, MAX_ESCALATIONS, TRAILER, GateFailure, Options, lint
from .digest import Digest
from .findings import Finding, added_lint
from .gate import suppressions
from .state import State
from .workspace import TestRun, Workspace, WorkspaceError, run_tests, summary

log = logging.getLogger("greenkeep")

GATE_NOTE = (
    "the full test suite is green, every test that fails now passes and no test is "
    "deleted or skipped; no new lint findings; no tool suppressions; a reviewer approves."
)
FAILURES = (GateFailure, WorkspaceError, ClaudeCliError, OSError, UnicodeError)
OUTPUT_CHARS = 4000  # the end of the red run: the tracebacks and pytest's summary
COUNTS = re.compile(r"(\d+) (passed|failed|errors?)\b")


def counts(output: str) -> tuple[int, int]:
    """(passing, failing) from pytest's closing line; errors count as failing."""
    found = {kind: int(n) for n, kind in COUNTS.findall(summary(output))}
    failing = found.get("failed", 0) + found.get("error", 0) + found.get("errors", 0)
    return found.get("passed", 0), failing


def room(digest: Digest) -> bool:
    """Agent runs share one cap per greenkeep run with the escalations."""
    return len(digest.escalated) < MAX_ESCALATIONS


def _fix(ws: Workspace, f: Finding, before: TestRun, review_model: str) -> list[str]:
    """The agent changes the clone; the change is gated and reviewed. Its paths, or
    GateFailure."""
    lint_before = frozenset(f.fingerprint for f in lint(ws))
    reply = llm.agent(
        llm.agent_prompt(f, GATE_NOTE, [before.output[-OUTPUT_CHARS:]]), AGENT_MODEL, ws.root
    )
    ws.git("clean", "-fdxq", "--", *CACHES)
    paths = ws.changed()
    if not paths:
        said = (reply.text.strip().splitlines() or ["no reason given"])[-1]
        raise GateFailure(f"the agent changed nothing: {said}")
    if silenced := suppressions(ws.git("diff", "-U0", "HEAD")):
        raise GateFailure(f"the change silences tools instead of fixing code: {silenced}")
    py = [p for p in paths if p.endswith(".py") and (ws.root / p).is_file()]
    if new := added_lint(lint(ws, *py) if py else [], lint_before):
        raise GateFailure(
            "the change adds lint findings: "
            + "; ".join(f"{a.path}:{a.line} {a.rule} {a.message}" for a in new)
        )
    after = run_tests(ws)
    if not after.ok:
        raise GateFailure(f"test suite failed: {summary(after.output)}")
    passing, should = counts(after.output)[0], sum(counts(before.output))
    if passing < should:  # a deleted, skipped or renamed-away test
        raise GateFailure(f"{passing} tests pass, {should} should: {summary(after.output)}")
    verdict = llm.review(f, ws.git("diff", "HEAD"), review_model, ws.root)
    if not verdict.approve:
        raise GateFailure(f"reviewer ({verdict.model}) rejected the change: {verdict.reason}")
    return paths


def _tried(ws: Workspace, exc: Exception, what: str) -> None:
    if llm.unavailable(exc):
        raise exc
    log.info("%s: the agent did not fix %s: %s", ws.name, what, str(exc).splitlines()[0])


def heal(ws: Workspace, tests: TestRun, opts: Options, state: State, digest: Digest) -> bool:
    """One agent try per base-branch commit at a suite red on a clean clone; a crash is the
    machine's, not the code's. True once the fix is merged (the next run works the repo)."""
    head = ws.git("rev-parse", "HEAD").strip()
    key = f"heal:{ws.name}"
    if tests.crashed or state.mark(key) == head or not room(digest):
        return False
    digest.escalated.append(f"{ws.name}: red test suite")
    f = Finding(
        "red",
        "suite",
        ".",
        0,
        f"the full test suite fails on a clean clone of {ws.base_branch}; make it pass",
        f"red:{ws.name}",
        title="the test suite is green again",
    )
    try:
        paths = _fix(ws, f, tests, opts.review_model)
    except FAILURES as exc:
        _tried(ws, exc, "the red suite")
        state.set_mark(key, head)
        return False
    state.set_mark(key, head)
    branch = f"greenkeep/heal-{head[:10]}"
    ws.git("checkout", "-q", "-B", branch)
    ws.git("add", "--", *paths)
    body = (
        f"The suite was red on a clean clone of {ws.base_branch} at {head[:10]}: "
        f"{summary(tests.output)}.\nFixed by `{AGENT_MODEL}` (agent), reviewed by "
        f"`{opts.review_model}`.\nGate: {GATE_NOTE}"
    )
    ws.git("commit", "-q", "-m", "fix: make the test suite green again", "-m", body, "-m", TRAILER)
    pr = (
        github.publish(ws, branch, "fix: make the test suite green again", body) if opts.push else 0
    )
    digest.merged.append(f"{ws.name}: {f.title} (agent" + (f", PR #{pr})" if pr else ")"))
    return True


def adapt(ws: Workspace, opts: Options, digest: Digest, pr: dict, tests: TestRun) -> str:
    """At a Dependabot PR's head, red here: the agent adapts the code to the new version and
    its commit goes onto the PR's branch. The new head, or "" when it failed."""
    n = pr["number"]
    digest.escalated.append(f"{ws.name}: Dependabot #{n}")
    f = Finding(
        "dependabot",
        "upgrade",
        ".",
        0,
        f"Dependabot PR #{n} ({pr['title']}) turns the test suite red; adapt the code to the "
        "new version and keep the upgrade",
        f"dependabot:{n}",
        title=pr["title"],
    )
    body = f"Adapted by `{AGENT_MODEL}` (agent), reviewed by `{opts.review_model}`."
    try:
        ws.git("add", "--", *_fix(ws, f, tests, opts.review_model))
        ws.git("commit", "-q", "-m", f"fix: adapt the code to #{n}", "-m", body, "-m", TRAILER)
        # no --force: a newer push by Dependabot wins; the caller marks this head either way
        ws.git("push", "-q", "origin", f"HEAD:refs/heads/{pr['headRefName']}")
    except FAILURES as exc:
        _tried(ws, exc, f"Dependabot #{n}")
        return ""
    return ws.git("rev-parse", "HEAD").strip()
