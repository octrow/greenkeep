from greenkeep.gate import (
    changed_old_lines,
    coverage_violations,
    is_test_path,
    line_is_covered,
    suppressions,
)

DIFF = """\
diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1 +0,0 @@
-import os
@@ -10,2 +9,3 @@ def f():
-    x = 1
-    y = 2
+    x = 1
+    y = 2
+    z = 3
@@ -20,0 +20 @@ def g():
+    pass
diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -5 +5 @@
-    assert 1
+    assert True
"""


def test_changed_old_lines_maps_hunks_to_baseline_lines():
    assert changed_old_lines(DIFF) == {"pkg/a.py": {1, 10, 11, 20}, "tests/test_a.py": {5}}


def test_coverage_violations_flags_lines_missing_in_baseline():
    cov = {"files": {"pkg/a.py": {"executed_lines": [1, 10, 20, 21], "missing_lines": [11]}}}
    assert coverage_violations(DIFF, cov) == ["pkg/a.py:11"]


def test_insertion_next_to_untested_statement_is_a_violation():
    # line 20 is a comment between an executed statement (19) and a missing one (22)
    cov = {"files": {"pkg/a.py": {"executed_lines": [1, 10, 11, 19], "missing_lines": [22]}}}
    assert coverage_violations(DIFF, cov) == ["pkg/a.py:20"]


def test_file_without_statements_cannot_be_changed():
    cov = {"files": {"pkg/a.py": {"executed_lines": [], "missing_lines": []}}}
    assert coverage_violations(DIFF, cov) == ["pkg/a.py:1", "pkg/a.py:10", "pkg/a.py:11",
                                              "pkg/a.py:20"]


def test_coverage_violations_flags_unmeasured_source_file_but_not_tests():
    assert coverage_violations(DIFF, {"files": {}}) == ["pkg/a.py (not measured)"]


def test_is_test_path():
    assert is_test_path("tests/test_a.py") and is_test_path("pkg/tests/conftest.py")
    assert is_test_path("test_top.py") and not is_test_path("pkg/a.py")


def test_line_is_covered_uses_enclosing_and_next_statement():
    cov = {"files": {"pkg/a.py": {"executed_lines": [1, 2, 6], "missing_lines": [3]}}}
    assert line_is_covered(cov, "pkg/a.py", 2)
    assert not line_is_covered(cov, "pkg/a.py", 3)
    assert not line_is_covered(cov, "pkg/a.py", 4)  # continuation of missing statement 3
    assert not line_is_covered(cov, "pkg/b.py", 1)
    assert line_is_covered(cov, "tests/test_a.py", 1)


def test_suppressions_catch_noqa_and_friends():
    diff = "+++ b/a.py\n+import os  # noqa: F401\n+x = 1\n-y  # noqa\n+# ruff: noqa\n"
    assert suppressions(diff) == ["import os  # noqa: F401", "# ruff: noqa"]
