"""Findings: concrete problems reported by deterministic tools (see CONTEXT.md)."""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .gate import is_test_path

# an unused `noqa` with words after its codes: ruff's own fix drops the words too, the
# patcher keeps them as a plain comment
NOQA_WITH_NOTE = re.compile(r"#\s*noqa(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?\s+\S")
VENDOR_DIRS = frozenset({"vendor", "_vendor", "vendored", "third_party"})

# a module is "untested" below this coverage, if it is big enough to matter
UNTESTED_PERCENT = 50.0
UNTESTED_MIN_STATEMENTS = 5
# lower rank = worked on first; pyflakes (F) are real bugs, style comes last
# complexity (C90, PLR) before simplify/perf/pyupgrade: a boost finds dozens of those
RULE_RANK = {"S": 0, "F": 1, "B": 2, "C": 3, "PLR": 3, "E": 5, "W": 6}
DEFAULT_RANK = 4


def rank(rule: str) -> int:
    # the whole letter prefix: SIM102 is simplify, not S (security)
    return RULE_RANK.get(re.match(r"[A-Z]*", rule).group(), DEFAULT_RANK)


@dataclass(frozen=True)
class Finding:
    kind: str  # "ruff" | "untested"
    rule: str
    path: str
    line: int
    message: str
    fingerprint: str
    fixable: bool = False  # ruff can fix it deterministically, no LLM needed
    title: str = ""  # issue, PR and commit subject
    end_line: int = 0  # the last line ruff's range reaches; 0: just `line`


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def ruff_findings(
    items: list[dict], *, root: str, read_line: Callable[[str, int], str]
) -> list[Finding]:
    """Ruff JSON output -> findings, most severe rule families first."""
    found, seen = [], Counter()
    for it in sorted(items, key=lambda it: (it["filename"], it["location"]["row"])):
        path = os.path.relpath(it["filename"], root)
        row = it["location"]["row"]
        text = read_line(path, row)
        fp = f"ruff:{it['code']}:{path}:{_digest(text.strip())}"
        # identical lines (8 × `except Exception:`) are separate findings; the first keeps
        # the plain fingerprint, so state from before the index stays valid
        seen[fp] += 1
        fp += f"#{seen[fp] - 1}" if seen[fp] > 1 else ""
        fix = it.get("fix") or {}
        noted = it["code"] == "RUF100" and NOQA_WITH_NOTE.search(text)
        found.append(
            Finding(
                "ruff",
                it["code"],
                path,
                row,
                it["message"],
                fp,
                fixable=fix.get("applicability") == "safe" and not noted,
                title=f"{it['code']}: {it['message']} ({path}:{row})",
                end_line=it.get("end_location", {}).get("row", row),
            )
        )
    return sorted(found, key=lambda f: rank(f.rule))


def untested_findings(cov: dict) -> list[Finding]:
    """Coverage JSON -> one finding per sizeable, poorly covered source module."""
    found = []
    for path, data in sorted(cov.get("files", {}).items()):
        s = data["summary"]
        if is_test_path(path) or s["num_statements"] < UNTESTED_MIN_STATEMENTS:
            continue
        if s["percent_covered"] < UNTESTED_PERCENT:
            msg = f"{s['percent_covered']:.0f}% covered"
            found.append(
                Finding(
                    "untested",
                    "coverage",
                    path,
                    1,
                    msg,
                    f"untested:{path}",
                    title=f"Add tests for {path}",
                )
            )
    return found


def is_vendored(root: Path, path: str) -> bool:
    """Code copied from another project (a vendor folder, or a VENDORED.md beside it or
    above it): its fixes and tests belong upstream."""
    folders = list(PurePosixPath(path).parents)[:-1]  # the repo root's own note does not count
    return bool(VENDOR_DIRS & set(PurePosixPath(path).parts[:-1])) or any(
        (root / d / "VENDORED.md").is_file() for d in folders
    )
