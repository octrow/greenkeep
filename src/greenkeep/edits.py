"""LLM replies -> file edits. SEARCH/REPLACE blocks, not unified diffs: weak models
mangle hunk headers, but they copy a few lines of code back reliably."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .gate import is_test_path

BLOCK = re.compile(
    r"^(?P<path>[^\n<>=]+?)\s*\n<<<<<<< SEARCH\n(?P<search>.*?)\n?=======\n"
    r"(?P<replace>.*?)\n?>>>>>>> REPLACE",
    re.DOTALL | re.MULTILINE,
)
# greedy, to the LAST fence: the one block we ask for may itself contain ``` in strings
CODE = re.compile(r"```(?:python|py)?\n(.*)```", re.DOTALL)


class EditError(ValueError):
    """The reply cannot be applied as-is; the attempt fails."""


@dataclass(frozen=True)
class Edit:
    path: str
    search: str
    replace: str


def parse_edits(reply: str) -> list[Edit]:
    edits = [
        Edit(m["path"].strip().strip("`"), m["search"], m["replace"]) for m in BLOCK.finditer(reply)
    ]
    if not edits:
        raise EditError("no SEARCH/REPLACE blocks in reply")
    return edits


def _target(root: Path, rel: str, allowed: frozenset[str], *, new: bool = False) -> Path:
    """Only files git tracks: never .git, .venv, untracked or generated files. `new`: a
    test file that does not exist yet."""
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        raise EditError(f"path outside repo: {rel}")
    clean = target.relative_to(root.resolve()).as_posix()
    if new and is_test_path(clean) and not clean.startswith(".") and not target.exists():
        return target  # not under .git/.venv: a reset keeps those, the gate never sees them
    if clean not in allowed:
        raise EditError(f"not a tracked file the patch may edit: {rel}")
    return target


def apply_edits(
    root: Path, edits: list[Edit], allowed: frozenset[str], *, new_tests: bool = False
) -> list[str]:
    """Apply every edit or raise; each non-empty SEARCH must occur exactly once.
    `new_tests`: an empty SEARCH may create a test file that does not exist yet."""
    touched = []
    for e in edits:
        if not e.search.strip():
            if not new_tests:
                raise EditError(f"empty SEARCH for {e.path}")
            target = _target(root, e.path, allowed, new=True)
            if target.exists():
                raise EditError(f"empty SEARCH for an existing file: {e.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(e.replace + "\n")
            touched.append(e.path)
            continue
        target = _target(root, e.path, allowed)
        try:
            text = target.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise EditError(f"cannot read {e.path}: {exc}") from exc
        if text.count(e.search) != 1:
            raise EditError(f"SEARCH matches {text.count(e.search)} times in {e.path}")
        target.write_text(text.replace(e.search, e.replace))
        touched.append(e.path)
    return touched


def python_block(reply: str) -> str:
    if m := CODE.search(reply):
        return m.group(1)
    raise EditError("no python code block in reply")
