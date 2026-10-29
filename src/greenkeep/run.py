"""One repo's run: baseline, findings, then each finding through the gate to a merge."""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from dataclasses import dataclass, replace
from datetime import date
from textwrap import shorten

from claude_cli import ClaudeCliError

from . import dependabot, deps, github, hunt, llm, ready, reverts
from .config import Target
from .core import Baseline, Change, GateFailure, Job, Options, lint
from .digest import Digest
from .edits import EditError
from .findings import Finding, is_vendored, untested_findings
from .gate import suppressions
from .kinds import kind_of
from .state import MAX_ATTEMPTS, State
from .workspace import (
    BOOST_RULES,
    TestRun,
    Workspace,
    WorkspaceError,
    clone,
    discard,
    remote_head,
    run_tests,
    sync,
    tool_head,
)

log = logging.getLogger("greenkeep")

MAX_FINDINGS_PER_REPO = 10
MAX_PARKED_IN_A_ROW = 2
FEEDBACK_CHARS = 1500
PYTEST_TOTALS = re.compile(r"^=*\s*(\d+ (?:failed|passed|errors?)\b[^\n]*? in [\d.]+s)",
                           re.MULTILINE)
SUBJECT_MAX = 72
TRAILER = "Co-Authored-By: greenkeep <greenkeep@users.noreply.github.com>"

# anything that sinks one attempt; it is counted, so a poison finding parks instead of
# failing every run. Claude not answering at all is not counted (`llm.unavailable`)
ATTEMPT_FAILURES = (GateFailure, EditError, WorkspaceError, ClaudeCliError, OSError,
                    UnicodeError, tomllib.TOMLDecodeError)


def _measured(tests: TestRun) -> bool:
    """A green suite that covers nothing at all means coverage did not see it run: then
    every module would look untested and no new test could ever raise its number."""
    total = tests.cov.get("totals", {}).get("percent_covered", 0.0)
    return tests.no_tests or total > 0


def baseline(ws: Workspace, tests: TestRun) -> Baseline:
    """All findings, in the work order their kinds define."""
    ruff = lint(ws)
    found = [*ruff, *(untested_findings(tests.cov) if _measured(tests) else []),
             *deps.collect(ws), *ready.collect(ws.root, github.web_url(ws.origin))]
    ordered = sorted(found, key=lambda f: kind_of(f).priority(f))  # stable within a kind
    return Baseline(tests, frozenset(f.fingerprint for f in ruff), ordered)


def close_vanished(job: Job) -> None:
    """Close the issues of findings the baseline no longer reports: the owner fixed the
    code, the line changed, or a newer advisory or release replaced the finding."""
    current = {f.fingerprint for f in job.base.findings}
    # outside a boost its rules are not linted: their findings are still there
    current |= {a.fingerprint for a in lint(replace(job.ws, extra_rules=BOOST_RULES))}
    packages = {fp.rsplit(":", 1)[0] for fp in current}
    for fp, issue in job.state.open_issues(job.ws.name):
        if fp in current or not _vanished(job, fp, packages):
            continue
        if github.close_issue(job.ws, issue, "greenkeep no longer finds this, closing."):
            log.info("  closed issue #%d: %s no longer found", issue, fp)
            job.state.set(job.ws.name, fp, issue=None)


def _vanished(job: Job, fp: str, packages: set[str]) -> bool:
    """Whether a finding missing from the baseline is really gone, by its kind."""
    kind = fp.split(":", 1)[0]
    if kind in ("vuln", "outdated"):  # a failed pip-audit or PyPI call reports nothing
        return fp.rsplit(":", 1)[0] in packages
    if kind == "untested":
        return _measured(job.base.tests)
    return kind != "bug"  # bugs come from a hunt, never from a baseline


def _check_no_new_lint(job: Job, paths: list[str]) -> None:
    if silenced := suppressions(job.ws.git("diff", "-U0", "HEAD")):
        raise GateFailure(f"the change silences tools instead of fixing code: {silenced}")
    py = [p for p in paths if p.endswith(".py")]
    # a file greenkeep wrote (a new test) is held to the repo's own rules: a boost's extra
    # rules are for improving existing code, and ruff_fix_new_test fixes by the repo's config
    new_files = set(job.ws.git("diff", "--name-only", "--diff-filter=A", "-z", "HEAD").split("\0"))
    added = [p for p in py if p in new_files]
    old = [p for p in py if p not in new_files]
    found = [*(lint(job.ws, *old) if old else []),
             *(lint(replace(job.ws, extra_rules=()), *added) if added else [])]
    new = [a for a in found if a.fingerprint not in job.base.lint]
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
    title = shorten(f"{kind_of(fix.finding).commit_type(fix.finding)}: {fix.finding.title}",
                    SUBJECT_MAX, placeholder="…")
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
    verdict = llm.review(f, job.ws.git("diff", "HEAD"), job.opts.review_model, job.ws.root)
    if not verdict.approve:
        raise GateFailure(f"reviewer ({verdict.model}) rejected the change: {verdict.reason}")
    return replace(change, reviewer=verdict.model)


