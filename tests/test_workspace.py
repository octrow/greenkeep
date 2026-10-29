import os
import time
from types import SimpleNamespace

import pytest

from greenkeep import workspace
from greenkeep.workspace import kept_addopts, repo_addopts, sweep


@pytest.mark.parametrize(("addopts", "kept"), [
    ("-ra --cov=src --cov-report term-missing -m 'not slow'", ["-ra", "-m", "not slow"]),
    ("--cov src --strict-markers", ["--strict-markers"]),
    ("--cov --no-cov-on-fail -p pytest_cov -x", ["-x"]),
    ("-n auto --dist loadfile -q", ["-q"]),
    ("-n4 --numprocesses=2 --tb=short", ["--tb=short"]),
])
def test_coverage_and_xdist_options_are_dropped_the_rest_is_kept(addopts, kept):
    assert kept_addopts(addopts) == kept


def test_addopts_come_from_the_file_pytest_itself_would_read(tmp_path):
    assert repo_addopts(tmp_path) == []
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ["-ra", "--cov=pkg"]\n')
    assert repo_addopts(tmp_path) == ["-ra"]
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -x --cov\n")
    assert repo_addopts(tmp_path) == ["-x"]  # pytest.ini wins, like in pytest
    (tmp_path / "pytest.ini").unlink()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\naddopts = ["-q"]\n')
    assert repo_addopts(tmp_path) == ["-q"]


def test_sweep_removes_clones_a_killed_run_left_but_not_a_running_one(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "CACHE", tmp_path)
    old, fresh = tmp_path / "run-a-20261016-023412", tmp_path / "run-b-20261022-000000"
    (old / "a").mkdir(parents=True)
    (fresh / "b").mkdir(parents=True)
    day_ago = time.time() - workspace.STALE_CLONE_SECONDS - 60
    os.utime(old, (day_ago, day_ago))
    sweep()
    assert not old.exists()
    assert fresh.exists()


def test_a_boosted_workspace_lints_with_the_extra_rules(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(workspace, "sh",
                        lambda args, *_a, **_k: seen.append(args) or SimpleNamespace(stdout="[]"))
    plain = workspace.Workspace("r", tmp_path, tmp_path)
    boosted = workspace.Workspace("r", tmp_path, tmp_path, extra_rules=("C901", "SIM"))
    workspace.ruff_json(plain)
    workspace.ruff_json(boosted)
    assert "--extend-select" not in seen[0]
    assert seen[1][seen[1].index("--extend-select") + 1] == "C901,SIM"
