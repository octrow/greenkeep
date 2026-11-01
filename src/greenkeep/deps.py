"""Dependency findings: known vulnerabilities (pip-audit) and outdated direct dependencies.

Both are fixed the same way, deterministically: `uv lock --upgrade-package name==version`.
Outdated ones are left to Dependabot where it already updates the repo's Python deps.
"""

from __future__ import annotations

import json
import re
import tomllib
import urllib.request
from collections.abc import Callable
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .findings import Finding
from .workspace import Workspace, sh

LOCK = "uv.lock"
PYPI = "https://pypi.org/pypi/{}/json"
PYPI_TIMEOUT = 20
AUDIT_TIMEOUT = 600
# `├── fastapi v0.142.2 (latest: v0.143.2)`; extras like `name[a, b]` are dropped
DEPENDABOT = Path(".github/dependabot.yml")
# Dependabot ecosystems that bump the deps in pyproject.toml / uv.lock
PYTHON_ECOSYSTEM = re.compile(r"package-ecosystem:\s*[\"']?(?:uv|pip)[\"']?\s*$", re.MULTILINE)
OUTDATED = re.compile(r"^[├└]── ([\w.-]+)(?:\[[^\]]*\])? v(\S+) \(latest: v(\S+)\)", re.MULTILINE)


