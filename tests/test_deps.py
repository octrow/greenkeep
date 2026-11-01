from packaging.specifiers import SpecifierSet

from greenkeep.deps import (
    constraints,
    dependabot_updates_deps,
    lowest_fix,
    outdated_findings,
    parse_outdated,
    pick_target,
    vuln_findings,
)

TREE = """buysmarter v0.1.2
├── alembic v1.18.5 (latest: v1.20.0)
├── asyncpg v0.32.0
├── market-sources[browser, impersonate] v0.1.1 (latest: v0.2.0)
├── openai v2.46.0 (latest: v3.31.0)
└── typer v0.27.0 (latest: v0.27.3)
"""


def test_parse_outdated_keeps_only_outdated_direct_deps():
    assert parse_outdated(TREE) == [
        ("alembic", "1.18.5"),
        ("market-sources", "0.1.1"),
        ("openai", "2.46.0"),
        ("typer", "0.27.0"),
    ]


def test_pick_target_stays_on_the_current_line():
    assert pick_target("2.46.0", ["2.46.0", "2.47.1", "2.48.0rc1", "3.31.0"]) == "2.47.1"
    assert pick_target("0.27.0", ["0.27.3", "0.28.0"]) == "0.27.3"  # 0.x: minor is the line
    assert pick_target("0.1.1", ["0.2.0"]) is None
    assert pick_target("1.0", ["1.0"]) is None


def test_outdated_findings_skip_deps_without_a_same_line_release():
    releases = {
        "alembic": ["1.20.0"],
        "market-sources": ["0.2.0"],
        "openai": ["3.31.0"],
        "typer": ["0.27.3"],
    }
    found = outdated_findings(parse_outdated(TREE), releases.__getitem__)
    assert [f.rule for f in found] == ["alembic==1.20.0", "typer==0.27.3"]
    assert found[0].title == "Bump alembic from 1.18.5 to 1.20.0"
    assert found[0].fingerprint == "outdated:alembic:1.20.0"


def test_lowest_fix_closes_every_vuln_and_prefers_current_line():
    vulns = [{"fix_versions": ["1.2.5", "2.0.1"]}, {"fix_versions": ["1.2.7"]}]
    assert lowest_fix("1.2.0", vulns) == "1.2.7"
    assert lowest_fix("1.2.0", [{"fix_versions": []}]) is None


def test_vuln_findings():
    audit = {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "3.1.2",
                "vulns": [
                    {"id": "GHSA-1", "fix_versions": ["3.1.3"]},
                    {"id": "CVE-2", "fix_versions": ["3.1.4"]},
                ],
            },
            {"name": "ok", "version": "1.0", "vulns": []},
            {"name": "nofix", "version": "1.0", "vulns": [{"id": "X", "fix_versions": []}]},
        ]
    }
    [f] = vuln_findings(audit)
    assert f.rule == "jinja2==3.1.4" and f.kind == "vuln"
    assert f.title == "Fix CVE-2, GHSA-1: bump jinja2 to 3.1.4"


def test_dependabot_owns_version_updates_only_for_python_ecosystems(tmp_path):
    assert not dependabot_updates_deps(tmp_path)
    cfg = tmp_path / ".github/dependabot.yml"
    cfg.parent.mkdir()
    cfg.write_text('updates:\n  - package-ecosystem: "github-actions"\n')
    assert not dependabot_updates_deps(tmp_path)
    cfg.write_text(
        "updates:\n  - package-ecosystem: github-actions\n"
        "  - package-ecosystem: uv\n    directory: /\n"
    )
    assert dependabot_updates_deps(tmp_path)
    cfg.write_text("updates:\n  - package-ecosystem: 'pip'\n")
    assert dependabot_updates_deps(tmp_path)


def test_an_advisory_without_a_fix_does_not_hide_the_fixable_ones():
    audit = {
        "dependencies": [
            {
                "name": "pillow",
                "version": "11.0",
                "vulns": [
                    {"id": "GHSA-1", "fix_versions": ["11.1"]},
                    {"id": "GHSA-2", "fix_versions": []},
                ],
            },
        ]
    }
    [f] = vuln_findings(audit)
    assert f.rule == "pillow==11.1" and f.fingerprint == "vuln:pillow:GHSA-1"
    assert "no fix yet: GHSA-2" in f.message


def test_outdated_targets_stay_inside_the_repo_constraints():
    # jobs: `typer==0.27.1` in a workspace member; `uv lock` found no solution for 0.27.3
    rows = [("typer", "0.27.1"), ("alembic", "1.18.5")]
    releases = {"typer": ["0.27.3"], "alembic": ["1.18.9", "1.20.0"]}
    found = outdated_findings(
        rows,
        releases.__getitem__,
        {"typer": SpecifierSet("==0.27.1"), "alembic": SpecifierSet("<1.19")},
    )
    assert [f.rule for f in found] == ["alembic==1.18.9"]


def test_constraints_come_from_the_root_and_every_workspace_member(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "jobs"\ndependencies = ["Alembic>=1"]\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n'
        '[tool.uv.workspace]\nmembers = ["pipeline"]\n'
    )
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline/pyproject.toml").write_text(
        '[project]\nname = "pipeline"\ndependencies = ["typer==0.27.1", "alembic<2"]\n'
    )
    specs = constraints(tmp_path)
    assert specs["typer"] == SpecifierSet("==0.27.1")
    assert specs["alembic"] == SpecifierSet(">=1,<2") and "pytest" in specs
