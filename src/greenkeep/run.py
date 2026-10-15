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
from .gate import coverage_violations, is_test_path, line_is_covered, suppressions
from .state import MAX_ATTEMPTS, State
from .workspace import (
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


# anything that sinks one attempt; it is counted, so a poison finding parks instead of
# failing every run. ponytail: a gateway outage also burns attempts (one finding per
# repo per run); add a health ping before the run if that starts parking good findings
ATTEMPT_FAILURES = (GateFailure, EditError, WorkspaceError, HttpLlmError, OSError,
                    UnicodeError)


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


@dataclass(frozen=True)
class Job:
    """Everything one repo's run shares: the clone, its baseline, the options, the history."""
    ws: Workspace
    base: Baseline
    opts: Options
    state: State

    def workable(self, f: Finding) -> bool:
        return self.state.is_workable(self.ws.name, f.fingerprint)


def characterization_path(f: Finding) -> str:
    """One file per module, named after its full path so same-named modules don't clash."""
    stem = f.path.removeprefix("src/").removesuffix(".py").replace("/", "_")
    return f"tests/test_{stem}_characterization.py"


def _write_tests(job: Job, f: Finding, feedback: str) -> Change:
    rel = characterization_path(f)
    target = job.ws.root / rel
    if target.exists():
        raise GateFailure(f"{rel} already exists")
    source = (job.ws.root / f.path).read_text()
    prompt = llm.characterization_prompt(f, source, feedback)
    reply = llm.ask(llm.TEST_SYSTEM, prompt, job.opts.model)
    target.parent.mkdir(exist_ok=True)
    target.write_text(python_block(reply.text))
    ruff_fix(job.ws, rel)  # safe fixes only: an unused import must not cost an attempt
    return Change([rel], reply.model, reply.prompt_sha)


def produce(job: Job, f: Finding, feedback: str) -> Change:
    if f.kind == "untested":
        return _write_tests(job, f, feedback)
    if f.fixable and not feedback:
        ruff_fix(job.ws, f.path, f.rule)
        return Change([f.path], "ruff --fix", "-")
    source = (job.ws.root / f.path).read_text()
    reply = llm.ask(llm.FIX_SYSTEM, llm.fix_prompt(f, source, feedback), job.opts.model)
    edited = apply_edits(job.ws.root, parse_edits(reply.text), job.ws.tracked())
    return Change(edited, reply.model, reply.prompt_sha)


def _percent(cov: dict, path: str) -> float:
    return cov.get("files", {}).get(path, {}).get("summary", {}).get("percent_covered", 0.0)


def _check_scope(job: Job, f: Finding, paths: list[str]) -> None:
    """What the change may touch: test files only, or covered lines that fix the finding."""
    if not paths:
        raise GateFailure("the change is empty")
    if f.kind == "untested" and not all(is_test_path(p) for p in paths):
        raise GateFailure(f"characterization tests may only add test files: {paths}")
    if f.kind == "ruff" and not is_test_path(f.path) and any(map(is_test_path, paths)):
        raise GateFailure(f"a source fix may not touch tests: {paths}")
    if f.kind == "ruff":
        if bad := coverage_violations(job.ws.git("diff", "-U0"), job.base.cov):
            raise GateFailure(f"changed lines were not covered before the change: {bad}")
        if any(a.fingerprint == f.fingerprint for a in lint(job.ws, f.path)):
            raise GateFailure("ruff still reports the finding")


def _check_no_new_lint(job: Job, paths: list[str]) -> None:
    if silenced := suppressions(job.ws.git("diff", "-U0", "HEAD")):
        raise GateFailure(f"the change silences tools instead of fixing code: {silenced}")
    py = [p for p in paths if p.endswith(".py")]
    new = [a for a in (lint(job.ws, *py) if py else []) if a.fingerprint not in job.base.lint]
    if new:
        raise GateFailure("the change adds lint findings: "
                          + "; ".join(f"{a.path}:{a.line} {a.rule} {a.message}" for a in new))


def _check_tests(job: Job, f: Finding) -> None:
    after = run_tests(job.ws)
    if not after.ok:
        raise GateFailure(f"test suite failed: {_summary(after.output)}\n{after.output}")
    if after.no_tests and not job.base.tests.no_tests:
        raise GateFailure("pytest collected no tests, but the baseline had some")
    if f.kind == "untested" and _percent(after.cov, f.path) <= _percent(job.base.cov, f.path):
        raise GateFailure(f"coverage of {f.path} did not grow")


def _summary(output: str) -> str:
    """pytest's closing `N failed, M passed` line: the one-line reason for logs and issues."""
    lines = output.strip().splitlines()
    return next((ln.strip("= ") for ln in reversed(lines) if " failed" in ln or " error" in ln),
                lines[-1] if lines else "no output")


def gate(job: Job, f: Finding) -> list[str]:
    """Raise GateFailure unless the change is safe to merge; return the changed paths."""
    paths = job.ws.changed()
    _check_scope(job, f, paths)
    _check_no_new_lint(job, paths)
    _check_tests(job, f)
    return paths


def _branch(f: Finding) -> str:
    return "greenkeep/" + hashlib.sha1(f.fingerprint.encode()).hexdigest()[:10]


def _commit_type(f: Finding) -> str:
    if f.kind == "untested":
        return "test"
    return "fix" if rank(f.rule) <= URGENT_RANK else "chore"


@dataclass(frozen=True)
class Fix:
    """A gated change ready to commit, and the plan issue it closes (push mode only)."""
    finding: Finding
    change: Change
    paths: list[str]
    issue: int | None

    def describe(self) -> str:
        f, c = self.finding, self.change
        lines = [f"Finding: `{f.rule}` at `{f.path}:{f.line}`: {f.message}",
                 f"Patched by: `{c.model}` (prompt `{c.prompt_sha}`)",
                 "Gate: " + GATE_NOTE[f.kind]]
        if self.issue:
            lines.append(f"\nCloses #{self.issue}")
        return "\n".join(lines)


def commit(ws: Workspace, fix: Fix) -> str:
    branch = _branch(fix.finding)
    ws.git("checkout", "-q", "-B", branch)  # -B: a failed publish may have left it
    ws.git("add", "--", *fix.paths)
    title = f"{_commit_type(fix.finding)}: {fix.finding.title}"[:72]
    ws.git("commit", "-q", "-m", title, "-m", fix.describe(), "-m", TRAILER)
    return branch


def _ensure_issue(job: Job, f: Finding) -> int | None:
    if not job.opts.push:
        return None
    issue = job.state.issue(job.ws.name, f.fingerprint)
    if issue is None:
        body = f"`{f.rule}` at `{f.path}:{f.line}`\n\n{f.message}\n\nFound by greenkeep."
        issue = github.open_issue(job.ws, f.title, body)
        job.state.set(job.ws.name, f.fingerprint, issue=issue)
    return issue


def _attempt(job: Job, f: Finding, feedback: str) -> tuple[Change, list[str]]:
    job.ws.reset()
    change = produce(job, f, feedback)
    return change, gate(job, f)


def _record_failure(job: Job, f: Finding, issue: int | None, reason: str) -> None:
    n = job.state.add_attempt(job.ws.name, f.fingerprint)
    log.info("  attempt %d/%d failed: %s", n, MAX_ATTEMPTS, reason.splitlines()[0])
    if n >= MAX_ATTEMPTS and issue:
        github.park_issue(job.ws, issue, reason.splitlines()[0])


def _land(job: Job, fix: Fix) -> None:
    """Commit; in push mode also publish and merge. Records the merge either way."""
    branch = commit(job.ws, fix)
    pr = github.publish(job.ws, branch, fix.finding.title, fix.describe()) \
        if job.opts.push else None
    job.state.set(job.ws.name, fix.finding.fingerprint, status="merged", pr=pr)
    log.info("  merged %s via %s%s", fix.finding.title, fix.change.model,
             f" (PR #{pr})" if pr else "")
    if not job.opts.push:
        log.info("%s", job.ws.git("show", "--stat", "--patch", "HEAD"))


def work(job: Job, f: Finding) -> bool:
    """Up to the remaining attempts on one finding; True once its fix is merged."""
    issue = _ensure_issue(job, f)
    feedback = ""
    while job.workable(f):
        try:
            change, paths = _attempt(job, f, feedback)
            _land(job, Fix(f, change, paths, issue))
            return True
        except ATTEMPT_FAILURES as exc:
            feedback = redact(str(exc), job.ws.secrets())[-FEEDBACK_CHARS:]
            _record_failure(job, f, issue, feedback)
    return False


def redact(text: str, secrets: list[str]) -> str:
    """Mask .env values: failure text goes to the LLM and, in short, to a public issue."""
    for value in secrets:
        text = text.replace(value, "***")
    return text


def _candidates(job: Job) -> list[Finding]:
    """Workable findings in priority order. A lint finding on an uncovered line is not
    attempted at all: it becomes workable once tests cover that line."""
    return [f for f in job.base.findings if job.workable(f) and _attemptable(job, f)]


def _attemptable(job: Job, f: Finding) -> bool:
    if f.kind == "untested":  # its tests already exist: more coverage is a later finding
        return not (job.ws.root / characterization_path(f)).exists()
    return line_is_covered(job.base.cov, f.path, f.line)


def _work_repo(job: Job) -> None:
    for f in _candidates(job)[:MAX_FINDINGS_PER_REPO]:
        if job.state.merged_today() >= job.opts.budget:
            return
        log.info("%s: %s", job.ws.name, f.title)
        if work(job, f):
            # ponytail: one merge per repo per run, the baseline coverage is stale after
            # it; re-run the baseline here if one fix per repo per day proves too slow
            return


def run_repo(src: Path, opts: Options, state: State) -> None:
    ws = clone(src)
    try:
        sync(ws)
        tests = run_tests(ws)
        if not tests.ok:
            log.warning("%s: baseline tests are red, skipping\n%s", ws.name, tests.output)
            return
        if opts.push:
            github.ensure_labels(ws)
        _work_repo(Job(ws, baseline(ws, tests), opts, state))
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
        except Exception:  # one broken repo must not cost the others their run
            failed += 1
            log.exception("%s: run failed", src.name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
