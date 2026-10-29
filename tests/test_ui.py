from greenkeep.ui import table_rows

ROWS = [
    {"repo": "repo-scout", "fp": "untested:a.py", "status": "merged", "attempts": 1, "issue": 9,
     "pr": 10, "updated": "2026-10-15", "title": "Add tests for a.py",
     "url": "https://github.com/octrow/repo-scout"},
    {"repo": "repo-scout", "fp": "untested:b.py", "status": "parked", "attempts": 3,
     "issue": 8, "pr": None, "updated": "2026-10-15", "title": None, "url": ""},
]


def test_table_rows_link_pr_first_then_issue_and_filter():
    merged, parked = table_rows(ROWS, None, "")
    assert merged["link"] == "https://github.com/octrow/repo-scout/pull/10"
    assert merged["link_text"] == "#10"
    assert parked["link"] == "" and parked["title"] == "untested:b.py"  # no url: no link
    assert [r["fp"] for r in table_rows(ROWS, "parked", "")] == ["untested:b.py"]
    assert [r["fp"] for r in table_rows(ROWS, None, "a.py")] == ["untested:a.py"]


def test_log_tail_keeps_event_lines_only():
    from greenkeep.ui import log_tail
    text = "2026-10-16 00:29:21,596 INFO a: start\n+ diff line\n    traceback\n" \
           "2026-10-16 00:29:22,000 DEBUG   full failure:\n" \
           "2026-10-16 00:30:00,000 INFO   merged x\n"
    assert log_tail(text).splitlines() == ["2026-10-16 00:30:00,000 INFO   merged x",
                                           "2026-10-16 00:29:21,596 INFO a: start"]
    assert log_tail("") == "(no events yet)"


def test_the_budget_shown_is_the_boost_budget_during_a_boost(monkeypatch):
    from types import SimpleNamespace

    from greenkeep import ui
    cfg = SimpleNamespace(budget_at=lambda now: 15 if now.tzinfo else 0)
    monkeypatch.setattr(ui, "load", lambda: cfg)
    assert ui.budget_today() == 15


async def test_the_page_resumes_a_pause_and_says_when_the_run_queue_is_full(tmp_path, monkeypatch):
    from nicegui.testing.user_simulation import user_simulation

    from greenkeep import ui
    from greenkeep.config import ConfigError
    from greenkeep.state import State

    def no_config():
        raise ConfigError("none")

    pause = tmp_path / "PAUSE"
    pause.write_text("by hand")
    monkeypatch.setattr(ui, "PAUSE_FILE", pause)
    monkeypatch.setattr(ui, "LOG_FILE", tmp_path / "greenkeep.log")
    monkeypatch.setattr(ui, "State", lambda: State(tmp_path / "state.db"))
    monkeypatch.setattr(ui, "load", no_config)
    monkeypatch.setattr(ui.run_pool, "submit", lambda *a, **k: None)  # the queue is full
    async with user_simulation(root=ui.index) as user:
        await user.open("/")
        await user.should_see("Paused")
        user.find("Resume").click()
        assert not pause.exists()
        user.find("Dry run now").click()
        await user.should_see("queue is full")


def test_a_second_start_opens_the_running_dashboard(monkeypatch):
    from greenkeep import port, ui
    opened = []
    monkeypatch.setattr(ui.port, "choose", lambda p, ask: port.Choice(p, serve=False))
    monkeypatch.setattr(ui.webbrowser, "open", opened.append)
    assert ui.main([]) == 0
    assert opened == ["http://127.0.0.1:8095/"]
    assert ui.main(["--no-show"]) == 0 and len(opened) == 1


def test_run_argv_is_the_bare_command_for_defaults_and_carries_every_option():
    from greenkeep import ui
    from greenkeep.core import DEFAULT_MODEL, DEFAULT_REVIEW_MODEL
    base = ["uv", "run", "--project", str(ui.PROJECT), "greenkeep"]
    assert ui.run_argv([], False, None, DEFAULT_MODEL, DEFAULT_REVIEW_MODEL, False) == base
    assert ui.run_argv(["a/b", "/x/y"], True, 5, "m1", "m2", True) == base + [
        "--push", "--budget", "5", "--model", "m1", "--review-model", "m2", "--keep",
        "a/b", "/x/y"]


async def test_the_options_dialog_starts_a_push_run_only_after_confirmation(tmp_path, monkeypatch):
    from nicegui import ui as nicegui_ui
    from nicegui.testing.user_simulation import user_simulation

    from greenkeep import ui
    from greenkeep.config import ConfigError
    from greenkeep.state import State

    def no_config():
        raise ConfigError("none")

    submitted = []
    monkeypatch.setattr(ui, "PAUSE_FILE", tmp_path / "PAUSE")
    monkeypatch.setattr(ui, "LOG_FILE", tmp_path / "greenkeep.log")
    monkeypatch.setattr(ui, "State", lambda: State(tmp_path / "state.db"))
    monkeypatch.setattr(ui, "load", no_config)
    monkeypatch.setattr(ui.run_pool, "submit",
                        lambda argv, **k: submitted.append((argv, k)) or object())
    async with user_simulation(root=ui.index) as user:
        await user.open("/")
        user.find("Run with options…").click()
        await user.should_see("Start")
        user.find(kind=nicegui_ui.switch).click()
        user.find("Start").click()
        await user.should_see("opens real issues")
        assert not submitted  # nothing runs before the confirmation
        user.find("Open PRs and merge").click()
    argv, kwargs = submitted[0]
    assert "--push" in argv and kwargs["label"] == "greenkeep run (push)"
