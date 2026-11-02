"""Gate rules that are pure functions of a diff and the baseline coverage."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def _test_dir(name: str) -> bool:
    """tests/, test/ and their variants: tests_e2e/, tests-integration/, integration_tests/."""
    return (
        name in ("tests", "test")
        or name.startswith(("tests_", "tests-"))
        or name.endswith(("_tests", "-tests"))
    )


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        any(_test_dir(d) for d in p.parts[:-1])
        or p.name.startswith("test_")
        or p.name == "conftest.py"
    )


def _header_path(side: str, rest: str) -> str | None:
    """`a/P` or `b/P` → P without the tab git appends to a path with a space; `/dev/null` → None."""
    if not rest.startswith(f"{side}/"):
        return None
    path = rest[2:]
    return path.removesuffix("\t")


def _parse(diff: str) -> tuple[list[tuple[str, set[int]]], list[str]]:
    """(path, baseline lines) per hunk of a `git diff -U0`, and the added lines without their `+`.

    git ends a diff line only at `\n`. `---`/`+++` are headers only before a file's first hunk
    (inside one, `+++x` is an added line `++x`). A hunk belongs to the new path, or for a deleted
    file (`+++ /dev/null`) to the old one. A pure insertion (`-N,0`) touches the line it lands
    after: if that line was never executed, the new code sits in an untested spot too.
    """
    hunks: list[tuple[str, set[int]]] = []
    added: list[str] = []
    in_hunk, old, new = False, None, None
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            in_hunk, old, new = False, None, None
        elif m := HUNK.match(line):
            in_hunk = True
            path = new if new is not None else old
            if path is not None:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                hunks.append((path, set(range(start, start + count)) if count else {max(start, 1)}))
        elif in_hunk:
            if line.startswith("+"):
                added.append(line[1:])
        elif line.startswith("--- "):
            old = _header_path("a", line[4:])
        elif line.startswith("+++ "):
            new = _header_path("b", line[4:])
    return hunks, added


def changed_old_lines(diff: str) -> dict[str, set[int]]:
    """Baseline line numbers each file's hunks touch."""
    out: dict[str, set[int]] = {}
    for path, lines in _parse(diff)[0]:
        out.setdefault(path, set()).update(lines)
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
    # coverage.py's own exclusion also takes `pragma no cover` and `pragma:nocover`
    r"noqa|ruff\s*:|type:\s*ignore|pylint:\s*disable|pragma:?\s*no\s*cover",
    re.IGNORECASE,
)


def suppressions(diff: str) -> list[str]:
    """Added lines that silence a tool instead of fixing the code."""
    return [t.strip() for t in _parse(diff)[1] if SUPPRESSION.search(t)]
