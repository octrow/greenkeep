from dataclasses import replace
from types import SimpleNamespace

import pytest

from greenkeep import kinds
from greenkeep.core import GateFailure
from greenkeep.findings import Finding
from greenkeep.kinds import (
    Kind,
    characterization_path,
    kind_of,
    line_budget,
    needs_service,
    related_context,
)


def finding(kind: str, rule: str) -> Finding:
    return Finding(kind, rule, "a.py", 1, "m", f"{kind}:{rule}")


def test_work_order_is_security_bugs_tests_upgrades_style():
    found = [finding("ruff", "E501"), finding("outdated", "x==2"), finding("untested", "cov"),
             finding("ruff", "F401"), finding("vuln", "y==3")]
    ordered = sorted(found, key=lambda f: kind_of(f).priority(f))
    assert [f.rule for f in ordered] == ["y==3", "F401", "cov", "x==2", "E501"]


def test_kind_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Kind()


def untested(path: str) -> Finding:
    return Finding("untested", "coverage", path, 1, "0% covered", f"untested:{path}")


def test_new_tests_go_where_the_repo_keeps_them(tmp_path):
    (tmp_path / "tests/db").mkdir(parents=True)
    assert characterization_path(untested("src/pkg/db/repo.py"), tmp_path) == "tests/db/test_repo.py"
    assert characterization_path(untested("pkg/util.py"), tmp_path) == "tests/test_util.py"
    (tmp_path / "tests/db/test_repo.py").write_text("")
    assert (characterization_path(untested("src/pkg/db/repo.py"), tmp_path)
            == "tests/db/test_pkg_db_repo.py")


