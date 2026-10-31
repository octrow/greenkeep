"""Readiness checks: does a tended repo look trustworthy to a stranger (see CONTEXT.md).

A failed check greenkeep can fix is a finding; one only the owner can settle (which
license, a README from nothing) is a line in the digest instead.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from .findings import Finding

README = "README.md"
PYPROJECT = "pyproject.toml"
WORKFLOWS = ".github/workflows"
# LICENSE, LICENSE-MIT, COPYING.md, a REUSE LICENSES/ folder
LICENSE = re.compile(r"^(LICEN[CS]ES?|COPYING)\b", re.IGNORECASE)
FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
# ATX `## Usage`, setext `Usage` over `===`/`---`, HTML `<h2>Usage</h2>`
HEADING = re.compile(r"^#{1,6}\s+(.+)$|^(\S.*)\n(?:=+|-+)\s*$|<h[1-6][^>]*>(.+?)</h[1-6]>",
                     re.MULTILINE | re.IGNORECASE)
PROJECT_HEADER = re.compile(r"^\[project\]\s*(#.*)?$")
# an existing badge of any CI: GitHub Actions (either URL form), shields.io, codecov, ...
BADGE = re.compile(r"badge\.svg|/actions/workflow|shields\.io|codecov\.io")
# rule -> (the section, what its heading says in English or Russian)
SECTIONS = {
    "readme-install": ("an installation", re.compile(
        r"install|setup|getting started|quick ?start|установ", re.IGNORECASE)),
    "readme-usage": ("a usage", re.compile(
        r"usage|example|how to|getting started|quick ?start|использ|пример", re.IGNORECASE)),
}
# a code line that installs or fetches the package, not one that uses it
INSTALL_LINE = re.compile(r"\b(pip3?|pipx|uv(\s+tool)?|poetry|conda)\s+(install|add)\b|"
                          r"\bgit\s+clone\b|^\W*cd\b")
CI_HINTS = ("ci", "test", "check")  # a workflow named like this is the one the badge shows
# the rule a deterministic or LLM fix of pyproject.toml answers -> the one field it may change
FIELDS = {"description": "description", "readme-field": "readme", "urls": "urls"}


def _finding(rule: str, path: str, message: str, title: str) -> Finding:
    return Finding("ready", rule, path, 0, message, f"ready:{rule}", title=title)


def owner_notes(root: Path) -> list[str]:
    """What only the owner can settle."""
    notes = []
    if not any(LICENSE.match(p.name) for p in root.iterdir()):
        notes.append("has no LICENSE: pick one")
    if not (root / README).is_file():
        notes.append(f"has no {README}")
    return notes


def collect(root: Path, url: str) -> list[Finding]:
    """`url`: the repo's GitHub page, "" when it is not on GitHub."""
    return [*_pyproject(root, url), *_readme(root, url)]


def _project(root: Path) -> dict | None:
    try:
        return tomllib.loads((root / PYPROJECT).read_text()).get("project")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None


def _pyproject(root: Path, url: str) -> list[Finding]:
    project = _project(root)
    if project is None or _project_header(root) is None:
        return []  # no [project] header line to insert under
    # an empty description needs words; an empty urls table must not get a second header
    missing = {"description"} if not project.get("description") else set()
    missing |= {k for k in ("readme", "urls") if k not in project}
    missing -= set(project.get("dynamic", []))
    found = []
    if "description" in missing:
        found.append(_finding("description", PYPROJECT, "[project] has no description: add "
                              "one line saying what the project does",
                              "Describe the project in pyproject.toml"))
    if "readme" in missing and (root / README).is_file():
        found.append(_finding("readme-field", PYPROJECT, f"[project] does not name {README}",
                              f"Name {README} in pyproject.toml"))
    if "urls" in missing and url:
        found.append(_finding("urls", PYPROJECT, "[project] has no urls",
                              "Link the repository from pyproject.toml"))
    return found


