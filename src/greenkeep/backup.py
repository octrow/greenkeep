"""A copy of state.db off the VM (PLAN.md B1): after each scheduled run, its SQL dump is
committed to a local checkout of a private repo and pushed. Text, so git keeps small deltas.
Restore: `sqlite3 ~/.local/share/greenkeep/state.db < state.sql`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .state import State
from .workspace import sh

DUMP = "state.sql"


def save(state: State, checkout: Path) -> None:
    """Commit the dump when it changed, and push; git failures raise WorkspaceError."""
    (checkout / DUMP).write_text("\n".join(state.db.iterdump()) + "\n")
    sh(["git", "add", DUMP], checkout)
    if sh(["git", "diff", "--cached", "--quiet"], checkout, check=False).returncode:
        sh(["git", "commit", "-q", "-m", f"state {date.today()}"], checkout)
    sh(["git", "push", "-q", "origin", "HEAD"], checkout)  # also a commit an offline run left
