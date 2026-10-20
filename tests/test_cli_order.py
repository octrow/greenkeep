from pathlib import Path

from greenkeep import cli
from greenkeep.config import Target
from greenkeep.core import Options
from greenkeep.state import State


def test_focus_repos_run_first_then_least_recently_merged(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_repo", lambda t, *_: seen.append(t.name))
    state = State(Path(":memory:"))
    targets = [Target("octrow/a"), Target("octrow/f", focus=True), Target("octrow/b")]
    monkeypatch.setattr(state, "last_merge", lambda n: {"a": "2026-10-01",
                                                        "f": "2026-10-15"}.get(n, ""))
    cli._run_all(targets, Options(False, 3, None, False, None), state)
    assert seen == ["f", "b", "a"]


def test_spent_budget_still_visits_every_repo_for_security(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_repo", lambda t, *_: seen.append(t.name))
    cli._run_all([Target("octrow/a"), Target("octrow/b")],
                 Options(False, 0, None, False, None), State(Path(":memory:")))
    assert seen == ["a", "b"]
