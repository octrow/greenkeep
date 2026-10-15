"""One repo's run: baseline, findings, then each finding through the gate to a merge."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace

from claude_cli.http_api import HttpLlmError

from . import deps, github, llm, reverts
from .config import Target
from .core import Baseline, Change, GateFailure, Job, Options, lint
from .digest import Digest
from .edits import EditError
from .findings import Finding, untested_findings
from .gate import suppressions
from .kinds import kind_of
from .state import MAX_ATTEMPTS, State
from .workspace import TestRun, Workspace, WorkspaceError, clone, discard, run_tests, sync

log = logging.getLogger("greenkeep")

MAX_FINDINGS_PER_REPO = 10
FEEDBACK_CHARS = 1500
PYTEST_TOTALS = re.compile(r"^=*\s*(\d+ (?:failed|passed|errors?)\b[^\n]*? in [\d.]+s)",
                           re.MULTILINE)
TRAILER = "Co-Authored-By: greenkeep <greenkeep@users.noreply.github.com>"

# anything that sinks one attempt; it is counted, so a poison finding parks instead of
# failing every run. ponytail: a gateway outage also burns attempts (one finding per
# repo per run); add a health ping before the run if that starts parking good findings
ATTEMPT_FAILURES = (GateFailure, EditError, WorkspaceError, HttpLlmError, OSError,
                    UnicodeError)


def baseline(ws: Workspace, tests: TestRun) -> Baseline:
    """All findings, in the work order their kinds define."""
    ruff = lint(ws)
    found = [*ruff, *untested_findings(tests.cov), *deps.collect(ws)]
    ordered = sorted(found, key=lambda f: kind_of(f).priority(f))  # stable within a kind
    return Baseline(tests, frozenset(f.fingerprint for f in ruff), ordered)


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
    kind_of(f).check_after(job, f, after)


def _summary(output: str) -> str:
    """pytest's closing `N failed, M passed` line: the one-line reason for logs and issues."""
    found = PYTEST_TOTALS.findall(output)
    if found:
        return found[-1]
    lines = output.strip().splitlines()
    return lines[-1] if lines else "no output"


def gate(job: Job, f: Finding) -> list[str]:
    """Raise GateFailure unless the change is safe to merge; return the changed paths."""
    paths = job.ws.changed()
    if not paths:
        raise GateFailure("the change is empty")
    kind_of(f).check_scope(job, f, paths)
    _check_no_new_lint(job, paths)
    _check_tests(job, f)
    return paths


def _branch(f: Finding) -> str:
    return "greenkeep/" + hashlib.sha1(f.fingerprint.encode()).hexdigest()[:10]


@dataclass(frozen=True)
class Fix:
    """A gated change ready to commit, and the plan issue it closes (push mode only)."""
    finding: Finding
    change: Change
    paths: list[str]
    issue: int | None

    def describe(self) -> str:
        f, c = self.finding, self.change
        where = f"`{f.path}:{f.line}`" if f.line else f"`{f.path}`"
        lines = [f"Finding: `{f.rule}` at {where}: {f.message}",
                 f"Patched by: `{c.model}` (prompt `{c.prompt_sha}`)"
                 + (f", reviewed by: `{c.reviewer}`" if c.reviewer else ""),
                 "Gate: " + kind_of(f).gate_note]
        if self.issue:
            lines.append(f"\nCloses #{self.issue}")
        return "\n".join(lines)


def commit(ws: Workspace, fix: Fix) -> str:
    branch = _branch(fix.finding)
    ws.git("checkout", "-q", "-B", branch)  # -B: a failed publish may have left it
    ws.git("add", "--", *fix.paths)
    title = f"{kind_of(fix.finding).commit_type(fix.finding)}: {fix.finding.title}"[:72]
    ws.git("commit", "-q", "-m", title, "-m", fix.describe(), "-m", TRAILER)
    return branch


def _ensure_issue(job: Job, f: Finding) -> int | None:
    if not job.opts.push:
        return None
    issue = job.state.issue(job.ws.name, f.fingerprint)
    if issue is None:
        body = f"`{f.rule}` at `{f.path}`\n\n{f.message}\n\nFound by greenkeep."
        issue = github.open_issue(job.ws, f.title, body)
        job.state.set(job.ws.name, f.fingerprint, issue=issue)
    return issue


def _attempt(job: Job, f: Finding, feedback: str) -> tuple[Change, list[str]]:
    job.ws.reset()
    change = kind_of(f).produce(job, f, feedback)
    paths = gate(job, f)
    return _review(job, f, change), paths


