"""Gate rules that are pure functions of a diff and the baseline coverage."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
FILE = re.compile(r"^\+\+\+ b/(.+)$")


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        "tests" in p.parts
        or "test" in p.parts
        or p.name.startswith("test_")
        or p.name == "conftest.py"
    )


def changed_old_lines(diff: str) -> dict[str, set[int]]:
    """Baseline line numbers each hunk of a `git diff -U0` touches.

    A pure insertion (`-N,0`) touches the line it lands after: if that line was never
    executed, the new code sits in an untested spot too.
    """
    out: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if m := FILE.match(line):
            current = out.setdefault(m.group(1), set())
        elif (m := HUNK.match(line)) and current is not None:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            current.update(range(start, start + count) if count else {max(start, 1)})
    return out


def _guards(statements: list[int], line: int) -> set[int]:
    """The statements a change at `line` lives in or next to.

    Coverage only lists the first line of each statement, so a continuation line, a
    comment or a blank line belongs to the statement that starts before it, and an
    insertion there also reaches the statement that follows. Both must have run.
    """
    before = [s for s in statements if s <= line]
    after = [s for s in statements if s >= line]
    return {
        g for g in (before[-1] if before else None, after[0] if after else None) if g is not None
    }


def line_is_covered(cov: dict, path: str, line: int) -> bool:
    """Whether a change at this line could pass the coverage rule at all."""
    if is_test_path(path):
        return True
    data = cov.get("files", {}).get(path)
    if data is None:
        return False
    executed = set(data.get("executed_lines", []))
    guards = _guards(sorted(executed | set(data.get("missing_lines", []))), line)
    return bool(guards) and guards <= executed


def coverage_violations(diff: str, baseline_cov: dict) -> list[str]:
    """Changed source lines that the tests did not execute before the change."""
    files = baseline_cov.get("files", {})
    bad = []
    for path, lines in changed_old_lines(diff).items():
        if is_test_path(path):
            continue  # test code is checked by the suite passing, not by coverage
        if path not in files:
            bad.append(f"{path} (not measured)")
            continue
        bad.extend(
            f"{path}:{n}" for n in sorted(lines) if not line_is_covered(baseline_cov, path, n)
        )
    return bad


SUPPRESSION = re.compile(
    r"noqa|ruff\s*:|type:\s*ignore|pylint:\s*disable|pragma:\s*no cover", re.IGNORECASE
)


def suppressions(diff: str) -> list[str]:
    """Added lines that silence a tool instead of fixing the code."""
    return [
        ln[1:].strip()
        for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++") and SUPPRESSION.search(ln)
    ]
