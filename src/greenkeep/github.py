"""Plan issues and fix PRs through the `gh` CLI, run inside the clone."""

from __future__ import annotations

import re

from .workspace import Workspace, sh

LABEL = "greenkeep"
PARKED_LABEL = "needs-human"
LABELS = {LABEL: "0e8a16", PARKED_LABEL: "d93f0b"}
NUMBER = re.compile(r"/(\d+)\s*$")


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
    _gh(ws, "issue", "comment", str(issue), "--body",
        f"greenkeep gave up after its attempts; last failure:\n\n```\n{reason[-1500:]}\n```")
    _gh(ws, "issue", "edit", str(issue), "--add-label", PARKED_LABEL)


def publish(ws: Workspace, branch: str, title: str, body: str) -> int:
    """Push the branch, open the PR and squash-merge it. Returns the PR number."""
    ws.git("push", "-q", "-u", "origin", branch)
    pr = _number(_gh(ws, "pr", "create", "--head", branch, "--base", ws.base_branch,
                     "--title", title, "--body", body, "--label", LABEL))
    _gh(ws, "pr", "merge", str(pr), "--squash", "--delete-branch")
    return pr
