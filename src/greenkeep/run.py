"""One run: clone each tended repo, collect findings, push each through the gate."""

from __future__ import annotations

import argparse
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from claude_cli.http_api import HttpLlmError

from . import github, llm
from .edits import EditError, apply_edits, parse_edits, python_block
from .findings import Finding, rank, ruff_findings, untested_findings
from .gate import coverage_violations, is_test_path, line_is_covered
from .state import MAX_ATTEMPTS, State
from .workspace import (
    KEEP_ON_RESET,
    TestRun,
    Workspace,
    WorkspaceError,
    clone,
    discard,
    ruff_fix,
    ruff_json,
    run_tests,
    sync,
)

log = logging.getLogger("greenkeep")

DEFAULT_MODEL = "free-stack"
DEFAULT_BUDGET = 3
MAX_FINDINGS_PER_REPO = 10
URGENT_RANK = 2  # S/F/B ruff rules go before characterization tests
FEEDBACK_CHARS = 1500
GATE_NOTE = {
    "ruff": "full test suite green; every changed line was covered before the change; "
            "no new lint findings.",
    "untested": "only test files added; full test suite green; module coverage grew; "
                "no new lint findings.",
}
TRAILER = "Co-Authored-By: greenkeep <greenkeep@users.noreply.github.com>"


class GateFailure(Exception):
    """The attempt's change did not pass the gate."""


@dataclass(frozen=True)
class Options:
    push: bool
    budget: int
    model: str
    keep: bool


@dataclass(frozen=True)
class Change:
    paths: list[str]
    model: str
    prompt_sha: str


@dataclass(frozen=True)
class Baseline:
    """The repo before any attempt: what the gate compares every change against."""
    tests: TestRun
    lint: frozenset[str]  # fingerprints of ruff findings already there
    findings: list[Finding]

    @property
    def cov(self) -> dict:
        return self.tests.cov


def baseline(ws: Workspace, tests: TestRun) -> Baseline:
    ruff = lint(ws)
    urgent = [f for f in ruff if rank(f.rule) <= URGENT_RANK]
    rest = [f for f in ruff if rank(f.rule) > URGENT_RANK]
    findings = urgent + untested_findings(tests.cov) + rest
    return Baseline(tests, frozenset(f.fingerprint for f in ruff), findings)


def lint(ws: Workspace, *paths: str) -> list[Finding]:
    return ruff_findings(ruff_json(ws, *paths), root=str(ws.root), read_line=ws.read_line)


def _write_tests(ws: Workspace, f: Finding, model: str, feedback: str) -> Change:
    rel = f"tests/test_{Path(f.path).stem}_characterization.py"
    target = ws.root / rel
    if target.exists():
        raise GateFailure(f"{rel} already exists")
    source = (ws.root / f.path).read_text()
    reply = llm.ask(llm.TEST_SYSTEM, llm.characterization_prompt(f, source, feedback), model)
    target.parent.mkdir(exist_ok=True)
    target.write_text(python_block(reply.text))
    return Change([rel], reply.model, reply.prompt_sha)


def produce(ws: Workspace, f: Finding, model: str, feedback: str) -> Change:
    if f.kind == "untested":
        return _write_tests(ws, f, model, feedback)
    if f.fixable and not feedback:
        ruff_fix(ws, f.rule, f.path)
        return Change([f.path], "ruff --fix", "-")
    source = (ws.root / f.path).read_text()
    prompt = llm.fix_prompt(f, source, ws.read_line(f.path, f.line), feedback)
    reply = llm.ask(llm.FIX_SYSTEM, prompt, model)
    return Change(apply_edits(ws.root, parse_edits(reply.text)), reply.model, reply.prompt_sha)


def changed_paths(ws: Workspace) -> list[str]:
    lines = ws.git("status", "--porcelain", "-uall").splitlines()
    paths = [ln[3:] for ln in lines]
    return [p for p in paths if not p.startswith(KEEP_ON_RESET)]


def _percent(cov: dict, path: str) -> float:
    return cov.get("files", {}).get(path, {}).get("summary", {}).get("percent_covered", 0.0)


def gate(ws: Workspace, f: Finding, base: Baseline) -> list[str]:
    """Raise GateFailure unless the change is safe to merge; return the changed paths."""
    paths = changed_paths(ws)
    if not paths:
        raise GateFailure("the change is empty")
    if f.kind == "untested" and not all(is_test_path(p) for p in paths):
        raise GateFailure(f"characterization tests may only add test files: {paths}")
    if f.kind == "ruff":
        if bad := coverage_violations(ws.git("diff", "-U0"), base.cov):
            raise GateFailure(f"changed lines were not covered before the change: {bad}")
        if any(a.fingerprint == f.fingerprint for a in lint(ws, f.path)):
            raise GateFailure("ruff still reports the finding")
    py = [p for p in paths if p.endswith(".py")]
    if new := [a for a in (lint(ws, *py) if py else []) if a.fingerprint not in base.lint]:
        raise GateFailure("the change adds lint findings: "
                          + "; ".join(f"{a.path}:{a.line} {a.rule} {a.message}" for a in new))
    run = run_tests(ws)
    if not run.ok:
        raise GateFailure(f"test suite failed:\n{run.output}")
    if f.kind == "untested" and _percent(run.cov, f.path) <= _percent(base.cov, f.path):
        raise GateFailure(f"coverage of {f.path} did not grow")
    return paths


