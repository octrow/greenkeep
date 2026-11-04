from datetime import datetime
from types import SimpleNamespace

import pytest
from claude_cli import ClaudeCliError, NotLoggedInError

from greenkeep import llm
from greenkeep.findings import Finding


def test_patcher_reads_the_clone_with_read_only_tools(monkeypatch, tmp_path):
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw)
        return SimpleNamespace(text="```python\n```")

    monkeypatch.setattr(llm, "run_claude", fake)
    llm.ask("sys", "prompt", "m", tmp_path)
    assert seen["tools"] == llm.READ_TOOLS and seen["cwd"] == tmp_path
    assert seen["setting_sources"] == ""  # none of the owner's hooks, plugins or CLAUDE.md


@pytest.mark.parametrize(
    ("now", "team"),
    [
        (
            datetime(
                2026,
                11,
                5,
                20,
                59,
            ),
            False,
        ),  # Thursday, before the window
        (
            datetime(
                2026,
                11,
                5,
                21,
                0,
            ),
            True,
        ),  # Thursday 21:00
        (
            datetime(
                2026,
                11,
                6,
                2,
                59,
            ),
            True,
        ),  # Friday night
        (
            datetime(
                2026,
                11,
                6,
                3,
                0,
            ),
            False,
        ),  # Friday 03:00: the window is over
        (
            datetime(
                2026,
                11,
                4,
                22,
                0,
            ),
            False,
        ),  # Wednesday night
    ],
)
def test_the_login_pays_only_in_its_hours_and_omniroute_outside_them(
    monkeypatch, tmp_path, now, team
):
    seen = {}
    monkeypatch.setattr(
        llm, "run_claude", lambda p, **kw: seen.update(kw) or SimpleNamespace(text="")
    )
    monkeypatch.setattr(llm, "_now", lambda: now)
    monkeypatch.setattr(llm, "LOGIN_HOURS", ((3, 21), (4, 3)))  # Thu 21:00 - Fri 03:00
    llm.ask("sys", "prompt", "claude-opus-5-5", tmp_path)
    # in the hours the Claude Code login pays; outside them OmniRoute's `claude` connection,
    # with the provider pinned so no combo answers instead
    assert seen["direct"] is team
    assert seen["model"] == ("claude-opus-5-5" if team else "claude/claude-opus-5-5")


def test_the_import_name_is_relative_to_the_test_root():
    # jobs runs its suite from pipeline/, and its tests import `gmail_api_client`
    assert llm.module_name("pipeline/gmail_api_client.py", "pipeline") == "gmail_api_client"
    assert llm.module_name("src/pkg/db/repo.py") == "pkg.db.repo"


def test_a_retry_of_a_test_file_corrects_the_previous_one_instead_of_starting_over():
    f = Finding("untested", "coverage", "pkg/a.py", 1, "10% covered", "untested:pkg/a.py")
    first = llm.characterization_prompt(f, "x = 1", {}, 120, "")
    retry = llm.characterization_prompt(
        f, "x = 1", {}, 120, "1 failed", previous="def test_x(): ..."
    )
    assert "def test_x(): ..." not in first
    assert "def test_x(): ..." in retry and "1 failed" in retry


def test_the_planner_reads_the_repo_at_high_effort_and_returns_its_plan(monkeypatch, tmp_path):
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return SimpleNamespace(text="1. mirror tests/test_a.py")

    monkeypatch.setattr(llm, "run_claude", fake)
    assert llm.plan("TASK", "opus", tmp_path) == "1. mirror tests/test_a.py"
    assert seen["model"] == "opus" and seen["effort"] == "high"
    assert seen["tools"] == llm.READ_TOOLS and "TASK" in seen["prompt"]


def test_only_claude_not_answering_counts_as_unavailable():
    assert llm.unavailable(NotLoggedInError("claude reports 'Not logged in'"))
    assert llm.unavailable(ClaudeCliError("claude exited 1: API Error: 529 overloaded_error"))
    assert llm.unavailable(ClaudeCliError("claude exited 1: API Error: Connection error."))
    assert not llm.unavailable(ClaudeCliError("claude exited 1: API Error: 400 prompt is too long"))
    assert not llm.unavailable(ClaudeCliError("claude timed out after 600s"))
    assert not llm.unavailable(OSError("API Error: 500"))  # only Claude's own errors


def test_without_login_hours_the_login_always_pays(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        llm, "run_claude", lambda p, **kw: seen.update(kw) or SimpleNamespace(text="")
    )
    llm.ask("sys", "prompt", "claude-opus-5-5", tmp_path)
    assert seen["direct"] and seen["model"] == "claude-opus-5-5"
