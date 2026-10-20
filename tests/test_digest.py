from greenkeep.digest import Digest


def test_digest_text_lists_only_nonempty_sections():
    d = Digest(merged=["repo-scout: tests for x (PR #10)"], problems=["jobs: red baseline"])
    text = d.text(budget_left=2)
    assert text.startswith("greenkeep: 1 merged, 0 parked, 1 problems; budget left today: 2")
    assert "Merged:\n• repo-scout" in text and "Needs you" not in text


def test_quiet_line_names_only_the_repos_waiting_for_budget():
    d = Digest(clean=9, waiting=["jobs", "chat-watch"], cooling=1)
    assert d.text(budget_left=0).endswith(
        "Quiet: 9 clean, 2 waiting for budget (jobs, chat-watch), 1 in cool-down")
    assert "Quiet" not in Digest().text(budget_left=3)
