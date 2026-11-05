from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_cli import ClaudeCliError, NotLoggedInError, UsageLimitError

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
    monkeypatch.setattr(
        state, "last_merge", lambda n: {"a": "2026-10-01", "f": "2026-10-15"}.get(n, "")
    )
    cli._run_all(targets, Options(False, 3, None, False, None), state)
    assert seen == ["f", "b", "a"]


def test_past_the_deadline_no_new_repo_starts_and_the_rest_go_first_next_run(monkeypatch):
    # first run: start, a, b (past the deadline), end; second run: start, b, c, a, end
    late = cli.RUN_DEADLINE.total_seconds() + 1
    seen, clock = [], iter([0, 0, late, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(cli, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli, "run_repo", lambda t, *_: seen.append(t.name))
    state = State(Path(":memory:"))
    targets = [Target("octrow/a"), Target("octrow/b"), Target("octrow/c")]
    digest = cli._run_all(targets, Options(False, 3, None, False, None), state)
    assert seen == ["a"]
    assert digest.problems == ["run stopped after 3 h, not reached: b, c"]
    seen.clear()
    cli._run_all(targets, Options(False, 3, None, False, None), state)
    assert seen == ["b", "c", "a"]


def test_the_digest_says_how_long_the_run_took(monkeypatch):
    clock = iter([0, 0, 0, 3780])
    monkeypatch.setattr(cli, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli, "run_repo", lambda *_: None)
    digest = cli._run_all(
        [Target("octrow/a"), Target("octrow/b")],
        Options(False, 3, None, False, None),
        State(Path(":memory:")),
    )
    assert digest.minutes == 63
    assert digest.text(budget_left=1).startswith(
        "greenkeep: 0 merged, 0 parked, 0 problems; budget left today: 1; took 63 min"
    )


def test_spent_budget_still_visits_every_repo_for_security(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_repo", lambda t, *_: seen.append(t.name))
    cli._run_all(
        [Target("octrow/a"), Target("octrow/b")],
        Options(False, 0, None, False, None),
        State(Path(":memory:")),
    )
    assert seen == ["a", "b"]


def test_a_named_repo_keeps_its_config_entry_test_root_and_hunt(monkeypatch, tmp_path):
    (tmp_path / "jobs").mkdir()
    entry = Target(str(tmp_path / "jobs"), tests="pipeline", hunt=True)
    monkeypatch.setattr(
        cli,
        "load",
        lambda: SimpleNamespace(
            repos=[entry], boosting=lambda _now: False, budget_at=lambda _now: 3
        ),
    )
    a = cli._parse_args([str(tmp_path / "jobs"), "octrow/other"])
    targets, _ = cli._targets(a)
    assert targets[0].tests == "pipeline" and targets[0].hunt
    assert targets[1] == Target("octrow/other")


@pytest.mark.parametrize(
    "error",
    [
        UsageLimitError("claude hit a usage/rate limit"),
        NotLoggedInError("claude reports 'Not logged in'"),
        ClaudeCliError('claude exited 1: API Error: 529 {"type":"overloaded_error"}'),
    ],
)
def test_claude_unavailable_stops_the_run_and_leaves_the_day_open(monkeypatch, error):
    seen = []

    def limited(t, *_):
        seen.append(t.name)
        raise error

    monkeypatch.setattr(cli, "run_repo", limited)
    digest = cli._run_all(
        [Target("octrow/a"), Target("octrow/b")],
        Options(False, 3, None, False, None),
        State(Path(":memory:")),
    )
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
    monkeypatch.setattr(cli, "_setup_logging", lambda *_: None)
    monkeypatch.setattr(cli, "_run_all", lambda *_: runs.append(1) or Digest())
    for boost in (False, True):
        cfg = SimpleNamespace(
            repos=[],
            boosting=lambda _now, b=boost: b,
            budget_at=lambda _now: 3,
            login_hours=None,
            backup=None,
        )
        monkeypatch.setattr(cli, "load", lambda cfg=cfg: cfg)
        cli.main(["--push"])
    assert runs == [1]


def test_a_boost_hunts_and_lints_more_in_every_repo(monkeypatch, tmp_path):
    cfg = SimpleNamespace(
        repos=[Target("octrow/a"), Target("octrow/b", hunt=True)],
        boosting=lambda _now: True,
        budget_at=lambda _now: 15,
    )
    monkeypatch.setattr(cli, "load", lambda: cfg)
    targets, budget = cli._targets(cli._parse_args([]))
    assert [t.hunt for t in targets] == [True, True] and budget == 15
    assert all(t.boost for t in targets)


def test_named_repos_take_the_budget_and_the_boost_from_the_config(monkeypatch):
    cfg = SimpleNamespace(
        repos=[Target("octrow/a")], boosting=lambda _now: True, budget_at=lambda _now: 15
    )
    monkeypatch.setattr(cli, "load", lambda: cfg)
    targets, budget = cli._targets(cli._parse_args(["octrow/other"]))
    assert budget == 15 and targets[0].hunt and targets[0].boost
    assert cli._targets(cli._parse_args(["octrow/other", "--budget", "2"]))[1] == 2
    assert cli._targets(cli._parse_args(["octrow/other", "--budget", "0"]))[1] == 0


def test_named_repos_without_a_config_get_the_default_budget(monkeypatch):
    def broken():
        raise cli.ConfigError("no config")

    monkeypatch.setattr(cli, "load", broken)
    targets, budget = cli._targets(cli._parse_args(["octrow/other"]))
    assert budget == cli.DEFAULT_BUDGET and targets == [Target("octrow/other")]


def scheduled_main(
    monkeypatch, tmp_path, sent: list[str], run_all=None, load=None, backup=None
) -> tuple[State, int]:
    """One scheduled `greenkeep --push` on a state.db in tmp_path (it persists between calls)."""
    state = State(tmp_path / "state.db")
    monkeypatch.setattr(cli, "State", lambda *_: state)
    monkeypatch.setattr(cli, "paused", lambda: False)
    monkeypatch.setattr(cli, "sweep", lambda: None)
    monkeypatch.setattr(cli, "send", sent.append)
    monkeypatch.setattr(cli, "_setup_logging", lambda *_: None)
    cfg = SimpleNamespace(
        repos=[Target("octrow/a")],
        boosting=lambda _now: False,
        budget_at=lambda _now: 3,
        login_hours=None,
        backup=backup,
    )
    monkeypatch.setattr(cli, "load", load or (lambda: cfg))
    if run_all:
        monkeypatch.setattr(cli, "_run_all", run_all)
    return state, cli.main(["--push"])


def test_a_run_that_cloned_no_repo_leaves_the_day_open(monkeypatch, tmp_path):
    # no network after boot or sleep: every clone failed, the next hourly run tries again
    state, _ = scheduled_main(monkeypatch, tmp_path, [], run_all=lambda *_: Digest())
    assert not state.ran_today()
    state, _ = scheduled_main(monkeypatch, tmp_path, [], run_all=lambda *_: Digest(reached=1))
    assert state.ran_today()


def test_a_broken_config_is_told_once_a_day(monkeypatch, tmp_path):
    def broken():
        raise cli.ConfigError("repos: expected a list")

    sent = []
    codes = [scheduled_main(monkeypatch, tmp_path, sent, load=broken)[1] for _ in range(2)]
    assert codes == [1, 1]
    assert len(sent) == 1 and "repos: expected a list" in sent[0]


def test_a_scheduled_run_backs_the_state_up_and_reports_a_failed_backup(monkeypatch, tmp_path):
    saved, sent = [], []

    def save(state, checkout):
        saved.append(checkout)
        raise cli.WorkspaceError("git push exit 128: no network")

    monkeypatch.setattr(cli.backup, "save", save)
    scheduled_main(
        monkeypatch, tmp_path, sent, run_all=lambda *_: Digest(reached=1), backup=tmp_path
    )
    assert saved == [tmp_path] and "state backup failed: git push exit 128" in sent[0]


def test_a_claude_stop_keeps_the_unstarted_repos_first_and_named_runs_leave_the_mark(monkeypatch):
    from claude_cli import ClaudeCliError

    seen = []

    def run_repo(t, *_):
        seen.append(t.name)
        if t.name == "a" and len(seen) == 1:
            raise ClaudeCliError("Not logged in")

    monkeypatch.setattr(cli, "run_repo", run_repo)
    monkeypatch.setattr(cli, "unavailable", lambda exc: True)
    state = State(Path(":memory:"))
    targets = [Target("octrow/a"), Target("octrow/b"), Target("octrow/c")]
    opts = Options(False, 3, None, False, None)
    cli._run_all(targets, opts, state)
    assert state.mark(cli.UNREACHED) == "a,b,c"
    cli._run_all([Target("octrow/c")], opts, state, False)  # a named run: the mark stays
    assert state.mark(cli.UNREACHED) == "a,b,c"