def _branch(f: Finding) -> str:
    return "greenkeep/" + hashlib.sha1(f.fingerprint.encode()).hexdigest()[:10]


def _commit_type(f: Finding) -> str:
    if f.kind == "untested":
        return "test"
    return "fix" if rank(f.rule) <= URGENT_RANK else "chore"


def _describe(f: Finding, change: Change, issue: int | None) -> str:
    lines = [f"Finding: `{f.rule}` at `{f.path}:{f.line}`: {f.message}",
             f"Patched by: `{change.model}` (prompt `{change.prompt_sha}`)",
             "Gate: " + GATE_NOTE[f.kind]]
    if issue:
        lines.append(f"\nCloses #{issue}")
    return "\n".join(lines)


def commit(ws: Workspace, f: Finding, change: Change, paths: list[str], issue: int | None) -> str:
    branch = _branch(f)
    ws.git("checkout", "-q", "-b", branch)
    ws.git("add", "--", *paths)
    title = f"{_commit_type(f)}: {f.title}"[:72]
    ws.git("commit", "-q", "-m", title, "-m", _describe(f, change, issue), "-m", TRAILER)
    return branch


def _ensure_issue(ws: Workspace, f: Finding, opts: Options, state: State) -> int | None:
    if not opts.push:
        return None
    issue = state.issue(ws.name, f.fingerprint)
    if issue is None:
        body = f"`{f.rule}` at `{f.path}:{f.line}`\n\n{f.message}\n\nFound by greenkeep."
        issue = github.open_issue(ws, f.title, body)
        state.set(ws.name, f.fingerprint, issue=issue)
    return issue


def _attempt(ws: Workspace, f: Finding, base: Baseline, opts: Options,
             feedback: str) -> tuple[Change, list[str]]:
    ws.reset()
    change = produce(ws, f, opts.model, feedback)
    return change, gate(ws, f, base)


def work(ws: Workspace, f: Finding, base: Baseline, opts: Options, state: State) -> bool:
    """Up to the remaining attempts on one finding; True once its fix is merged."""
    issue = _ensure_issue(ws, f, opts, state)
    feedback = ""
    while state.is_workable(ws.name, f.fingerprint):
        try:
            change, paths = _attempt(ws, f, base, opts, feedback)
        except (GateFailure, EditError) as exc:
            feedback = str(exc)[-FEEDBACK_CHARS:]
            n = state.add_attempt(ws.name, f.fingerprint)
            log.info("  attempt %d/%d failed: %s", n, MAX_ATTEMPTS, feedback.splitlines()[0])
            if n >= MAX_ATTEMPTS and issue:
                github.park_issue(ws, issue, feedback)
            continue
        branch = commit(ws, f, change, paths, issue)
        pr = github.publish(ws, branch, f.title, _describe(f, change, issue)) \
            if opts.push else None
        state.set(ws.name, f.fingerprint, status="merged", pr=pr)
        log.info("  merged %s via %s%s", f.title, change.model, f" (PR #{pr})" if pr else "")
        if not opts.push:
            log.info("%s", ws.git("show", "--stat", "--patch", "HEAD"))
        return True
    return False


def run_repo(src: Path, opts: Options, state: State) -> None:
    ws = clone(src)
    try:
        sync(ws)
        tests = run_tests(ws)
        if not tests.ok:
            log.warning("%s: baseline tests are red, skipping\n%s", ws.name, tests.output)
            return
        base = baseline(ws, tests)
        if opts.push:
            github.ensure_labels(ws)
        tried = 0
        for f in base.findings:
            if state.merged_today() >= opts.budget or tried >= MAX_FINDINGS_PER_REPO:
                break
            if not state.is_workable(ws.name, f.fingerprint):
                continue
            if f.kind == "ruff" and not line_is_covered(base.cov, f.path, f.line):
                continue  # not an attempt: it becomes workable once tests cover the line
            tried += 1
            log.info("%s: %s", ws.name, f.title)
            if work(ws, f, base, opts, state):
                # ponytail: one merge per repo per run, the baseline coverage is stale after
                # it; re-run the baseline here if one fix per repo per day proves too slow
                return
    finally:
        if not opts.keep:
            discard(ws)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="greenkeep", description=__doc__)
    p.add_argument("repos", nargs="+", type=Path, help="local checkouts of tended repos")
    p.add_argument("--push", action="store_true",
                   help="open issues/PRs and merge them (default: local dry run)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="merged fix PRs per day")
    p.add_argument("--model", default=DEFAULT_MODEL, help="OmniRoute model/combo for patches")
    p.add_argument("--keep", action="store_true", help="keep the throwaway clones")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    opts = Options(a.push, a.budget, a.model, a.keep)
    # a dry run never touches the persistent state: attempts and budget stay unspent
    state = State() if a.push else State(Path(":memory:"))
    failed = 0
    for src in a.repos:
        try:
            run_repo(src.resolve(), opts, state)
        except (WorkspaceError, HttpLlmError) as exc:
            failed += 1
            log.error("%s: %s", src.name, exc)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
