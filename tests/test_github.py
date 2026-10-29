from pathlib import Path
from types import SimpleNamespace

import pytest

from greenkeep import github
from greenkeep.workspace import WorkspaceError


def fake_gh(monkeypatch, merges: list[int], states: list[str]) -> list[list[str]]:
    """`gh pr merge` exits with `merges` in order; `gh pr view` answers `states` in order."""
    calls = []

    def sh(args, cwd, check=True, timeout=0):
        calls.append(args)
        if args[:3] == ["gh", "pr", "merge"]:
            code = merges.pop(0)
            if check and code:
                raise WorkspaceError("gh pr merge… exit 1: not mergeable")
            return SimpleNamespace(returncode=code, stdout="", stderr="not mergeable")
        if args[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=0, stdout=states.pop(0) + "\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(github, "sh", sh)
    monkeypatch.setattr(github.time, "sleep", lambda _: None)
    return calls


WS = SimpleNamespace(root=Path("."))


def test_a_merge_refused_while_github_works_out_mergeability_is_retried(monkeypatch):
    # repo-scout 2026-10-29: the first merge right after `pr create`, no required checks
    calls = fake_gh(monkeypatch, merges=[1, 0], states=["UNKNOWN", "CLEAN"])
    github.merge(WS, 7)
    assert [c[2] for c in calls] == ["merge", "view", "view", "merge"]  # no `checks --watch`


def test_a_merge_blocked_by_required_checks_waits_for_them(monkeypatch):
    calls = fake_gh(monkeypatch, merges=[1, 0], states=["BLOCKED"])
    github.merge(WS, 7)
    assert [c[2] for c in calls] == ["merge", "view", "checks", "merge"]


def test_a_merge_github_still_refuses_fails_the_attempt(monkeypatch):
    fake_gh(monkeypatch, merges=[1, 1], states=["DIRTY", "OPEN"])
    with pytest.raises(WorkspaceError):
        github.merge(WS, 7)


def test_a_merge_github_made_although_gh_failed_counts_as_merged(monkeypatch):
    fake_gh(monkeypatch, merges=[1, 1], states=["CLEAN", "MERGED"])
    github.merge(WS, 7)