def _version(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def same_line(current: Version, candidate: Version) -> bool:
    """Same major (same minor for 0.x): the upgrades semver promises not to break."""
    if current.major == 0:
        return candidate.major == 0 and candidate.minor == current.minor
    return candidate.major == current.major


def pick_target(
    current: str, available: list[str], allowed: SpecifierSet | None = None
) -> str | None:
    """Newest stable release on the current line that the repo's own specifier allows,
    if newer than what is locked."""
    cur = _version(current)
    if cur is None:
        return None
    ok = [
        v
        for v in map(_version, available)
        if v
        and not v.is_prerelease
        and v > cur
        and same_line(cur, v)
        and (allowed is None or v in allowed)
    ]
    return str(max(ok)) if ok else None


def parse_outdated(tree: str) -> list[tuple[str, str]]:
    """`uv tree --outdated --depth 1` -> [(name, locked version)] of outdated direct deps."""
    return [(m[1], m[2]) for m in OUTDATED.finditer(tree)]


def outdated_findings(
    rows: list[tuple[str, str]],
    releases: Callable[[str], list[str]],
    specs: dict[str, SpecifierSet] | None = None,
) -> list[Finding]:
    found = []
    for name, current in rows:
        allowed = (specs or {}).get(canonicalize_name(name))
        target = pick_target(current, releases(name), allowed)
        if target:
            found.append(
                Finding(
                    "outdated",
                    f"{name}=={target}",
                    LOCK,
                    0,
                    f"{name} {current} → {target}",
                    f"outdated:{name}:{target}",
                    title=f"Bump {name} from {current} to {target}",
                )
            )
    return found


def _fixes(cur: Version, vuln: dict) -> list[Version]:
    return sorted(v for v in map(_version, vuln.get("fix_versions", [])) if v and v > cur)


def lowest_fix(current: str, vulns: list[dict]) -> str | None:
    """The smallest version that fixes every vuln with a fixed release, preferring fixes
    on the current line; a vuln with none is left for the message."""
    cur = _version(current)
    if cur is None:
        return None
    needed = [
        next((v for v in fixes if same_line(cur, v)), fixes[0])
        for fixes in (_fixes(cur, vuln) for vuln in vulns)
        if fixes
    ]
    return str(max(needed)) if needed else None


def vuln_findings(audit: dict) -> list[Finding]:
    """pip-audit JSON -> one finding per vulnerable package that an upgrade can fix."""
    found = []
    for dep in audit.get("dependencies", []):
        cur, vulns = _version(dep.get("version", "")), dep.get("vulns", [])
        target = lowest_fix(dep.get("version", ""), vulns)
        if not (cur and target):
            continue
        ids = sorted({v["id"] for v in vulns if _fixes(cur, v)})
        open_ids = sorted({v["id"] for v in vulns if not _fixes(cur, v)})
        name = dep["name"]
        found.append(
            Finding(
                "vuln",
                f"{name}=={target}",
                LOCK,
                0,
                f"{name} {dep['version']}: {', '.join(ids)}"
                + (f"; no fix yet: {', '.join(open_ids)}" if open_ids else ""),
                f"vuln:{name}:{'+'.join(ids)}",
                title=f"Fix {', '.join(ids)}: bump {name} to {target}",
            )
        )
    return found


def pypi_releases(name: str) -> list[str]:
    try:
        with urllib.request.urlopen(PYPI.format(name), timeout=PYPI_TIMEOUT) as resp:
            return list(json.load(resp).get("releases", {}))
    except (OSError, ValueError):
        return []  # unknown or private package: no target, no finding


def collect(ws: Workspace) -> list[Finding]:
    """Vulnerabilities first. Only for repos with a committed uv.lock (`uv sync` creates
    one in the clone otherwise, and an untracked lock is not something to upgrade)."""
    if LOCK not in ws.tracked():
        return []
    req = ws.scratch / "requirements.txt"
    sh(
        [
            "uv",
            "export",
            "--format",
            "requirements-txt",
            "--no-hashes",
            "--no-emit-project",
            "-q",
            "-o",
            str(req),
        ],
        ws.root,
    )
    audit = sh(
        [
            "uvx",
            "pip-audit",
            "-r",
            str(req),
            "--disable-pip",
            "--no-deps",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ],
        ws.root,
        check=False,
        timeout=AUDIT_TIMEOUT,
    )
    vulns = vuln_findings(json.loads(audit.stdout or "{}"))
    if dependabot_updates_deps(ws.root):  # it opens the same bumps, and edits `==` pins too
        return vulns
    tree = sh(["uv", "tree", "--outdated", "--depth", "1"], ws.root).stdout
    return vulns + outdated_findings(parse_outdated(tree), pypi_releases, constraints(ws.root))


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def constraints(root: Path) -> dict[str, SpecifierSet]:
    """Every direct dependency's specifier, by canonical name, from pyproject.toml and its
    uv workspace members: `uv lock --upgrade-package` cannot move past an `==` pin."""
    data = _toml(root / "pyproject.toml")
    members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    specs: dict[str, SpecifierSet] = {}
    for doc in [
        data,
        *(_toml(p) for m in members for p in sorted(root.glob(f"{m}/pyproject.toml"))),
    ]:
        for req in _requirements(doc):
            name = canonicalize_name(req.name)
            specs[name] = specs.get(name, SpecifierSet()) & req.specifier
    return specs


def _requirements(doc: dict) -> list[Requirement]:
    """One pyproject's dependencies, extras and groups; an include-group table or an
    unparsable line is skipped."""
    project = doc.get("project", {})
    texts = [
        *project.get("dependencies", []),
        *(r for group in project.get("optional-dependencies", {}).values() for r in group),
        *(r for group in doc.get("dependency-groups", {}).values() for r in group),
    ]
    reqs = []
    for text in texts:
        try:
            if isinstance(text, str):
                reqs.append(Requirement(text))
        except InvalidRequirement:
            continue
    return reqs


# extras and groups a test run installs too (uv syncs the `dev` group by default)
TEST_EXTRAS = frozenset({"dev", "test", "tests", "testing"})


def extra_only(root: Path, path: str) -> set[str]:
    """Canonical names of the packages that only an optional extra of `path`'s project
    installs: a test importing them fails wherever the extra is not installed."""
    doc = next(
        (
            d
            for p in [*(root / path).parents]
            if p.is_relative_to(root) and (d := _toml(p / "pyproject.toml")).get("project")
        ),
        {},
    )
    project = doc.get("project", {})
    extras = project.get("optional-dependencies", {})
    groups = doc.get("dependency-groups", {})
    installed = [
        *project.get("dependencies", []),
        *(
            r
            for name, reqs in [*extras.items(), *groups.items()]
            if name in TEST_EXTRAS
            for r in reqs
        ),
    ]
    optional = [r for name, reqs in extras.items() if name not in TEST_EXTRAS for r in reqs]
    return _names(optional) - _names(installed)


def _names(texts: list) -> set[str]:
    names = set()
    for text in texts:
        try:
            names.add(canonicalize_name(Requirement(text).name))
        except (InvalidRequirement, TypeError):  # a group's {include-group = ...} table
            continue
    return names


def dependabot_updates_deps(root: Path) -> bool:
    try:
        return bool(PYTHON_ECOSYSTEM.search((root / DEPENDABOT).read_text()))
    except OSError:
        return False


def upgrade(ws: Workspace, spec: str) -> None:
    sh(["uv", "lock", "-q", "--upgrade-package", spec], ws.root)
