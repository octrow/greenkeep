"""Reverted merges: a greenkeep merge someone reverted, or one after which CI on the base
branch went red (greenkeep then reverts it itself), means the gate let something wrong
through. The finding is marked `reverted` and never tried again; the caller cools the repo
down (see CONTEXT.md, Revert)."""

from __future__ import annotations

import logging
import re

from . import github
from .state import State
from .workspace import Workspace

log = logging.getLogger(__name__)

# what `git revert` and GitHub's "Revert" button write into the commit body
REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def reverted(log_text: str, shas: set[str]) -> set[str]:
    """The merge commits among `shas` that some commit in `log_text` reverts."""
    named = REVERTS.findall(log_text)
    return {s for s in shas if any(s.startswith(n) or n.startswith(s) for n in named)}


def broke_main(ws: Workspace, sha: str) -> bool:
    """CI failed on the merge commit but not on the commit before it, and not because
    GitHub refused to start it (billing)."""
    return (
        github.ci_red(ws, sha)
        and not github.ci_red(ws, ws.git("rev-parse", f"{sha}^").strip())
        and not github.ci_not_started(ws, sha)
    )


def _revert(ws: Workspace, sha: str, pr: int | None) -> int:
    """A revert PR, merged: no model writes it, `git revert` does."""
    branch = f"greenkeep/revert-{sha[:10]}"
    ws.git("checkout", "-q", "-B", branch, ws.base_branch)
    ws.git("revert", "--no-edit", sha)  # a conflict raises: the repo fails this run
    return github.publish(
        ws,
        branch,
        f"revert: greenkeep merge #{pr}, CI on {ws.base_branch} went red after it",
        f"CI failed on {sha} and passed on the commit before it, so greenkeep takes "
        f"its change back. The finding is not tried again.\n\nReverts #{pr}",
    )


def check(ws: Workspace, state: State, *, push: bool) -> list[str]:
    """One digest line per greenkeep merge reverted since the last run; each is marked
    `reverted`. Push mode backfills merge commits stored without a sha and reverts a merge
    that turned CI red; a dry run says it would."""
    merges = {}
    for fp, pr, sha in state.merges(ws.name):
        if not sha and pr and push:
            sha = github.merge_sha(ws, pr)
            state.set(ws.name, fp, sha=sha)
        if sha:
            merges[sha] = (fp, pr)
    lines = []
    for sha in sorted(reverted(ws.git("log", "--format=%B", ws.base_branch), set(merges))):
        fp, pr = merges[sha]
        state.set(ws.name, fp, status="reverted")
        lines.append(f"{ws.name}: greenkeep merge #{pr} was reverted")
    for fp, pr, sha in state.watched(ws.name):
        if not broke_main(ws, sha):
            continue
        what = f"{ws.name}: CI on {ws.base_branch} went red after greenkeep merge #{pr}"
        if not push:
            lines.append(f"{what}; would revert")
            continue
        lines.append(f"{what}; reverted in #{_revert(ws, sha, pr)}")
        state.set(ws.name, fp, status="reverted")
    for line in lines:
        log.warning(line)
    return lines
