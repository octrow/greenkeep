import os
import time
from types import SimpleNamespace

import pytest

from greenkeep import workspace
from greenkeep.workspace import kept_addopts, repo_addopts, sweep


@pytest.mark.parametrize(
    ("addopts", "kept"),
    [
        ("-ra --cov=src --cov-report term-missing -m 'not slow'", ["-ra", "-m", "not slow"]),
        ("--cov src --strict-markers", ["--strict-markers"]),
        ("--cov --no-cov-on-fail -p pytest_cov -x", ["-x"]),
        ("-n auto --dist loadfile -q", ["-q"]),
        ("-n4 --numprocesses=2 --tb=short", ["--tb=short"]),
    ],
)
def test_coverage_and_xdist_options_are_dropped_the_rest_is_kept(addopts, kept):
    assert kept_addopts(addopts) == kept


def test_addopts_come_from_the_file_pytest_itself_would_read(tmp_path):
    assert repo_addopts(tmp_path) == []
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ["-ra", "--cov=pkg"]\n'
    )
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
    monkeypatch.setattr(
        workspace, "sh", lambda args, *_a, **_k: seen.append(args) or SimpleNamespace(stdout="[]")
    )
    plain = workspace.Workspace("r", tmp_path, tmp_path)
    boosted = workspace.Workspace("r", tmp_path, tmp_path, extra_rules=("C901", "SIM"))
    workspace.ruff_json(plain)
    workspace.ruff_json(boosted)
    assert "--extend-select" not in seen[0]
    assert seen[1][seen[1].index("--extend-select") + 1] == "C901,SIM"


def test_a_crash_keeps_its_header_and_the_repos_own_frames():
    venv = "/c/run-x/x/.venv/lib/python3.12/site-packages"
    text = (
        "....." * 100 + "\nFatal Python error: Segmentation fault\n\n"
        "Current thread 0x1 (most recent call first):\n"
        + f'  File "{venv}/PySide6/x.py", line 1 in show\n' * 200
        + '  File "/c/run-x/x/tests/gui/test_card.py", line 12 in test_card\n'
        + f'  File "{venv}/_pytest/main.py", line 1 in _main\n' * 200
        + "\nExtension modules: "
        + "PySide6.QtCore, " * 300
        + "(total: 300)\n"
    )
    out = workspace.suite_tail(text)
    assert out.startswith("Fatal Python error: Segmentation fault")
    assert "tests/gui/test_card.py" in out and "site-packages" not in out
    assert "Extension modules" not in out
    assert workspace.suite_tail("x" * 5000) == "x" * workspace.OUTPUT_TAIL


def test_a_long_crash_dump_keeps_its_header_first():
    text = "Fatal Python error: Aborted\n" + '  File "/usr/lib/python3.12/threading.py"\n' * 500
    out = workspace.suite_tail(text)
    assert out.startswith("Fatal Python error: Aborted") and len(out) <= workspace.OUTPUT_TAIL


def test_only_the_baseline_runs_a_crashed_suite_once_more(monkeypatch):
    runs = iter(
        [workspace.TestRun(False, {}, "x", crashed=True), workspace.TestRun(True, {}, "1 passed")]
    )
    monkeypatch.setattr(workspace, "run_tests", lambda ws: next(runs))
    assert workspace.baseline_tests(None).ok
    red = workspace.TestRun(False, {}, "1 failed")
    monkeypatch.setattr(workspace, "run_tests", lambda ws: red)
    assert workspace.baseline_tests(None) is red  # a plain red suite is not re-run
