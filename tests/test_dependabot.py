import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from greenkeep import dependabot, github
from greenkeep.core import Options
from greenkeep.digest import Digest
from greenkeep.state import State
from greenkeep.workspace import Workspace, WorkspaceError

GROUPED = (
    "Bumps the pip group with 2 updates.\n"
    "Updates `requests` from 2.31.0 to 2.32.3\n"
    "<details><summary>Commits</summary>Bump actions/checkout from 3 to 4</details>\n"
    "Updates `ruff` from 0.6.1 to 0.6.9.\n"
)


def test_minor_bump_in_the_title_is_safe():
    assert dependabot.safe_bump("Bump requests from 2.31.0 to 2.32.0", "")


def test_every_pair_of_a_grouped_body_counts_but_collapsed_release_notes_do_not():
    assert dependabot.safe_bump("Bump the pip group with 2 updates", GROUPED)
    assert not dependabot.safe_bump("Bump the pip group", GROUPED + "Updates `x` from 1.9 to 2.0")


def test_a_major_bump_or_no_pair_at_all_is_left_to_the_owner():
    assert not dependabot.safe_bump("Bump django from 4.2.1 to 5.0.0", "")
    assert not dependabot.safe_bump("Bump the pip group with 3 updates", "")


def test_a_0x_minor_bump_is_a_major_one():
    assert not dependabot.safe_bump("Bump pydantic-ai from 0.6.1 to 0.7.0", "")
    assert dependabot.safe_bump("Bump pydantic-ai from 0.6.1 to 0.6.3", "")


def test_actions_only_and_their_checks():
    assert dependabot.actions_only([{"path": ".github/workflows/ci.yml"}])
    assert not dependabot.actions_only([{"path": ".github/workflows/ci.yml"}, {"path": "uv.lock"}])
    assert dependabot.checks_passed(
        [{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}, {"conclusion": "SKIPPED"}]
    )
    assert not dependabot.checks_passed([{"conclusion": "SUCCESS"}, {"conclusion": ""}])
    assert not dependabot.checks_passed([])  # CI never started: no verdict


def pr(
    n: int,
    title: str = "Bump requests from 2.31.0 to 2.32.0",
    files: tuple = ("uv.lock",),
    checks: tuple = (),
    mergeable: str = "MERGEABLE",
) -> dict:
    return {
        "number": n,
        "title": title,
        "body": "",
        "headRefOid": f"sha{n}",
        "mergeable": mergeable,
        "files": [{"path": p} for p in files],
        "statusCheckRollup": [{"conclusion": c} for c in checks],
    }


class Fake:
    """`gh`, git and the suite, recorded; `green` decides every suite run."""

    def __init__(self, monkeypatch, tmp_path: Path, prs: list[dict], green: bool = True) -> None:
        self.prs, self.green = prs, green
        self.tested, self.merged, self.git = [], [], []
        self.ws = Workspace("demo", tmp_path, tmp_path, "main", "https://github.com/o/demo")
        monkeypatch.setattr(self.ws, "git", lambda *a, **kw: self.git.append(a) or "")
        monkeypatch.setattr(github, "_gh", lambda ws, *a: json.dumps(self.prs))
        monkeypatch.setattr(github, "merge", lambda ws, n, head="": self.merged.append(n))
        monkeypatch.setattr(dependabot, "sync", lambda ws: None)
        monkeypatch.setattr(dependabot, "run_tests", self._run_tests)
        self.state, self.digest = State(Path(":memory:")), Digest()

    def _run_tests(self, ws: Workspace) -> SimpleNamespace:
        self.tested.append(self.git[-1])
        return SimpleNamespace(ok=self.green, output="1 failed")

    def run(self, push: bool = True) -> None:
        dependabot.merge_safe(self.ws, Options(push, 3, None, False, None), self.state, self.digest)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    return lambda prs, green=True: Fake(monkeypatch, tmp_path, prs, green)


def test_a_green_dependency_pr_is_merged_and_goes_to_the_digest(fake):
    f = fake([pr(7)])
    f.run()
    assert f.tested == [("checkout", "-q", "--detach", "sha7")] and f.merged == [7]
    assert f.digest.merged == ["demo: Bump requests from 2.31.0 to 2.32.0 (Dependabot #7)"]
    assert ("checkout", "-q", "-f", "main") in f.git[-3:]  # back on the base branch


def test_a_red_pr_is_not_retested_until_its_head_changes(fake):
    f = fake([pr(7)], green=False)
    f.run()
    f.run()
    assert len(f.tested) == 1 and f.merged == []
    f.prs[0]["headRefOid"] = "new"
    f.run()
    assert len(f.tested) == 2


def test_majors_and_conflicts_are_skipped_and_at_most_three_are_tested(fake):
    f = fake(
        [
            pr(1, "Bump django from 4.2 to 5.0"),
            pr(2, mergeable="CONFLICTING"),
            *(pr(n) for n in range(3, 8)),
        ]
    )
    f.run()
    assert f.merged == [3, 4, 5]


def test_an_actions_pr_merges_on_its_checks_without_a_suite_run(fake):
    f = fake(
        [
            pr(
                1,
                "Bump actions/checkout from 4.1.0 to 4.2.0",
                (".github/workflows/ci.yml",),
                ("SUCCESS",),
            ),
            pr(
                2,
                "Bump actions/setup-python from 5.0 to 5.1",
                (".github/workflows/ci.yml",),
                ("PENDING",),
            ),
        ]
    )
    f.run()
    assert f.merged == [1] and f.tested == []


def test_a_dry_run_only_tells(fake):
    f = fake([pr(7)])
    f.run(push=False)
    assert f.tested == [] and f.merged == [] and f.git == [] and f.digest.merged == []


def test_one_failing_pr_does_not_stop_the_others(fake, monkeypatch):
    f = fake([pr(1), pr(2)])

    def merge(ws, n, head=""):
        if n == 1:
            raise WorkspaceError("gh pr merge… exit 1: not mergeable")
        f.merged.append(n)

    monkeypatch.setattr(github, "merge", merge)
    f.run()
    assert f.merged == [2]
