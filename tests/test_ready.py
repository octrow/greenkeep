import tomllib

from greenkeep import ready

URL = "https://github.com/o/demo"
README_OK = "# demo\n\n## Install\n\nuv tool install demo\n\n## Usage\n\ndemo run\n"


def repo(tmp_path, pyproject: str, readme: str | None = README_OK, workflow: bool = False):
    (tmp_path / "pyproject.toml").write_text(pyproject)
    if readme is not None:
        (tmp_path / "README.md").write_text(readme)
    if workflow:
        (tmp_path / ".github/workflows").mkdir(parents=True)
        (tmp_path / ".github/workflows/release.yml").write_text("")
        (tmp_path / ".github/workflows/ci.yml").write_text("")
    return tmp_path


def rules(root, url=URL) -> list[str]:
    return [f.rule for f in ready.collect(root, url)]


def test_a_ready_repo_has_no_findings(tmp_path):
    root = repo(tmp_path, '[project]\nname = "d"\ndescription = "x"\nreadme = "README.md"\n'
                          '[project.urls]\nHomepage = "h"\n')
    assert rules(root) == []


def test_each_missing_piece_is_its_own_finding(tmp_path):
    root = repo(tmp_path, '[project]\nname = "d"\n', readme="# demo\n\nText.\n", workflow=True)
    assert rules(root) == ["description", "readme-field", "urls", "readme-install",
                           "readme-usage", "ci-badge"]
    assert rules(root, url="") == ["description", "readme-field", "readme-install",
                                   "readme-usage"]  # nothing that needs a GitHub link


def test_dynamic_fields_and_russian_headings_count(tmp_path):
    root = repo(tmp_path, '[project]\nname = "d"\ndynamic = ["description", "readme", "urls"]\n',
                readme="# demo\n\n## Установка\n\n## Использование\n")
    assert rules(root) == []


def test_deterministic_fixes_make_their_check_pass(tmp_path):
    root = repo(tmp_path, '[project]\nname = "d"\ndescription = "x"\n\n[tool.x]\ny = 1\n',
                readme="# demo\n\n## Install\n\n## Usage\n", workflow=True)
    for rule in ("readme-field", "urls", "ci-badge"):
        ready.FIXES[rule](root, URL)
    assert rules(root) == []
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["readme"] == "README.md" and data["tool"] == {"x": {"y": 1}}
    assert data["project"]["urls"]["Issues"] == f"{URL}/issues"
    lines = (root / "README.md").read_text().splitlines()
    assert lines[0] == "# demo" and "workflows/ci.yml/badge.svg" in lines[2]


def test_owner_notes_for_what_greenkeep_cannot_decide(tmp_path):
    assert ready.owner_notes(tmp_path) == ["has no LICENSE: pick one", "has no README.md"]
    (tmp_path / "LICENSE.md").write_text("MIT")
    (tmp_path / "README.md").write_text("# x\n")
    assert ready.owner_notes(tmp_path) == []


def test_headings_in_code_blocks_do_not_count_and_other_heading_forms_do(tmp_path):
    readme = ("Demo\n====\n\n```bash\n# install deps\npip install x\n```\n\n"
              "<h2>Usage</h2>\n")
    root = repo(tmp_path, '[project]\nname = "d"\ndescription = "x"\nreadme = "README.md"\n'
                          "urls = {}\n", readme=readme)
    assert rules(root) == ["readme-install"]  # and an empty urls table is not "missing"


def test_any_existing_ci_badge_counts_and_release_only_workflows_get_none(tmp_path):
    root = repo(tmp_path, '[project]  # meta\nname = "d"\ndescription = "x"\nreadme = "README.md"\n'
                          '[project.urls]\nHomepage = "h"\n',
                readme=README_OK + "![ci](https://img.shields.io/github/actions/workflow/status/o/d/t.yml)\n",
                workflow=True)
    assert rules(root) == []
    (root / "README.md").write_text(README_OK)
    (root / ".github/workflows/ci.yml").unlink()
    assert rules(root) == []  # release.yml alone is not CI


def test_badge_goes_under_the_title_not_into_a_code_block(tmp_path):
    root = repo(tmp_path, '[project]\nname = "d"\n',
                readme="```sh\n# not a title\n```\n\n# demo\n", workflow=True)
    ready.FIXES["ci-badge"](root, URL)
    lines = (root / "README.md").read_text().splitlines()
    assert lines[1] == "# not a title" and lines[4] == "# demo" and "badge.svg" in lines[6]


def test_dual_and_reuse_license_layouts_count(tmp_path):
    (tmp_path / "README.md").write_text("# x\n")
    (tmp_path / "LICENSES").mkdir()
    assert ready.owner_notes(tmp_path) == []
    (tmp_path / "LICENSES").rmdir()
    (tmp_path / "LICENSE-APACHE").write_text("")
    assert ready.owner_notes(tmp_path) == []
