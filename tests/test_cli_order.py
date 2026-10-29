from pathlib import Path
from types import SimpleNamespace

from claude_cli import UsageLimitError

from greenkeep import cli
from greenkeep.config import Target
from greenkeep.core import Options
from greenkeep.digest import Digest
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


def test_a_named_repo_keeps_its_config_entry_test_root_and_hunt(monkeypatch, tmp_path):
    (tmp_path / "jobs").mkdir()
    entry = Target(str(tmp_path / "jobs"), tests="pipeline", hunt=True)
    monkeypatch.setattr(cli, "load", lambda: SimpleNamespace(repos=[entry]))
    a = cli._parse_args([str(tmp_path / "jobs"), "octrow/other"])
    targets, _ = cli._targets(a)
    assert targets[0].tests == "pipeline" and targets[0].hunt
    assert targets[1] == Target("octrow/other")


def test_usage_limit_stops_the_run_and_leaves_the_day_open(monkeypatch):
    seen = []

    def limited(t, *_):
        seen.append(t.name)
        raise UsageLimitError("claude hit a usage/rate limit")

    monkeypatch.setattr(cli, "run_repo", limited)
    digest = cli._run_all([Target("octrow/a"), Target("octrow/b")],
                          Options(False, 3, None, False, None), State(Path(":memory:")))
    assert seen == ["a"]
    assert digest.limited and len(digest.problems) == 1


def test_a_boost_runs_again_on_a_day_already_done(monkeypatch, tmp_path):
    runs = []
    state = State(tmp_path / "state.db")
    state.record_run()
    monkeypatch.setattr(cli, "State", lambda *_: state)
    monkeypatch.setattr(cli, "paused", lambda: False)
    monkeypatch.setattr(cli, "sweep", lambda: None)
    monkeypatch.setattr(cli, "send", lambda text: None)
    monkeypatch.setattr(cli, "_setup_logging", lambda: None)
    monkeypatch.setattr(cli, "_run_all", lambda *_: runs.append(1) or Digest())
    for boost in (False, True):
        cfg = SimpleNamespace(repos=[], boosting=lambda _now, b=boost: b,
                              budget_at=lambda _now: 3)
        monkeypatch.setattr(cli, "load", lambda cfg=cfg: cfg)
        cli.main(["--push"])
    assert runs == [1]


def test_a_boost_hunts_in_every_repo(monkeypatch, tmp_path):
    cfg = SimpleNamespace(repos=[Target("octrow/a"), Target("octrow/b", hunt=True)],
                          boosting=lambda _now: True, budget_at=lambda _now: 15)
    monkeypatch.setattr(cli, "load", lambda: cfg)
    targets, budget = cli._targets(cli._parse_args([]))
    assert [t.hunt for t in targets] == [True, True] and budget == 15
