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
        return SimpleNamespace(returncode=0, stdout="", stderr="")

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
    calls = fake_gh(monkeypatch, merges=[1, 1], states=["CLEAN", "MERGED"])
    github.merge(WS, 7)  # no WorkspaceError: the attempt is not counted
    assert [c[2] for c in calls] == ["merge", "view", "merge", "view"]


def fake_api(monkeypatch, failed: str, message: str) -> None:
    def sh(args, cwd, check=True, timeout=0):
        out = message if "annotations" in args[2] else failed
        return SimpleNamespace(returncode=0, stdout=out + "\n")
    monkeypatch.setattr(github, "sh", sh)


def test_ci_that_github_did_not_start_for_billing_is_told_apart(monkeypatch):
    # linux-coding 2026-10-29: every job failed with this annotation, no step ran
    ws = SimpleNamespace(root=Path("."), origin="https://github.com/octrow/linux-coding.git")
    fake_api(monkeypatch, "122420575008", "true")
    assert github.ci_not_started(ws, "d6a7fb8")
    fake_api(monkeypatch, "122420575008", "false")  # a real failure
    assert not github.ci_not_started(ws, "d6a7fb8")
    fake_api(monkeypatch, "", "")  # nothing failed, or gh failed
    assert not github.ci_not_started(ws, "d6a7fb8")
    fake_api(monkeypatch, "122420575008", "true")  # not on GitHub: nothing to ask
    assert not github.ci_not_started(SimpleNamespace(root=Path("."), origin="/srv/x"), "d6")


def test_required_checks_a_ruleset_has_not_reported_yet_are_waited_for(monkeypatch):
    # repo-scout#25 2026-10-30: BLOCKED by a ruleset's `pytest`, which gh did not list yet
    checks = [SimpleNamespace(returncode=1, stdout="", stderr="no required checks reported "
                              "on the 'greenkeep/20e00b2dc3' branch"),
              SimpleNamespace(returncode=0, stdout="", stderr="")]
    calls = fake_gh(monkeypatch, merges=[1, 0], states=["BLOCKED"])
    real = github.sh
    monkeypatch.setattr(github, "sh", lambda args, cwd, check=True, timeout=0:
                        checks.pop(0) if args[2] == "checks" else real(args, cwd, check, timeout))
    github.merge(WS, 25)  # no WorkspaceError: the attempt is not counted
    assert not checks and [c[2] for c in calls] == ["merge", "view", "merge"]


def test_red_required_checks_fail_the_attempt(monkeypatch):
    fake_gh(monkeypatch, merges=[1], states=["BLOCKED"])
    real = github.sh
    monkeypatch.setattr(github, "sh", lambda args, cwd, check=True, timeout=0:
                        SimpleNamespace(returncode=1, stdout="pytest fail", stderr="")
                        if args[2] == "checks" else real(args, cwd, check, timeout))
    with pytest.raises(WorkspaceError, match="pytest fail"):
        github.merge(WS, 25)
