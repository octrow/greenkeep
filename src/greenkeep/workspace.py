"""Throwaway clones and the commands run inside them. The owner's checkout is only
ever read (as a `--reference` object store and for its `.env`), never changed."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Target

log = logging.getLogger(__name__)

CACHE = Path.home() / ".cache/greenkeep"
TEST_TIMEOUT = 1800
PYTEST_NO_TESTS = 5
# kept across attempt resets: secrets copied in, and the synced venv
KEEP_ON_RESET = (".env", ".venv")
MIN_SECRET_LEN = 6  # shorter .env values (ports, flags) would mask half the output


class WorkspaceError(RuntimeError):
    """The repo cannot be worked on in this run (no pyproject, sync failed...)."""


def sh(args: list[str], cwd: Path, *, check: bool = True,
       timeout: int = TEST_TIMEOUT) -> subprocess.CompletedProcess:
    # our own venv must not leak into the target's `uv run`
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        proc = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                              check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"{' '.join(args[:3])}… timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise WorkspaceError(f"{' '.join(args[:3])}… exit {proc.returncode}: "
                             f"{(proc.stderr or proc.stdout)[-800:]}")
    return proc


@dataclass
class Workspace:
    name: str
    root: Path  # the clone
    scratch: Path  # coverage data etc., outside the clone so it is never committed
    base_branch: str = ""

    def git(self, *args: str, check: bool = True) -> str:
        return sh(["git", *args], self.root, check=check).stdout

    def reset(self) -> None:
        self.git("checkout", "-q", "-f", self.base_branch)
        self.git("reset", "-q", "--hard")
        # -x: ignored leftovers of a failed attempt must not help the next one pass
        excludes = [a for k in KEEP_ON_RESET for a in ("-e", k)]
        self.git("clean", "-fdxq", *excludes)

    def tracked(self) -> frozenset[str]:
        return frozenset(self.git("ls-files", "-z").split("\0")) - {""}

    def changed(self) -> list[str]:
        """Paths the attempt changed or added, except the kept secrets and venv."""
        def kept(name: str) -> bool:
            return name.split("/")[0] in KEEP_ON_RESET

        new = [n for n in self.git("ls-files", "-o", "--exclude-standard", "-z").split("\0")
               if n and not kept(n)]
        if new:  # intent-to-add: new files show up in `git diff` too (kept ones never are,
            self.git("add", "-N", "--", *new)  # or a hard reset would delete them)
        names = self.git("diff", "--name-only", "-z", "HEAD").split("\0")
        return [n for n in names if n and not kept(n)]

    def secrets(self) -> list[str]:
        """Values from the copied .env, to be masked in anything leaving the machine."""
        env = self.root / ".env"
        if not env.is_file():
            return []
        values = (ln.partition("=")[2].strip().strip("\"'") for ln in env.read_text().splitlines()
                  if "=" in ln and not ln.lstrip().startswith("#"))
        return [v for v in values if len(v) >= MIN_SECRET_LEN]

    def read_line(self, path: str, n: int) -> str:
        lines = (self.root / path).read_text(errors="replace").splitlines()
        return lines[n - 1] if 0 < n <= len(lines) else ""


def clone(target: Target) -> Workspace:
    """Clone the target's origin, borrowing objects (and .env) from its local checkout."""
    name, src = target.name, target.path
    origin = sh(["git", "remote", "get-url", "origin"], src, check=False).stdout.strip() \
        if src else target.origin
    if not origin:
        raise WorkspaceError(f"{name}: local checkout has no origin to clone")
    run_dir = CACHE / f"run-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    root, scratch = run_dir / name, run_dir / "scratch"
    scratch.mkdir(parents=True)
    reference = ["--reference-if-able", str(src)] if src else []
    sh(["git", "clone", "-q", *reference, origin, str(root)], run_dir)
    if src and (src / ".env").is_file():
        shutil.copy2(src / ".env", root / ".env")
    ws = Workspace(name, root, scratch)
    ws.base_branch = ws.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    return ws


def discard(ws: Workspace) -> None:
    shutil.rmtree(ws.root.parent, ignore_errors=True)


def sync(ws: Workspace) -> None:
    if not (ws.root / "pyproject.toml").is_file():
        raise WorkspaceError("no pyproject.toml: needs revival (not in v1)")
    sh(["uv", "sync", "-q"], ws.root)


@dataclass(frozen=True)
class TestRun:
    ok: bool
    cov: dict
    output: str
    no_tests: bool = False  # pytest collected nothing (exit 5)


def run_tests(ws: Workspace) -> TestRun:
    """Full suite under coverage. No tests at all counts as green with empty coverage."""
    data = ws.scratch / ".coverage"
    data.unlink(missing_ok=True)
    try:
        proc = sh(["uv", "run", "--with", "pytest", "--with", "coverage", "coverage", "run",
                   f"--data-file={data}", "--source=.", "--omit=tests/*,.venv/*",
                   "-m", "pytest", "-q", "-p", "no:cacheprovider"], ws.root, check=False)
    except WorkspaceError as exc:  # a hanging test is a red suite, not a crashed run
        return TestRun(False, {}, str(exc))
    tail = (proc.stdout + proc.stderr)[-3000:]
    if proc.returncode not in (0, PYTEST_NO_TESTS):
        return TestRun(False, {}, tail)
    # --source makes never-imported modules show up at 0%: that is what bootstraps a repo
    # without tests. With nothing measured at all, coverage refuses to report.
    out = ws.scratch / "cov.json"
    rep = sh(["uv", "run", "--with", "coverage", "coverage", "json", f"--data-file={data}",
              "-o", str(out), "-q"], ws.root, check=False)
    cov = json.loads(out.read_text()) if rep.returncode == 0 else {"files": {}}
    return TestRun(True, cov, tail, no_tests=proc.returncode == PYTEST_NO_TESTS)


def ruff_json(ws: Workspace, *paths: str) -> list[dict]:
    proc = sh(["uvx", "ruff", "check", "--output-format", "json", "--exit-zero",
               *(paths or ["."])], ws.root)
    return json.loads(proc.stdout or "[]")


def ruff_fix(ws: Workspace, path: str, rule: str | None = None) -> None:
    """Ruff's safe fixes on one file: one rule, or whatever the repo's own config selects."""
    select = ["--select", rule] if rule else []
    sh(["uvx", "ruff", "check", "--fix", *select, "--exit-zero", path], ws.root)
