"""Plan issues and fix PRs through the `gh` CLI, run inside the clone."""

from __future__ import annotations

import re
import time

from .workspace import Workspace, sh

LABEL = "greenkeep"
PARKED_LABEL = "needs-human"
LABELS = {LABEL: "0e8a16", PARKED_LABEL: "d93f0b"}
NUMBER = re.compile(r"/(\d+)\s*$")
CHECKS_TIMEOUT = 1800
MERGE_STATE_POLLS = 10  # GitHub works mergeability out within seconds of `pr create`
MERGE_STATE_WAIT = 3


def web_url(origin: str) -> str:
    """https://github.com/owner/name for an https or ssh GitHub remote, else ""."""
    m = re.match(r"(?:https://|git@)github\.com[/:](.+?)(?:\.git)?/?$", origin)
    return f"https://github.com/{m.group(1)}" if m else ""


def _gh(ws: Workspace, *args: str) -> str:
    return sh(["gh", *args], ws.root).stdout.strip()


def _number(url: str) -> int:
    if m := NUMBER.search(url):
        return int(m.group(1))
    raise ValueError(f"no issue/PR number in gh output: {url!r}")


def ensure_labels(ws: Workspace) -> None:
    for name, color in LABELS.items():
        _gh(ws, "label", "create", name, "--color", color, "--force")


def open_issue(ws: Workspace, title: str, body: str) -> int:
    return _number(_gh(ws, "issue", "create", "--title", title, "--body", body,
                       "--label", LABEL))


def park_issue(ws: Workspace, issue: int, reason: str) -> None:
    """Only the one-line reason goes public: test output may echo secrets from .env."""
    _gh(ws, "issue", "comment", str(issue), "--body",
        f"greenkeep gave up after its attempts. Last failure: `{reason[:200]}`\n\n"
        "Full output is in the local greenkeep log.")
    _gh(ws, "issue", "edit", str(issue), "--add-label", PARKED_LABEL)


def close_issue(ws: Workspace, issue: int, reason: str) -> bool:
    """False when gh could not close it (already closed by hand, or offline)."""
    return sh(["gh", "issue", "close", str(issue), "--comment", reason], ws.root,
              check=False).returncode == 0


def publish(ws: Workspace, branch: str, title: str, body: str) -> int:
    """Push the branch, open the PR (or reuse one a failed run left) and squash-merge it.

    Idempotent: the branch lives in greenkeep's own namespace, so a stale one from an
    earlier failed publish is simply overwritten.
    """
    ws.git("push", "-q", "--force", "-u", "origin", branch)
    open_pr = _gh(ws, "pr", "list", "--head", branch, "--state", "open", "--json", "number",
                  "--jq", ".[0].number // empty")
    pr = int(open_pr) if open_pr else _number(_gh(
        ws, "pr", "create", "--head", branch, "--base", ws.base_branch,
        "--title", title, "--body", body, "--label", LABEL))
    merge(ws, pr)
    return pr


def merge_sha(ws: Workspace, pr: int) -> str:
    """The commit a merged PR put on the base branch (what a revert names)."""
    return _gh(ws, "pr", "view", str(pr), "--json", "mergeCommit", "--jq",
               ".mergeCommit.oid // empty")


def merge(ws: Workspace, pr: int, head: str = "") -> None:
    """Merge now, or once the repo's required checks (CI) have passed. With `head`, only
    while the PR still points at that commit (the one greenkeep tested)."""
    cmd = ["gh", "pr", "merge", str(pr), "--squash", "--delete-branch",
           *(["--match-head-commit", head] if head else [])]
    if sh(cmd, ws.root, check=False).returncode == 0:
        return
    # BLOCKED: required checks still running (or a review); without required checks
    # `checks --required` itself fails, so any other state just tries the merge again
    if _merge_state(ws, pr) == "BLOCKED":
        sh(["gh", "pr", "checks", str(pr), "--watch", "--required", "--fail-fast"], ws.root,
           timeout=CHECKS_TIMEOUT)  # red or hung CI raises: the attempt fails
    sh(cmd, ws.root)


def _merge_state(ws: Workspace, pr: int) -> str:
    """mergeStateStatus once GitHub has computed it (UNKNOWN right after the PR opens)."""
    for _ in range(MERGE_STATE_POLLS):
        state = _gh(ws, "pr", "view", str(pr), "--json", "mergeStateStatus",
                    "--jq", ".mergeStateStatus")
        if state != "UNKNOWN":
            break
        time.sleep(MERGE_STATE_WAIT)
    return state
