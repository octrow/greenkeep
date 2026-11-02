"""Differential test: greenkeep.gate vs the Lean model of logic-map step 19 (`lake exe oracle`).

Diffs are generated in the shape `git diff -U0` writes them (headers, `/dev/null` sides, the tab
git appends to a path with a space, `,1` counts left out), so a disagreement is one a real run
can meet.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import event, given, settings
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
    "pragma: no cover", "pragma no cover", "PRAGMA: NO COVER",
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


@st.composite
def file_diff(draw, path: str) -> str:
    kind = draw(st.sampled_from(["modify", "modify", "modify", "new", "delete"]))
    head = [f"diff --git a/{path} b/{path}"]
    if kind == "new":
        k = draw(st.integers(1, 3))
        head += ["new file mode 100644", "index 0000000..1111111"]
        hunks = [(0, 0, 1, k)]
    elif kind == "delete":
        c = draw(st.integers(1, 3))
        head += ["deleted file mode 100644", "index 1111111..0000000"]
        hunks = [(1, c, 0, 0)]
    else:
        head += ["index 1111111..2222222 100644"]
        hunks = []
        for _ in range(draw(st.integers(1, 3))):
            c, k = draw(st.sampled_from([(0, 1), (1, 0), (1, 1), (2, 1), (0, 2), (3, 2)]))
            hunks.append((draw(st.integers(0, 25)), c, draw(st.integers(1, 25)), k))
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


# Known findings (map mode: the code stays as it is; PLAN.md, section «Lean: шаг 19», L1–L4). Each mask accepts a
# disagreement only when the code equals the model on the input as the code reads it.
PY_ONLY_BREAKS = (
    "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\r"  # str.splitlines() ends a line there, git does not
)


def _as_python_splits(diff: str) -> str:
    return "".join("\n" if c in PY_ONLY_BREAKS else c for c in diff)


KEPT = "\x01"  # the tab the code keeps in a path; the model drops a final tab, keeps this


FINDING_A = "#L1 splitlines() breaks a diff line at a form feed: a marker or a header escapes"
FINDING_B = (
    "#L2 the tab git appends to a path with a space stays in it: never measured, never a test"
)
FINDING_C = "#L3 an added line starting with ++ is read as a +++ header: skipped, or a new file"
FINDING_D = "#L4 a deleted file's lines go to the file before it, or nowhere when it comes first"


def _spoofs(diff: str) -> list[int]:
    """Indexes of added lines whose text starts with `++`: they read `+++` inside a hunk."""
    hits, in_hunk = [], False
    for i, ln in enumerate(diff.split("\n")):
        if ln.startswith("diff --git "):
            in_hunk = False
        elif ln.startswith("@@ "):
            in_hunk = True
        elif in_hunk and ln.startswith("+++"):
            hits.append(i)
    return hits


def _suppression_reading(diff: str) -> tuple[str, list[str]]:
    """The diff as `suppressions` reads it, and the known findings that make it differ."""
    names = []
    if any(c in diff for c in PY_ONLY_BREAKS):
        diff, names = _as_python_splits(diff), [FINDING_A]
    if spoofs := _spoofs(diff):
        lines = diff.split("\n")
        diff = "\n".join(ln for i, ln in enumerate(lines) if i not in spoofs)
        names.append(FINDING_C)
    return diff, names


def _coverage_reading(diff: str, cov: dict) -> tuple[str, dict, list[str], list[str]]:
    """The diff and coverage as `coverage_violations` reads them, the findings involved, and the
    files the code makes up from a spoofed header (it lists a file even with no hunk after it)."""
    names, phantoms = [], []
    if any(c in diff for c in PY_ONLY_BREAKS):
        diff, names = _as_python_splits(diff), [FINDING_A]
    if spoofs := _spoofs(diff):
        lines = diff.split("\n")
        for i in spoofs:
            if m := re.match(r"^\+\+\+ b/(.+)$", lines[i]):
                lines[i] = f"diff --git a/{m[1]} b/{m[1]}\n{lines[i]}"
                names.append(FINDING_C)
                phantoms.append(m[1])
        diff = "\n".join(lines)
    if "+++ /dev/null\n" in diff:
        diff, names = _deleted_as_code_reads(diff), [*names, FINDING_D]
    if "\t\n" in diff:  # the code keeps the tab: give the model a path char it keeps too
        diff = re.sub(r"^((?:---|\+\+\+) [ab]/.*)\t$", "\\1" + KEPT, diff, flags=re.MULTILINE)
        names.append(FINDING_B)
    return diff, cov, names, phantoms


def _deleted_as_code_reads(diff: str) -> str:
    """A deleted file's `--- a/P` pointed at the file the code still has open (or /dev/null)."""
    lines, current = diff.split("\n"), None
    for i, ln in enumerate(lines):
        if m := re.match(r"^\+\+\+ b/(.+)$", ln):
            current = m[1]
        elif ln == "+++ /dev/null" and lines[i - 1].startswith("--- a/"):
            lines[i - 1] = f"--- a/{current}" if current else "--- /dev/null"
    return "\n".join(lines)