def test_context_has_the_tests_that_import_the_module_and_their_conftest(tmp_path):
    files = {"tests/conftest.py": "FIXTURE", "tests/test_a.py": "from pkg.db import repo",
             "tests/test_b.py": "import other", "tests/other/conftest.py": "NOPE",
             "pyproject.toml": "[tool.pytest.ini_options]\nasyncio_mode = 'auto'\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    ws = SimpleNamespace(root=tmp_path, tests="", tracked=lambda: set(files))
    ctx = related_context(ws, untested("src/pkg/db/repo.py"))
    assert set(ctx) == {"tests/conftest.py", "tests/test_a.py",
                        "pyproject.toml [tool.pytest] / [tool.ruff]"}
    assert "asyncio_mode" in ctx["pyproject.toml [tool.pytest] / [tool.ruff]"]


def test_line_budget_scales_with_the_module_but_has_a_floor():
    assert line_budget("x\n" * 10) == 120
    assert line_budget("x\n" * 200) == 300


def test_a_new_test_file_never_shares_a_basename_with_another_test(tmp_path):
    # pytest's default import mode fails on two test_extract.py in folders without __init__
    (tmp_path / "tests").mkdir()
    (tmp_path / "test_extract.py").write_text("")
    assert (characterization_path(untested("src/cc_bench/extract.py"), tmp_path)
            == "tests/test_cc_bench_extract.py")


def test_a_repo_with_its_tests_at_the_root_gets_the_new_one_there_too(tmp_path):
    (tmp_path / "test_session.py").write_text("")
    assert characterization_path(untested("src/cc_bench/arms.py"), tmp_path) == "test_arms.py"


def test_new_tests_go_under_the_test_root(tmp_path):
    assert (characterization_path(untested("app/gui/page.py"), tmp_path, "app")
            == "app/tests/test_page.py")


def test_a_module_that_talks_to_a_service_gets_no_generated_tests():
    assert needs_service("import asyncpg\n")
    assert needs_service("from psycopg.rows import dict_row\n")
    assert needs_service("def f():\n    import redis\n")
    assert not needs_service("import json\nfrom pkg import redis_keys\n")
    assert not needs_service("def broken(:\n")


def test_a_lint_fix_may_change_only_the_file_of_its_finding():
    f = Finding("ruff", "S110", "tests/test_app.py", 88, "m", "ruff:S110:x")
    with pytest.raises(GateFailure, match="only"):
        kind_of(f).check_scope(None, f, ["tests/test_app.py", "src/app/core.py"])


@pytest.mark.parametrize(("rule", "planned"), [("C901", True), ("PLR0915", True),
                                               ("SIM117", False), ("F401", False)])
def test_a_boost_plans_only_the_hard_changes_first(monkeypatch, tmp_path, rule, planned):
    asked = []
    monkeypatch.setattr(kinds.llm, "plan", lambda prompt, model, root: "THE PLAN")
    monkeypatch.setattr(kinds.llm, "ask",
                        lambda system, prompt, model, root: asked.append(prompt)
                        or kinds.llm.Reply("r", model, "sha"))
    job = SimpleNamespace(opts=SimpleNamespace(model="sonnet", planner="opus"),
                          ws=SimpleNamespace(root=tmp_path))
    reply = kinds.patch(job, "SYS", "TASK", hard=kinds.is_hard(finding("ruff", rule)))
    assert ("THE PLAN" in asked[0]) == planned
    assert (reply.model == "sonnet + plan by opus") == planned


def test_without_a_planner_nothing_is_planned(monkeypatch, tmp_path):
    monkeypatch.setattr(kinds.llm, "plan", lambda *a: pytest.fail("planned"))
    monkeypatch.setattr(kinds.llm, "ask", lambda s, p, m, r: kinds.llm.Reply("r", m, "sha"))
    job = SimpleNamespace(opts=SimpleNamespace(model="sonnet", planner=""),
                          ws=SimpleNamespace(root=tmp_path))
    assert kinds.patch(job, "SYS", "TASK", hard=True).model == "sonnet"
    assert kinds.is_hard(finding("untested", "coverage")) and kinds.is_hard(finding("bug", "x"))


def lint_job(tmp_path, source: str, executed: list[int], missing: list[int]) -> SimpleNamespace:
    (tmp_path / "a.py").write_text(source)
    cov = {"files": {"a.py": {"executed_lines": executed, "missing_lines": missing}}}
    return SimpleNamespace(ws=SimpleNamespace(root=tmp_path),
                           base=SimpleNamespace(tests=SimpleNamespace(cov=cov)))


def test_a_lint_finding_needs_every_line_it_spans_covered(tmp_path):
    # SIM103 in tiktok-lazy-follower: reported at 104, spans 105, which no test ran
    job = lint_job(tmp_path, "x = 1\nif x:\n    y = 2\n", executed=[1, 2], missing=[3])
    f = Finding("ruff", "SIM103", "a.py", 2, "m", "ruff:SIM103:a.py:d", end_line=3)
    assert not kind_of(f).attemptable(job, f)
    one_line = replace(f, end_line=2)
    assert kind_of(one_line).attemptable(job, one_line)


def test_a_complexity_finding_needs_the_whole_function_covered(tmp_path):
    # ruff puts C901 on the name, and `def` runs at import: the body is what changes
    source = "def f(x):\n    if x:\n        return 1\n    return 2\n"
    f = Finding("ruff", "C901", "a.py", 1, "m", "ruff:C901:a.py:d", end_line=1)
    assert not kind_of(f).attemptable(lint_job(tmp_path, source, [1, 2, 4], [3]), f)
    assert kind_of(f).attemptable(lint_job(tmp_path, source, [1, 2, 3, 4], []), f)


def test_fixing_one_of_several_identical_lines_passes_the_scope_check(monkeypatch):
    # tg-bot-template BLE001: eight identical `except Exception:` lines in one file
    fp = "ruff:BLE001:a.py:d"
    job = SimpleNamespace(ws=SimpleNamespace(git=lambda *a: ""),
                          base=SimpleNamespace(lint=frozenset({fp, fp + "#1"}),
                                               tests=SimpleNamespace(cov={})))
    f = Finding("ruff", "BLE001", "a.py", 3, "m", fp)
    monkeypatch.setattr(kinds, "lint", lambda ws, *p: [replace(f, line=9)])  # the other copy
    kind_of(f).check_scope(job, f, ["a.py"])
    monkeypatch.setattr(kinds, "lint", lambda ws, *p: [f, replace(f, fingerprint=fp + "#1")])
    with pytest.raises(GateFailure, match="still reports"):
        kind_of(f).check_scope(job, f, ["a.py"])


def test_a_dependency_upgrade_syncs_the_venv_before_the_suite(monkeypatch):
    # `uv run` leaves a package that only an extra needs at its old version
    calls = []
    monkeypatch.setattr(kinds.deps, "upgrade", lambda ws, spec: calls.append("lock"))
    monkeypatch.setattr(kinds, "sync", lambda ws: calls.append("sync"))
    f = Finding("vuln", "six==1.17.0", "uv.lock", 0, "m", "vuln:six:X")
    kind_of(f).produce(SimpleNamespace(ws=None), f, "")
    assert calls == ["lock", "sync"]


def test_the_state_budget_count_skips_exactly_the_budget_exempt_kinds():
    # state cannot import kinds (kinds -> core -> state): its prefix list must follow them
    from greenkeep.state import BUDGET_FREE
    exempt = {f"{name}:" for name, k in kinds.KINDS.items() if k.exempt_from_budget}
    assert set(BUDGET_FREE) == exempt
