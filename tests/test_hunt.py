from types import SimpleNamespace

from greenkeep import hunt, llm


def test_a_claim_on_a_line_no_test_ran_is_dropped():
    # the gate would reject every fix there: three attempts (Opus plans in a boost) for nothing
    cov = {"files": {"a.py": {"executed_lines": [1, 2], "missing_lines": [3]}}}
    job = SimpleNamespace(
        ws=SimpleNamespace(read_line=lambda p, n: "x = 1"),
        base=SimpleNamespace(tests=SimpleNamespace(cov=cov)),
    )
    assert hunt._finding(job, "a.py", llm.Claim(3, "t", "w")) is None
    assert hunt._finding(job, "a.py", llm.Claim(2, "t", "w")) is not None


def test_a_claim_on_a_def_line_needs_the_body_run():
    # `def` runs at import, so its line is always covered; the fix goes in the body
    cov = {"files": {"a.py": {"executed_lines": [1, 5, 6], "missing_lines": [3]}}}
    lines = {
        1: "def f(x):",
        2: '    """Doc."""',
        3: "    return int(x)",
        5: "def g():",
        6: "    return 1",
    }
    job = SimpleNamespace(
        ws=SimpleNamespace(read_line=lambda p, n: lines[n]),
        base=SimpleNamespace(tests=SimpleNamespace(cov=cov)),
    )
    assert hunt._finding(job, "a.py", llm.Claim(1, "t", "w")) is None
    assert hunt._finding(job, "a.py", llm.Claim(5, "t", "w")) is not None