def known_suppression_finding(oracle, diff, code) -> str | None:
    seen, names = _suppression_reading(diff)
    return " + ".join(names) if names and code == model_suppressions(oracle, seen) else None


def known_coverage_finding(oracle, diff, cov, code) -> str | None:
    seen, seen_cov, names, phantoms = _coverage_reading(diff, cov)
    if FINDING_B in names:  # only a path's final tab, the one git appended
        code = [re.sub(r"\t( \(not measured\)|:\d+)$", KEPT + r"\1", c) for c in code]
    code = sorted(code)
    model = set(model_violations(oracle, seen, seen_cov))
    model |= {
        f"{p} (not measured)"
        for p in phantoms
        if p not in seen_cov["files"] and ask(oracle, "T", _codes(p)) == [0]
    }
    return " + ".join(names) if names and code == sorted(model) else None


@settings(max_examples=500)
@given(d=diffs())
def test_suppressions_match_model(oracle, d):
    diff, _ = d
    code, model = suppressions(diff), model_suppressions(oracle, diff)
    if code != model:
        name = known_suppression_finding(oracle, diff, code)
        assert name, f"new disagreement: code {code}, model {model}"
        event(name)


@settings(max_examples=500)
@given(data=st.data())
def test_coverage_violations_match_model(oracle, data):
    diff, ps = data.draw(diffs())
    cov = data.draw(coverage(ps))
    code = coverage_violations(diff, cov)
    assert len(set(code)) == len(code)
    model = model_violations(oracle, diff, cov)
    if sorted(code) != model:
        name = known_coverage_finding(oracle, diff, cov, code)
        assert name, f"new disagreement: code {sorted(code)}, model {model}"
        event(name)


@settings(max_examples=300)
@given(path=paths)
def test_is_test_path_matches_model(oracle, path):
    assert is_test_path(path) == (ask(oracle, "T", _codes(path)) == [1])


# Pinned counterexamples, one per finding, re-minimised by hand into realistic diffs. Strict xfail:
# when the code is fixed the pin passes, the xfail fails, and the pin and its mask go together.
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


@pytest.mark.xfail(strict=True, reason=FINDING_A)
def test_pin_a_form_feed_hides_noqa(oracle):
    assert suppressions(PINS["A"]) == model_suppressions(oracle, PINS["A"])


@pytest.mark.xfail(strict=True, reason=FINDING_B)
def test_pin_b_path_with_space_never_measured(oracle):
    code = coverage_violations(PINS["B"], COV_A_B)
    assert sorted(code) == model_violations(oracle, PINS["B"], COV_A_B)


@pytest.mark.xfail(strict=True, reason=FINDING_C)
def test_pin_c_added_header_line_moves_later_hunks_to_a_test_file(oracle):
    code = coverage_violations(PINS["C"], COV_SRC)
    assert sorted(code) == model_violations(oracle, PINS["C"], COV_SRC)


@pytest.mark.xfail(strict=True, reason=FINDING_C)
def test_pin_c_added_line_starting_with_plus_plus_hides_noqa(oracle):
    assert suppressions(PINS["C2"]) == model_suppressions(oracle, PINS["C2"])


@pytest.mark.xfail(strict=True, reason=FINDING_D)
def test_pin_d_deleted_uncovered_file_passes(oracle):
    code = coverage_violations(PINS["D"], COV_SRC)
    assert sorted(code) == model_violations(oracle, PINS["D"], COV_SRC)
