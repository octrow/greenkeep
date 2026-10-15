"""Findings: concrete problems reported by deterministic tools (see CONTEXT.md)."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass

from .gate import is_test_path

# a module is "untested" below this coverage, if it is big enough to matter
UNTESTED_PERCENT = 50.0
UNTESTED_MIN_STATEMENTS = 5
# lower rank = worked on first; pyflakes (F) are real bugs, style comes last
RULE_RANK = {"S": 0, "F": 1, "B": 2, "E": 5, "W": 6}
DEFAULT_RANK = 4


def rank(rule: str) -> int:
    return RULE_RANK.get(rule[:1], DEFAULT_RANK)


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


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def ruff_findings(items: list[dict], *, root: str,
                  read_line: Callable[[str, int], str]) -> list[Finding]:
    """Ruff JSON output -> findings, most severe rule families first."""
    found = []
    for it in items:
        path = os.path.relpath(it["filename"], root)
        row = it["location"]["row"]
        fp = f"ruff:{it['code']}:{path}:{_digest(read_line(path, row).strip())}"
        fix = it.get("fix") or {}
        found.append(Finding("ruff", it["code"], path, row, it["message"], fp,
                             fixable=fix.get("applicability") == "safe",
                             title=f"{it['code']}: {it['message']} ({path})"))
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
            found.append(Finding("untested", "coverage", path, 1, msg, f"untested:{path}",
                                 title=f"Add tests for {path}"))
    return found
