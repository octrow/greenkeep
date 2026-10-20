from greenkeep.findings import ruff_findings, untested_findings

RUFF = [
    {"code": "F401", "message": "`os` imported but unused", "filename": "/w/pkg/a.py",
     "location": {"row": 1, "column": 8}},
    {"code": "E501", "message": "Line too long", "filename": "/w/pkg/a.py",
     "location": {"row": 3, "column": 1}},
]


def test_ruff_findings_relative_path_and_stable_fingerprint():
    lines = {"pkg/a.py": ["import os", "", "x = 1"]}
    found = ruff_findings(RUFF, root="/w", read_line=lambda p, n: lines[p][n - 1])
    assert [f.rule for f in found] == ["F401", "E501"]
    assert found[0].path == "pkg/a.py" and found[0].line == 1
    # fingerprint ignores line numbers so it survives code moving around
    moved = ruff_findings([{**RUFF[0], "location": {"row": 9, "column": 8}}], root="/w",
                          read_line=lambda p, n: "import os")
    assert moved[0].fingerprint == found[0].fingerprint


def test_ruff_findings_ranks_pyflakes_before_style():
    found = ruff_findings(list(reversed(RUFF)), root="/w", read_line=lambda p, n: "x")
    assert found[0].rule == "F401"


def test_untested_findings_skips_tests_small_and_covered_files():
    cov = {"files": {
        "pkg/core.py": {"summary": {"num_statements": 40, "percent_covered": 10.0}},
        "pkg/tiny.py": {"summary": {"num_statements": 3, "percent_covered": 0.0}},
        "pkg/good.py": {"summary": {"num_statements": 40, "percent_covered": 90.0}},
        "tests/test_x.py": {"summary": {"num_statements": 40, "percent_covered": 0.0}},
    }}
    found = untested_findings(cov)
    assert [f.path for f in found] == ["pkg/core.py"]
    assert found[0].kind == "untested"


def test_ruff_safe_fix_marks_finding_fixable():
    item = {**RUFF[0], "fix": {"applicability": "safe"}}
    assert ruff_findings([item], root="/w", read_line=lambda p, n: "x")[0].fixable
    unsafe = {**RUFF[0], "fix": {"applicability": "unsafe"}}
    assert not ruff_findings([unsafe], root="/w", read_line=lambda p, n: "x")[0].fixable


def test_same_rule_twice_in_one_file_gets_distinct_titles():
    s110 = {"code": "S110", "message": "`try`-`except`-`pass` detected", "filename": "/w/u.py"}
    items = [{**s110, "location": {"row": n, "column": 5}} for n in (4, 12)]
    found = ruff_findings(items, root="/w", read_line=lambda p, n: "pass")
    assert [f.title for f in found] == ["S110: `try`-`except`-`pass` detected (u.py:4)",
                                        "S110: `try`-`except`-`pass` detected (u.py:12)"]
