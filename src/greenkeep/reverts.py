"""Auto-pause: a reverted greenkeep merge means the gate let something wrong through, so
greenkeep stops until the owner has looked (see CONTEXT.md, Auto-pause)."""

from __future__ import annotations

import re

from . import github
from .state import State
from .workspace import Workspace

# what `git revert` and GitHub's "Revert" button write into the commit body
REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


class AutoPause(Exception):
    """A greenkeep merge was reverted; the run must stop."""


def reverted(log: str, shas: set[str]) -> set[str]:
    """The merge commits among `shas` that some commit in `log` reverts."""
    named = REVERTS.findall(log)
    return {s for s in shas if any(s.startswith(n) or n.startswith(s) for n in named)}


def check(ws: Workspace, state: State, *, backfill: bool) -> None:
    """Raise AutoPause if the clone's history reverts any greenkeep merge in this repo.

    `backfill` (push mode) asks GitHub for merge commits recorded before they were stored.
    """
    merges = {}
    for fp, pr, sha in state.merges(ws.name):
        if not sha and pr and backfill:
            sha = github.merge_sha(ws, pr)
            state.set(ws.name, fp, sha=sha)
        if sha:
            merges[sha] = (fp, pr)
    if not merges:
        return
    hit = reverted(ws.git("log", "--format=%B", ws.base_branch), set(merges))
    for sha in hit:
        state.set(ws.name, merges[sha][0], status="reverted")
    if hit:
        prs = ", ".join(f"#{merges[s][1]}" for s in sorted(hit))
        raise AutoPause(f"{ws.name}: greenkeep merge {prs} was reverted")
