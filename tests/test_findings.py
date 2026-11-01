import pytest

from greenkeep.findings import is_vendored, rank, ruff_findings, untested_findings

RUFF = [
    {
        "code": "F401",
        "message": "`os` imported but unused",
        "filename": "/w/pkg/a.py",
        "location": {"row": 1, "column": 8},
    },
    {
        "code": "E501",
        "message": "Line too long",
        "filename": "/w/pkg/a.py",
        "location": {"row": 3, "column": 1},
    },
]


def test_ruff_findings_relative_path_and_stable_fingerprint():
    lines = {"pkg/a.py": ["import os", "", "x = 1"]}
    found = ruff_findings(RUFF, root="/w", read_line=lambda p, n: lines[p][n - 1])
    assert [f.rule for f in found] == ["F401", "E501"]
    assert found[0].path == "pkg/a.py" and found[0].line == 1
    # fingerprint ignores line numbers so it survives code moving around
    moved = ruff_findings(
        [{**RUFF[0], "location": {"row": 9, "column": 8}}],
        root="/w",
        read_line=lambda p, n: "import os",
    )
    assert moved[0].fingerprint == found[0].fingerprint


def test_ruff_findings_ranks_pyflakes_before_style():
    found = ruff_findings(list(reversed(RUFF)), root="/w", read_line=lambda p, n: "x")
    assert found[0].rule == "F401"


def test_untested_findings_skips_tests_small_and_covered_files():
    cov = {
        "files": {
            "pkg/core.py": {"summary": {"num_statements": 40, "percent_covered": 10.0}},
            "pkg/tiny.py": {"summary": {"num_statements": 3, "percent_covered": 0.0}},
            "pkg/good.py": {"summary": {"num_statements": 40, "percent_covered": 90.0}},
            "tests/test_x.py": {"summary": {"num_statements": 40, "percent_covered": 0.0}},
        }
    }
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
    assert [f.title for f in found] == [
        "S110: `try`-`except`-`pass` detected (u.py:4)",
        "S110: `try`-`except`-`pass` detected (u.py:12)",
    ]


@pytest.mark.parametrize(
    ("line", "fixable"),
    [
        ("except Exception:  # noqa: BLE001  (no nvidia-smi -> unknown)", False),
        ("import os  # noqa: F401", True),
        ("import os  # noqa: E501, F401", True),
    ],
)
def test_an_unused_noqa_with_a_note_goes_to_the_patcher_ruff_would_drop_the_note(line, fixable):
    item = {**RUFF[0], "code": "RUF100", "fix": {"applicability": "safe"}}
    assert ruff_findings([item], root="/w", read_line=lambda p, n: line)[0].fixable is fixable


def test_vendored_code_is_left_to_its_upstream(tmp_path):
    (tmp_path / "app/gui/kit").mkdir(parents=True)
    (tmp_path / "app/gui/kit/VENDORED.md").write_text("copied from nicegui-kit")
    assert is_vendored(tmp_path, "app/gui/kit/run_pool.py")
    assert is_vendored(tmp_path, "src/pkg/_vendor/six.py")
    assert not is_vendored(tmp_path, "app/gui/page.py")


def test_a_rule_family_ranks_by_its_whole_prefix_so_sim_is_not_security():
    assert rank("S110") < rank("F401") < rank("C901") == rank("PLR0912") < rank("SIM102")
    assert rank("SIM102") == rank("PERF401") < rank("E501")


def test_identical_lines_get_a_fingerprint_each_and_the_first_keeps_its_own():
    items = [
        {
            "code": "BLE001",
            "message": "m",
            "filename": "/w/a.py",
            "location": {"row": r, "column": 5},
        }
        for r in (9, 3)
    ]
    found = ruff_findings(items, root="/w", read_line=lambda p, n: "except Exception:")
    alone = ruff_findings(items[1:], root="/w", read_line=lambda p, n: "except Exception:")
    by_line = {f.line: f.fingerprint for f in found}
    assert by_line[3] == alone[0].fingerprint and by_line[9] == alone[0].fingerprint + "#1"


def test_ruff_findings_keep_the_last_line_they_span():
    item = {**RUFF[0], "end_location": {"row": 4, "column": 1}}
    assert ruff_findings([item], root="/w", read_line=lambda p, n: "x")[0].end_line == 4
