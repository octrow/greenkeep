"""Gate rules that are pure functions of a diff and the baseline coverage."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
FILE = re.compile(r"^\+\+\+ b/(.+)$")


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return "tests" in p.parts or "test" in p.parts or p.name.startswith("test_") \
        or p.name == "conftest.py"


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
        missing = set(files[path].get("missing_lines", []))
        bad.extend(f"{path}:{n}" for n in sorted(lines & missing))
    return bad


def line_is_covered(cov: dict, path: str, line: int) -> bool:
    """Whether a fix at this line could pass the coverage rule at all."""
    if is_test_path(path):
        return True
    data = cov.get("files", {}).get(path)
    return data is not None and line not in data.get("missing_lines", [])
