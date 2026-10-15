"""Dependency findings: known vulnerabilities (pip-audit) and outdated direct dependencies.

Both are fixed the same way, deterministically: `uv lock --upgrade-package name==version`.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable

from packaging.version import InvalidVersion, Version

from .findings import Finding
from .workspace import Workspace, sh

LOCK = "uv.lock"
PYPI = "https://pypi.org/pypi/{}/json"
PYPI_TIMEOUT = 20
AUDIT_TIMEOUT = 600
# `├── fastapi v0.142.2 (latest: v0.143.2)`; extras like `name[a, b]` are dropped
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


def pick_target(current: str, available: list[str]) -> str | None:
    """Newest stable release on the current line, if newer than what is locked."""
    cur = _version(current)
    if cur is None:
        return None
    ok = [v for v in map(_version, available)
          if v and not v.is_prerelease and v > cur and same_line(cur, v)]
    return str(max(ok)) if ok else None


def parse_outdated(tree: str) -> list[tuple[str, str]]:
    """`uv tree --outdated --depth 1` -> [(name, locked version)] of outdated direct deps."""
    return [(m[1], m[2]) for m in OUTDATED.finditer(tree)]


def outdated_findings(rows: list[tuple[str, str]],
                      releases: Callable[[str], list[str]]) -> list[Finding]:
    found = []
    for name, current in rows:
        target = pick_target(current, releases(name))
        if target:
            found.append(Finding("outdated", f"{name}=={target}", LOCK, 0,
                                 f"{name} {current} → {target}", f"outdated:{name}:{target}",
                                 title=f"Bump {name} from {current} to {target}"))
    return found


def lowest_fix(current: str, vulns: list[dict]) -> str | None:
    """The smallest version that fixes every vuln, preferring fixes on the current line."""
    cur = _version(current)
    if cur is None:
        return None
    needed = []
    for vuln in vulns:
        fixes = sorted(v for v in map(_version, vuln.get("fix_versions", [])) if v and v > cur)
        if not fixes:
            return None  # one vuln has no fixed release yet: an upgrade cannot close it
        needed.append(next((v for v in fixes if same_line(cur, v)), fixes[0]))
    return str(max(needed)) if needed else None


def vuln_findings(audit: dict) -> list[Finding]:
    """pip-audit JSON -> one finding per vulnerable package that an upgrade can fix."""
    found = []
    for dep in audit.get("dependencies", []):
        ids = sorted({vuln["id"] for vuln in dep.get("vulns", [])})
        target = lowest_fix(dep.get("version", ""), dep.get("vulns", []))
        if ids and target:
            name = dep["name"]
            found.append(Finding("vuln", f"{name}=={target}", LOCK, 0,
                                 f"{name} {dep['version']}: {', '.join(ids)}",
                                 f"vuln:{name}:{'+'.join(ids)}",
                                 title=f"Fix {', '.join(ids)}: bump {name} to {target}"))
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
    sh(["uv", "export", "--format", "requirements-txt", "--no-hashes", "--no-emit-project",
        "-q", "-o", str(req)], ws.root)
    audit = sh(["uvx", "pip-audit", "-r", str(req), "--disable-pip", "--no-deps",
                "--format", "json", "--progress-spinner", "off"], ws.root, check=False,
               timeout=AUDIT_TIMEOUT)
    vulns = vuln_findings(json.loads(audit.stdout or "{}"))
    tree = sh(["uv", "tree", "--outdated", "--depth", "1"], ws.root).stdout
    return vulns + outdated_findings(parse_outdated(tree), pypi_releases)


def upgrade(ws: Workspace, spec: str) -> None:
    sh(["uv", "lock", "-q", "--upgrade-package", spec], ws.root)
