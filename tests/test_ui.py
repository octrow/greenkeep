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
