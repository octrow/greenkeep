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


def test_minor_bump_in_the_title_is_same_major():
    assert dependabot.upgrade("Bump requests from 2.31.0 to 2.32.0", "") == "same"


def test_every_pair_of_a_grouped_body_counts_but_collapsed_release_notes_do_not():
    assert dependabot.upgrade("Bump the pip group with 2 updates", GROUPED) == "same"
    grouped_major = GROUPED + "Updates `x` from 1.9 to 2.0"
    assert dependabot.upgrade("Bump the pip group", grouped_major) == "major"


def test_a_major_bump_is_major_and_no_pair_at_all_is_unknown():
    assert dependabot.upgrade("Bump django from 4.2.1 to 5.0.0", "") == "major"
    assert dependabot.upgrade("Bump the pip group with 3 updates", "") == ""
    assert dependabot.upgrade("Bump python from 3.12-slim to 3.13-slim", "") == ""


def test_a_requirement_update_names_its_lower_bounds():
    title = "chore(deps): update python-dotenv requirement from >=1.0 to >=1.2.4"
    assert dependabot.upgrade(title, "") == "same"
    title = "chore(deps): update openai requirement from >=1.30 to >=3.26.1"
    assert dependabot.upgrade(title, "") == "major"


def test_a_0x_minor_bump_is_a_major_one():
    assert dependabot.upgrade("Bump pydantic-ai from 0.6.1 to 0.7.0", "") == "major"
    assert dependabot.upgrade("Bump pydantic-ai from 0.6.1 to 0.6.3", "") == "same"


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
        "headRefName": f"dependabot/pip/x-{n}",
        "mergeable": mergeable,
        "files": [{"path": p} for p in files],
        "statusCheckRollup": [{"conclusion": c} for c in checks],
    }


class Fake:
    """`gh`, git and the suite, recorded; `green` decides every suite run."""

    def __init__(
        self,
        monkeypatch,
        tmp_path: Path,
        prs: list[dict],
        green: bool = True,
        covered: float = 80.0,
        adapted: str = "",
    ) -> None:
        self.prs, self.green, self.covered = prs, green, covered
        self.tested, self.merged, self.git, self.adapted = [], [], [], []
        self.ws = Workspace("demo", tmp_path, tmp_path, "main", "https://github.com/o/demo")
        monkeypatch.setattr(self.ws, "git", lambda *a, **kw: self.git.append(a) or "")
        monkeypatch.setattr(github, "_gh", lambda ws, *a: json.dumps(self.prs))
        monkeypatch.setattr(github, "merge", lambda ws, n, head="": self.merged.append(n))

        def adapt(ws, opts, digest, pr, tests):
            digest.escalated.append(pr["number"])  # like the real one: it takes a slot
            self.adapted.append(pr["number"])
            return adapted

        monkeypatch.setattr(dependabot.repair, "adapt", adapt)
        monkeypatch.setattr(dependabot, "sync", lambda ws: None)
        monkeypatch.setattr(dependabot, "run_tests", self._run_tests)
        self.state, self.digest = State(Path(":memory:")), Digest()

    def _run_tests(self, ws: Workspace) -> SimpleNamespace:
        self.tested.append(self.git[-1])
        # like the real one: a red run measures no coverage
        cov = {"totals": {"percent_covered": self.covered}} if self.green else {}
        return SimpleNamespace(ok=self.green, output="1 failed", cov=cov)

    def run(self, push: bool = True) -> None:
        baseline = {"totals": {"percent_covered": self.covered}}
        opts = Options(push, 3, None, False, None)
        dependabot.merge_safe(self.ws, opts, self.state, self.digest, baseline)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    return lambda prs, **kw: Fake(monkeypatch, tmp_path, prs, **kw)


def test_a_green_dependency_pr_is_merged_and_goes_to_the_digest(fake):
    f = fake([pr(7)])
    f.run()
    assert f.tested == [("checkout", "-q", "--detach", "sha7")] and f.merged == [7]
    assert f.digest.merged == ["demo: Bump requests from 2.31.0 to 2.32.0 (Dependabot #7)"]
    assert ("checkout", "-q", "-f", "main") in f.git[-3:]  # back on the base branch


def test_a_red_pr_gets_the_agent_once_and_is_not_retested_until_its_head_changes(fake):
    f = fake([pr(7)], green=False)
    f.run()
    f.run()
    assert len(f.tested) == 1 and f.merged == [] and f.adapted == [7]
    f.prs[0]["headRefOid"] = "new"
    f.run()
    assert len(f.tested) == 2


def test_conflicts_and_non_versions_are_skipped_and_at_most_three_are_tested(fake):
    f = fake(
        [
            pr(1, "Bump python from 3.12-slim to 3.13-slim"),
            pr(2, mergeable="CONFLICTING"),
            *(pr(n) for n in range(3, 8)),
        ]
    )
    f.run()
    assert f.merged == [3, 4, 5]


def test_a_green_major_is_merged_only_on_a_well_covered_suite(fake):
    f = fake([pr(1, "Bump django from 4.2 to 5.0")])
    f.run()
    assert f.merged == [1]
    f = fake([pr(1, "Bump django from 4.2 to 5.0")], covered=40.0)
    f.run()
    f.run()
    assert f.merged == [] and len(f.tested) == 1 and f.adapted == []


def test_a_red_pr_the_agent_adapts_is_merged_at_the_agents_commit(fake):
    f = fake([pr(1, "Bump django from 4.2 to 5.0")], green=False, adapted="agent-sha")
    f.run()
    assert f.adapted == [1] and f.merged == [1]
    assert f.digest.merged == [
        "demo: Bump django from 4.2 to 5.0 (Dependabot #1, adapted by the agent)"
    ]


def test_red_prs_get_the_agent_at_most_up_to_the_run_cap(fake):
    f = fake([pr(n) for n in (1, 2, 3)], green=False)
    f.run()
    assert f.adapted == [1, 2]


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


def test_a_red_pr_on_a_thinly_covered_baseline_is_not_adapted(fake):
    f = fake([pr(1)], green=False, covered=40.0)
    f.run()
    assert f.adapted == [] and f.merged == []
