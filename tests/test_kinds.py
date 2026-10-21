from types import SimpleNamespace

import pytest

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


def test_new_tests_go_under_the_test_root(tmp_path):
    assert (characterization_path(untested("app/gui/page.py"), tmp_path, "app")
            == "app/tests/test_page.py")


def test_a_module_that_talks_to_a_service_gets_no_generated_tests():
    assert needs_service("import asyncpg\n")
    assert needs_service("from psycopg.rows import dict_row\n")
    assert needs_service("def f():\n    import redis\n")
    assert not needs_service("import json\nfrom pkg import redis_keys\n")
    assert not needs_service("def broken(:\n")
