"""Dependabot's open PRs, merged once greenkeep's own suite passes on them.

The owner's private repos have no Actions minutes left, so their CI never starts and the
PRs pile up; a local suite run is the check. A major upgrade also needs the suite to cover
OUTDATED_MIN_COVERAGE. A PR red here gets one try by the agent (repair.adapt), which adapts
the code on the PR's own branch; nobody else would (ADR 0004).
"""

from __future__ import annotations

import json
import logging
import re

from . import deps, github, repair
from .core import Options
from .digest import Digest
from .kinds import OUTDATED_MIN_COVERAGE
from .state import State
from .workspace import TestRun, Workspace, WorkspaceError, run_tests, sync

log = logging.getLogger("greenkeep")

MAX_DEPENDABOT_PER_RUN = 3  # suite runs cost minutes; the rest wait for the next run
FIELDS = "number,title,body,headRefOid,headRefName,mergeable,files,statusCheckRollup"
# `from 2.31.0 to 2.32.0`, `from v3 to v4`, ``from `0.6.1` to `0.6.9` ``, `from >=1.0 to >=1.2.4`
BUMP = re.compile(r"\bfrom `?(?:[<>=~^]=?)?v?(\d[^\s`]*)`? to `?(?:[<>=~^]=?)?v?(\d[^\s`]*)")
TRAILING = ".,;:)"
# release notes and commit lists: they quote upstream's own bumps, not this PR's
DETAILS = re.compile(r"<details>.*?</details>", re.DOTALL | re.IGNORECASE)
ACTIONS_DIR = ".github/"
CHECKS_OK = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
FAILURES = (WorkspaceError, OSError, ValueError, KeyError)


def bumps(text: str) -> list[tuple[str, str]]:
    """Every `from X to Y` version pair outside the collapsed sections."""
    shown = DETAILS.sub("", text)
    return [(a.rstrip(TRAILING), b.rstrip(TRAILING)) for a, b in BUMP.findall(shown)]


def upgrade(title: str, body: str) -> str:
    """ "same" when every pair named stays on its major line, "major" when one leaves it, ""
    when none is named or one is not a version (a sha, a docker tag): that PR is skipped."""
    pairs = bumps(f"{title}\n{body}")
    versions = [(deps._version(old), deps._version(new)) for old, new in pairs]
    known = [(a, b) for a, b in versions if a is not None and b is not None]
    if not pairs or len(known) < len(pairs):
        return ""
    return "same" if all(deps.same_line(a, b) for a, b in known) else "major"


def actions_only(files: list[dict]) -> bool:
    """Only workflow files: the local suite never runs them, so it cannot vouch for them."""
    return bool(files) and all(f["path"].startswith(ACTIONS_DIR) for f in files)


def checks_passed(rollup: list[dict]) -> bool:
    """CI's own verdict: every check done and none failed. No checks at all (no Actions
    minutes) is no verdict."""
    return bool(rollup) and all(
        (c.get("conclusion") or c.get("state")) in CHECKS_OK for c in rollup
    )


def _key(ws: Workspace, pr: dict) -> str:
    return f"dependabot:{ws.name}:{pr['number']}"


def _eligible(ws: Workspace, state: State, pr: dict) -> bool:
    if pr["mergeable"] == "CONFLICTING":
        return False  # Dependabot rebases it itself
    if state.mark(_key(ws, pr)) == pr["headRefOid"]:
        return False  # red on this very commit last time
    if not upgrade(pr["title"], pr["body"] or ""):
        log.info(
            "%s: Dependabot #%d names no version, skipped: %s", ws.name, pr["number"], pr["title"]
        )
        return False
    return True


def _merge(ws: Workspace, digest: Digest, pr: dict, head: str = "") -> None:
    # the tested head only: a newer push from Dependabot waits for the next run
    github.merge(ws, pr["number"], head=head or pr["headRefOid"])
    log.info("%s: merged Dependabot #%d: %s", ws.name, pr["number"], pr["title"])
    adapted = ", adapted by the agent" if head else ""
    digest.merged.append(f"{ws.name}: {pr['title']} (Dependabot #{pr['number']}{adapted})")


def _covered(tests: TestRun) -> float:
    return tests.cov.get("totals", {}).get("percent_covered", 0.0)


def _merge_on_checks(ws: Workspace, opts: Options, digest: Digest, pr: dict) -> None:
    if not checks_passed(pr["statusCheckRollup"] or []):
        log.info("%s: Dependabot #%d has no green checks yet", ws.name, pr["number"])
        return
    if not opts.push:
        log.info(
            "%s: would merge Dependabot #%d on its checks: %s", ws.name, pr["number"], pr["title"]
        )
        return
    _merge(ws, digest, pr)


def _test_and_merge(ws: Workspace, opts: Options, state: State, digest: Digest, pr: dict) -> None:
    n, sha = pr["number"], pr["headRefOid"]
    if not opts.push:
        log.info("%s: would test and merge Dependabot #%d: %s", ws.name, n, pr["title"])
        return
    try:
        ws.reset()
        ws.git("fetch", "-q", "origin", f"pull/{n}/head")
        ws.git("checkout", "-q", "--detach", sha)
        sync(ws)
        tests = run_tests(ws)
        major = upgrade(pr["title"], pr["body"] or "") == "major"
        # an agent's green suite proves as little as a major's when the suite covers little
        if (major or not tests.ok) and _covered(tests) < OUTDATED_MIN_COVERAGE:
            log.info("%s: Dependabot #%d: the suite covers too little to vouch for it", ws.name, n)
            head = ""
        elif tests.ok:
            head = sha
        else:
            log.info("%s: Dependabot #%d is red here", ws.name, n)
            log.debug("  suite output:\n%s", tests.output)
            head = repair.adapt(ws, opts, digest, pr, tests) if repair.room(digest) else ""
    finally:
        ws.reset()  # back on the base branch
    if not head:
        state.set_mark(_key(ws, pr), sha)  # this head is not tried again
        return
    _merge(ws, digest, pr, "" if head == sha else head)


def merge_safe(ws: Workspace, opts: Options, state: State, digest: Digest) -> None:
    """After greenkeep's own fixes, on a repo whose baseline suite is green. Dependabot
    merges neither spend the daily budget nor become findings."""
    if not github.web_url(ws.origin):
        return  # not on GitHub: no Dependabot
    try:
        prs = json.loads(
            github._gh(
                ws, "pr", "list", "--author", "app/dependabot", "--state", "open", "--json", FIELDS
            )
        )
    except FAILURES as exc:
        log.warning("%s: cannot list Dependabot PRs: %s", ws.name, exc)
        return
    tested = 0
    for pr in prs:
        try:
            if not _eligible(ws, state, pr):
                continue
            if actions_only(pr["files"]):
                _merge_on_checks(ws, opts, digest, pr)
            elif tested < MAX_DEPENDABOT_PER_RUN:
                tested += 1
                _test_and_merge(ws, opts, state, digest, pr)
        except FAILURES as exc:
            log.warning("%s: Dependabot #%s skipped: %s", ws.name, pr.get("number"), exc)