def _readme(root: Path, url: str) -> list[Finding]:
    try:
        text = (root / README).read_text()
    except (OSError, UnicodeError):
        return []
    headings = ["".join(groups) for groups in HEADING.findall(FENCE.sub("", text))]
    shown = {"readme-usage": _calls_package(text, _names(root))}
    found = [_finding(rule, README, f"{README} has no {section} section: add one",
                      f"Add {section} section to {README}")
             for rule, (section, pattern) in SECTIONS.items()
             if not shown.get(rule) and not any(map(pattern.search, headings))]
    if url and _workflow(root) and not BADGE.search(text):
        found.append(_finding("ci-badge", README, f"{README} has no CI badge",
                              f"Add a CI badge to {README}"))
    return found


def _names(root: Path) -> set[str]:
    """What a README calls the package by: its name, its import name, its scripts."""
    project = _project(root) or {}
    name = project.get("name", "")
    scripts = project.get("scripts", {})
    return {n for n in (name, name.replace("-", "_"), *scripts) if n}


def _calls_package(text: str, names: set[str]) -> bool:
    """A code block with a line that runs or imports the package shows its usage, whatever
    the heading above it says ("What it looks like")."""
    if not names:
        return False
    called = re.compile(rf"(?<![\w.-])({'|'.join(map(re.escape, names))})(?![\w-])")
    return any(called.search(line) and not INSTALL_LINE.search(line)
               for block in FENCE.finditer(text) for line in block.group(0).splitlines()[1:-1])


def _workflow(root: Path) -> str:
    """The workflow file the CI badge shows, "" when the repo has none."""
    names = sorted(p.name for p in (root / WORKFLOWS).glob("*.y*ml"))
    hinted = [n for n in names if any(h in n.lower() for h in CI_HINTS)]
    return (hinted or [""])[0]  # only release or deploy workflows: no CI to show


def _project_header(root: Path) -> int | None:
    lines = (root / PYPROJECT).read_text().splitlines()
    return next((i for i, text in enumerate(lines) if PROJECT_HEADER.match(text)), None)


def _insert_under_project(root: Path, line: str, after: str) -> None:
    """Below the one-line `after = ...` key of [project] when there is one, else the header."""
    # ponytail: rewrites line endings as \n; keep CRLF if a CRLF repo shows up
    path, at = root / PYPROJECT, _project_header(root)
    lines = path.read_text().splitlines()
    key = re.compile(rf"^{after}\s*=\s*(\".*\"|'.*')\s*(#.*)?$")
    for i in range(at + 1, len(lines)):
        if lines[i].startswith("["):
            break
        if key.match(lines[i]):
            at = i
            break
    path.write_text("\n".join([*lines[:at + 1], line, *lines[at + 1:]]) + "\n")


def _fix_readme_field(root: Path, url: str) -> None:
    _insert_under_project(root, f'readme = "{README}"', after="description")


def _fix_urls(root: Path, url: str) -> None:
    path = root / PYPROJECT
    path.write_text(path.read_text().rstrip("\n") + "\n\n[project.urls]\n"
                    f'Homepage = "{url}"\nIssues = "{url}/issues"\n')


def _fix_ci_badge(root: Path, url: str) -> None:
    path, name = root / README, _workflow(root)
    badge = f"[![CI]({url}/actions/workflows/{name}/badge.svg)]({url}/actions/workflows/{name})"
    lines = path.read_text().splitlines()
    title = next((i for i, text in enumerate(lines) if text.startswith("# ")
                  and not _in_fence(lines, i)), None)
    lines = [badge, "", *lines] if title is None else \
        [*lines[:title + 1], "", badge, *lines[title + 1:]]
    path.write_text("\n".join(lines) + "\n")


def _in_fence(lines: list[str], i: int) -> bool:
    return sum(text.startswith(("```", "~~~")) for text in lines[:i]) % 2 == 1


# the checks a few lines of code fix; the others (description, README sections) need words
FIXES: dict[str, Callable[[Path, str], None]] = {
    "readme-field": _fix_readme_field, "urls": _fix_urls, "ci-badge": _fix_ci_badge}