def _record_failure(job: Job, f: Finding, reason: str) -> None:
    retries = kind_of(f).retries
    job.state.add_attempt(job.ws.name, f.fingerprint, last=not retries)
    n = job.state.attempts(job.ws.name, f.fingerprint)
    log.info("  attempt %d/%d failed: %s", n, MAX_ATTEMPTS if retries else 1,
             reason.splitlines()[0])
    log.debug("  full failure:\n%s", reason)
    if retries and n < MAX_ATTEMPTS:
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
    job.state.set(job.ws.name, f.fingerprint, title=f.title, url=github.web_url(job.ws.origin))
    issue = _ensure_issue(job, f)
    feedback = ""
    while job.state.is_workable(job.ws.name, f.fingerprint):
        try:
            change, paths = _attempt(job, f, feedback)
            _land(job, Fix(f, change, paths, issue))
            return True
        except ATTEMPT_FAILURES as exc:
            if llm.unavailable(exc):
                raise
            feedback = clip(redact(str(exc), job.ws.secrets()))
            _record_failure(job, f, feedback)
            if _timed_out(exc):
                return False  # up to 10 minutes each: the next attempt waits for tomorrow
    return False


def _timed_out(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timed out after" in str(exc)


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
            and not is_vendored(job.ws.root, f.path)
            and kind_of(f).attemptable(job, f)]


def _within_budget(job: Job, f: Finding) -> bool:
    return kind_of(f).exempt_from_budget or job.state.merged_today() < job.opts.budget


def _hunted(job: Job, enabled: bool) -> list[Finding]:
    """Bug hunt, once nothing else is left and the budget can still take a fix."""
    if not enabled or job.state.merged_today() >= job.opts.budget:
        return []
    try:
        return hunt.hunt(job)
    except ATTEMPT_FAILURES as exc:
        if llm.unavailable(exc):
            raise
        log.warning("%s: bug hunt failed: %s", job.ws.name, exc)
        return []


def _work_repo(job: Job, hunting: bool = False) -> None:
    candidates = _candidates(job)[:MAX_FINDINGS_PER_REPO] or _hunted(job, hunting)
    if not candidates:
        job.digest.clean += 1
        return
    parked_in_a_row, worked = 0, False
    for f in candidates:
        if not _within_budget(job, f):
            continue  # a later finding may still be budget-exempt (security)
        worked = True
        log.info("%s: %s", job.ws.name, f.title)
        if work(job, f):
            # ponytail: one merge per repo per run, the baseline is stale after it;
            # re-run the baseline here if one fix per repo per day proves too slow
            return
        if job.state.status(job.ws.name, f.fingerprint) != "parked":
            continue  # timed out: it waits for the next run, the repo is not to blame
        parked_in_a_row += 1
        if parked_in_a_row >= MAX_PARKED_IN_A_ROW:
            # the repo, not the findings, is likely the problem: stop paying for it
            until = job.state.cool_down(job.ws.name, job.ws.git("rev-parse", "HEAD").strip(),
                                        tool_head())
            job.digest.problems.append(f"{job.ws.name}: {parked_in_a_row} findings parked "
                                       f"in a row, cool-down until {until}")
            return
    if not worked:
        job.digest.waiting.append(job.ws.name)


def _cooling(target: Target, state: State) -> bool:
    """In cool-down, unless it ran out or the repo or greenkeep changed since it began."""
    cd = state.cool_down_of(target.name)
    if cd is None:
        return False
    head = remote_head(target)
    if date.today() < cd.until and head in ("", cd.repo_head) and tool_head() == cd.tool_head:
        return True
    log.info("%s: cool-down lifted", target.name)
    state.lift_cool_down(target.name)
    return False


def _tell_owner(ws: Workspace, state: State, digest: Digest) -> None:
    """What only the owner can settle goes in the digest once, not every day."""
    for note in ready.owner_notes(ws.root):
        key = f"told:{ws.name}:{note}"
        if not state.mark(key):
            digest.owner.append(f"{ws.name}: {note}")
            state.set_mark(key, date.today().isoformat())


def run_repo(target: Target, opts: Options, state: State, digest: Digest) -> None:
    cooling = _cooling(target, state)
    if cooling:
        digest.cooling += 1
    ws = clone(target)
    digest.reached += 1
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
        base = baseline(ws, tests)
        if opts.push:
            close_vanished(Job(ws, base, opts, state, digest))
        if opts.push:  # a dry run tells, but does not remember it told
            _tell_owner(ws, state, digest)
        else:
            digest.owner += [f"{ws.name}: {note}" for note in ready.owner_notes(ws.root)]
        if cooling:  # security fixes and Dependabot do not wait out a cool-down
            base = replace(base, findings=[f for f in base.findings
                                           if kind_of(f).exempt_from_budget])
        if base.findings or not cooling:
            _work_repo(Job(ws, base, opts, state, digest), target.hunt and not cooling)
        dependabot.merge_safe(ws, opts, state, digest)  # greenkeep's own fixes go first
    finally:
        if not opts.keep:
            discard(ws)
