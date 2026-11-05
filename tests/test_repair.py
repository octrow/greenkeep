from pathlib import Path
from types import SimpleNamespace

from greenkeep import github, repair
from greenkeep.core import Options
from greenkeep.digest import Digest
from greenkeep.state import State
from greenkeep.workspace import TestRun as SuiteRun
from greenkeep.workspace import Workspace, WorkspaceError

RED = SuiteRun(False, {}, "=== 1 failed, 4 passed in 0.5s ===")


def heal_fixture(monkeypatch, tmp_path):
    ws = Workspace("demo", tmp_path, tmp_path, "main", "https://github.com/o/demo")
    calls = []
    monkeypatch.setattr(ws, "git", lambda *a, **kw: calls.append(a) or "head")
    monkeypatch.setattr(ws, "reset", lambda: calls.append(("reset",)))
    monkeypatch.setattr(repair, "_fix", lambda *a: ["a.py"])
    return ws, calls


def test_a_hung_suite_is_not_healed(monkeypatch, tmp_path):
    ws, _ = heal_fixture(monkeypatch, tmp_path)
    fixed = []
    monkeypatch.setattr(repair, "_fix", lambda *a: fixed.append(1) or ["a.py"])
    hung = SuiteRun(False, {}, "pytest timed out after 600s")
    opts = Options(True, 3, None, False, None)
    assert not repair.heal(ws, hung, opts, State(Path(":memory:")), Digest()) and not fixed


def test_a_refused_heal_merge_closes_its_pr_and_returns_the_clone_to_base(monkeypatch, tmp_path):
    ws, calls = heal_fixture(monkeypatch, tmp_path)
    closed = []

    def refuse(ws, branch, title, body):
        raise WorkspaceError("gh pr merge 5 exit 1: blocked")

    monkeypatch.setattr(github, "publish", refuse)
    monkeypatch.setattr(github, "abandon", lambda ws, branch: closed.append(branch))
    digest = Digest()
    opts = Options(True, 3, SimpleNamespace(), False, None)
    assert not repair.heal(ws, RED, opts, State(Path(":memory:")), digest)
    assert closed == ["greenkeep/heal-head"] and calls[-1] == ("reset",) and not digest.merged
