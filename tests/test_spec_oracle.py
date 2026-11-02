"""Differential test: greenkeep.gate vs the Lean model of logic-map step 19 (`lake exe oracle`).

Diffs are generated in the shape `git diff -U0` writes them (headers, `/dev/null` sides, the tab
git appends to a path with a space, `,1` counts left out), so a disagreement is one a real run
can meet.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from greenkeep.gate import coverage_violations, is_test_path, suppressions

LEAN = Path(__file__).parent.parent / "lean"
ORACLE = LEAN / ".lake/build/bin/oracle"
# lake may live only in ~/.elan/bin, off PATH in a plain test run.
LAKE = shutil.which("lake") or str(Path.home() / ".elan/bin/lake")
pytestmark = pytest.mark.skipif(not Path(LAKE).exists(), reason="Lean (lake) is not installed")

DIRS = [
    "src",
    "pkg",
    "tests",
    "test",
    "tests_e2e",
    "tests-integration",
    "it-tests",
    "integration_tests",
    "a b",
    "testsuite",
    "attests",
]
NAMES = ["x.py", "y.py", "test_x.py", "conftest.py", "a b.py", "tests.py", "x_test.py"]
# Pieces of a source line: markers in several spellings, and characters Python's splitlines()
# treats as line ends although git does not (form feed, file separator).
PIECES = [
    "x", "y = 1", " ", "\tx", "\x0c", "\x1c", "#", "+", "++ b/", "z.py", "-", "@@", ":",
    "noqa", "NoQA", "type:", "type: ignore", " ignore", "ruff", "pylint:", " disable",
    "pragma: no cover", "pragma no cover", "pragma:nocover", "PRAGMA: NO COVER",
]  # fmt: skip

paths = st.builds(
    lambda ds, n: "/".join([*ds, n]),
    st.lists(st.sampled_from(DIRS), max_size=2),
    st.sampled_from(NAMES),
)
source_line = st.lists(st.sampled_from(PIECES), max_size=6).map("".join)


def _count(n: int) -> str:
    return "" if n == 1 else f",{n}"


def _side(prefix: str, path: str | None) -> str:
    if path is None:
        return "/dev/null"
    return f"{prefix}/{path}" + ("\t" if " " in path else "")


_MODE_LINES = {
    "new": ["new file mode 100644", "index 0000000..1111111"],
    "delete": ["deleted file mode 100644", "index 1111111..0000000"],
    "modify": ["index 1111111..2222222 100644"],
}


@st.composite
def _hunks(draw, kind: str) -> list[tuple[int, int, int, int]]:
    """(old start, old count, new start, new count) per hunk, as git writes them for this kind of change."""
    if kind == "new":
        return [(0, 0, 1, draw(st.integers(1, 3)))]
    if kind == "delete":
        return [(1, draw(st.integers(1, 3)), 0, 0)]
    shapes = st.sampled_from([(0, 1), (1, 0), (1, 1), (2, 1), (0, 2), (3, 2)])
    return [
        (draw(st.integers(0, 25)), c, draw(st.integers(1, 25)), k)
        for c, k in draw(st.lists(shapes, min_size=1, max_size=3))
    ]


@st.composite
def file_diff(draw, path: str) -> str:
    kind = draw(st.sampled_from(["modify", "modify", "modify", "new", "delete"]))
    head = [f"diff --git a/{path} b/{path}", *_MODE_LINES[kind]]
    hunks = draw(_hunks(kind))
    old = None if kind == "new" else path
    new = None if kind == "delete" else path
    head += [f"--- {_side('a', old)}", f"+++ {_side('b', new)}"]
    body = []
    for s, c, t, k in hunks:
        ctx = draw(st.sampled_from(["", " def f():"]))
        body.append(f"@@ -{s}{_count(c)} +{t}{_count(k)} @@{ctx}")
        body += ["-" + draw(source_line) for _ in range(c)]
        body += ["+" + draw(source_line) for _ in range(k)]
    return "\n".join(head + body) + "\n"


@st.composite
def diffs(draw) -> tuple[str, list[str]]:
    ps = draw(st.lists(paths, min_size=1, max_size=3, unique=True))
    return "".join(draw(file_diff(p)) for p in ps), ps


@st.composite
def coverage(draw, ps: list[str]) -> dict:
    files = {}
    for p in [*ps, "other.py"]:
        if draw(st.booleans()) or draw(st.booleans()):
            lines = draw(st.sets(st.integers(1, 30), max_size=12))
            executed = draw(st.sets(st.sampled_from(sorted(lines)))) if lines else set()
            files[p] = {
                "executed_lines": sorted(executed),
                "missing_lines": sorted(lines - executed),
            }
    return {"files": files}


@pytest.fixture(scope="module")
def oracle():
    subprocess.run([LAKE, "build", "oracle"], cwd=LEAN, check=True, capture_output=True)
    proc = subprocess.Popen([ORACLE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    yield proc
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait()


def _codes(s: str) -> list[int]:
    return [ord(c) for c in s]


def ask(oracle, tag: str, nums: list[int]) -> list[int]:
    oracle.stdin.write(f"{tag} {' '.join(map(str, nums))}\n")
    oracle.stdin.flush()
    reply = oracle.stdout.readline()
    assert not reply.startswith("error"), reply
    return [int(x) for x in reply.split()]


def _strings(nums: list[int]) -> list[str]:
    out, i = [], 0
    while i < len(nums):
        n = nums[i]
        out.append("".join(map(chr, nums[i + 1 : i + 1 + n])))
        i += 1 + n
    return out


def model_suppressions(oracle, diff: str) -> list[str]:
    # the code returns each line stripped; the model returns it as added
    return [t.strip() for t in _strings(ask(oracle, "S", _codes(diff)))]


def model_violations(oracle, diff: str, cov: dict) -> list[str]:
    nums = [len(diff), *_codes(diff), len(cov["files"])]
    for p, data in cov["files"].items():
        e, m = data["executed_lines"], data["missing_lines"]
        nums += [len(p), *_codes(p), len(e), *e, len(m), *m]
    reply, out, i = ask(oracle, "C", nums), set(), 0
    while i < len(reply):
        n = reply[i]
        path = "".join(map(chr, reply[i + 1 : i + 1 + n]))
        flag, line = reply[i + 1 + n], reply[i + 2 + n]
        out.add(f"{path}:{line}" if flag else f"{path} (not measured)")
        i += 3 + n
    return sorted(out)  # the model lists per hunk; the code merges a file's lines into a set


@settings(max_examples=500)
@given(d=diffs())
def test_suppressions_match_model(oracle, d):
    diff, _ = d
    assert suppressions(diff) == model_suppressions(oracle, diff)


@settings(max_examples=500)
@given(data=st.data())
def test_coverage_violations_match_model(oracle, data):
    diff, ps = data.draw(diffs())
    cov = data.draw(coverage(ps))
    code = coverage_violations(diff, cov)
    assert len(set(code)) == len(code)
    assert sorted(code) == model_violations(oracle, diff, cov)


@settings(max_examples=300)
@given(path=paths)
def test_is_test_path_matches_model(oracle, path):
    assert is_test_path(path) == (ask(oracle, "T", _codes(path)) == [1])


# Counterexamples the model found (PLAN.md, «Lean: шаг 19 Gate», L1–L4, L6), re-minimised by hand into
# realistic diffs: each one broke the code before its fix.
COV_A_B = {"files": {"src/a b.py": {"executed_lines": [1, 2, 3], "missing_lines": []}}}
COV_SRC = {
    "files": {
        "src/a.py": {"executed_lines": [1, 2, 3], "missing_lines": [20]},
        "src/old.py": {"executed_lines": [], "missing_lines": [1, 2]},
    }
}
PINS = {
    "A": "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -2 +2 @@\n-x = 1\n+x = 1\x0c  # noqa\n",
    "B": "diff --git a/src/a b.py b/src/a b.py\n--- a/src/a b.py\t\n+++ b/src/a b.py\t\n"
    "@@ -2 +2 @@\n-x = 1\n+x = 2\n",
    "C": "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -1,0 +2 @@\n+++ b/tests/t.py\n@@ -20 +21 @@\n-    return 1\n+    return 2\n",
    "C2": "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -2 +2 @@\n-x = 1\n+++x  # noqa\n",
    "D": "diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n--- a/src/old.py\n"
    "+++ /dev/null\n@@ -1,2 +0,0 @@\n-def f():\n-    return 1\n",
}


def test_pin_a_form_feed_hides_noqa(oracle):
    assert suppressions(PINS["A"]) == model_suppressions(oracle, PINS["A"])


def test_pin_b_path_with_space_never_measured(oracle):
    code = coverage_violations(PINS["B"], COV_A_B)
    assert sorted(code) == model_violations(oracle, PINS["B"], COV_A_B)


def test_pin_c_added_header_line_moves_later_hunks_to_a_test_file(oracle):
    code = coverage_violations(PINS["C"], COV_SRC)
    assert sorted(code) == model_violations(oracle, PINS["C"], COV_SRC)


def test_pin_c_added_line_starting_with_plus_plus_hides_noqa(oracle):
    assert suppressions(PINS["C2"]) == model_suppressions(oracle, PINS["C2"])


def test_pin_d_deleted_uncovered_file_passes(oracle):
    code = coverage_violations(PINS["D"], COV_SRC)
    assert sorted(code) == model_violations(oracle, PINS["D"], COV_SRC)


@pytest.mark.parametrize("marker", ["# pragma no cover", "#pragma:nocover", "# PRAGMA NO COVER"])
def test_pin_l6_coverage_py_spellings_of_no_cover_are_suppressions(oracle, marker):
    diff = f"diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -2 +2 @@\n-x = 1\n+x = 1  {marker}\n"
    assert suppressions(diff) == model_suppressions(oracle, diff) == [f"x = 1  {marker}"]