def _review(job: Job, f: Finding, change: Change) -> Change:
    """The last, most expensive check: a second model reads the diff that passed the gate."""
    if change.deterministic:
        return change
    verdict = llm.review(f, job.ws.git("diff", "HEAD"), job.opts.review_model)
    if not verdict.approve:
        raise GateFailure(f"reviewer ({verdict.model}) rejected the change: {verdict.reason}")
    return replace(change, reviewer=verdict.model)


def _record_failure(job: Job, f: Finding, reason: str) -> None:
    job.state.add_attempt(job.ws.name, f.fingerprint)
    n = job.state.attempts(job.ws.name, f.fingerprint)
    log.info("  attempt %d/%d failed: %s", n, MAX_ATTEMPTS, reason.splitlines()[0])
    log.debug("  full failure:\n%s", reason)
    if n < MAX_ATTEMPTS:
        return
    issue = job.state.issue(job.ws.name, f.fingerprint)
    job.digest.parked.append(f"{job.ws.name}: {f.title}"
                             + (f" (issue #{issue})" if issue else ""))
    if issue:
        github.park_issue(job.ws, issue, reason.splitlines()[0])


def _land(job: Job, fix: Fix) -> None:
    """Commit; in push mode also publish and merge. Records the merge either way."""
    branch = commit(job.ws, fix)
    if job.opts.push:
        pr = github.publish(job.ws, branch, fix.finding.title, fix.describe())
        sha = github.merge_sha(job.ws, pr)
    else:
        pr, sha = None, job.ws.git("rev-parse", "HEAD").strip()
    job.state.set(job.ws.name, fix.finding.fingerprint, status="merged", pr=pr, sha=sha)
    log.info("  merged %s via %s%s", fix.finding.title, fix.change.model,
             f" (PR #{pr})" if pr else "")
    job.digest.merged.append(f"{job.ws.name}: {fix.finding.title}"
                             + (f" (PR #{pr})" if pr else ""))
    if not job.opts.push:
        log.info("%s", job.ws.git("show", "--stat", "--patch", "HEAD"))


def work(job: Job, f: Finding) -> bool:
    """Up to the remaining attempts on one finding; True once its fix is merged."""
    job.state.set(job.ws.name, f.fingerprint, title=f.title, url=web_url(job.ws.origin))
    issue = _ensure_issue(job, f)
    feedback = ""
    while job.state.is_workable(job.ws.name, f.fingerprint):
        try:
            change, paths = _attempt(job, f, feedback)
            _land(job, Fix(f, change, paths, issue))
            return True
        except ATTEMPT_FAILURES as exc:
            feedback = clip(redact(str(exc), job.ws.secrets()))
            _record_failure(job, f, feedback)
    return False


def web_url(origin: str) -> str:
    """https://github.com/owner/name for an https or ssh GitHub remote, else ""."""
    m = re.match(r"(?:https://|git@)github\.com[/:](.+?)(?:\.git)?/?$", origin)
    return f"https://github.com/{m.group(1)}" if m else ""


def clip(text: str) -> str:
    """Keep the one-line reason (first line) and the end of long output (the traceback)."""
    if len(text) <= FEEDBACK_CHARS:
        return text
    return text.splitlines()[0] + "\n…\n" + text[-FEEDBACK_CHARS:]


def redact(text: str, secrets: list[str]) -> str:
    """Mask .env values: failure text goes to the LLM and, in short, to a public issue."""
    for value in secrets:
        text = text.replace(value, "***")
    return text


def _candidates(job: Job) -> list[Finding]:
    return [f for f in job.base.findings
            if job.state.is_workable(job.ws.name, f.fingerprint)
            and kind_of(f).attemptable(job, f)]


def _within_budget(job: Job, f: Finding) -> bool:
    return kind_of(f).exempt_from_budget or job.state.merged_today() < job.opts.budget


def _work_repo(job: Job) -> None:
    for f in _candidates(job)[:MAX_FINDINGS_PER_REPO]:
        if not _within_budget(job, f):
            continue  # a later finding may still be budget-exempt (security)
        log.info("%s: %s", job.ws.name, f.title)
        if work(job, f):
            # ponytail: one merge per repo per run, the baseline is stale after it;
            # re-run the baseline here if one fix per repo per day proves too slow
            return


def run_repo(target: Target, opts: Options, state: State, digest: Digest) -> None:
    ws = clone(target)
    try:
        reverts.check(ws, state, backfill=opts.push)  # raises AutoPause before any work
        sync(ws)
        tests = run_tests(ws)
        if not tests.ok:
            log.warning("%s: baseline tests are red, skipping\n%s", ws.name, tests.output)
            digest.problems.append(f"{ws.name}: baseline tests are red in a clean clone")
            return
        if opts.push:
            github.ensure_labels(ws)
        _work_repo(Job(ws, baseline(ws, tests), opts, state, digest))
    finally:
        if not opts.keep:
            discard(ws)
