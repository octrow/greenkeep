from greenkeep.gate import changed_old_lines, coverage_violations, is_test_path

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
    cov = {"files": {"pkg/a.py": {"missing_lines": [11, 30]}}}
    assert coverage_violations(DIFF, cov) == ["pkg/a.py:11"]


def test_coverage_violations_flags_unmeasured_source_file_but_not_tests():
    assert coverage_violations(DIFF, {"files": {}}) == ["pkg/a.py (not measured)"]


def test_is_test_path():
    assert is_test_path("tests/test_a.py") and is_test_path("pkg/tests/conftest.py")
    assert is_test_path("test_top.py") and not is_test_path("pkg/a.py")


def test_line_is_covered():
    from greenkeep.gate import line_is_covered
    cov = {"files": {"pkg/a.py": {"missing_lines": [3]}}}
    assert line_is_covered(cov, "pkg/a.py", 2)
    assert not line_is_covered(cov, "pkg/a.py", 3)
    assert not line_is_covered(cov, "pkg/b.py", 1)
    assert line_is_covered(cov, "tests/test_a.py", 1)
