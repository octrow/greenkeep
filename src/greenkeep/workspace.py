"""Throwaway clones and the commands run inside them. The owner's checkout is only
ever read (as a `--reference` object store and for its `.env`), never changed."""

from __future__ import annotations

import configparser
import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .config import Target

log = logging.getLogger(__name__)

CACHE = Path.home() / ".cache/greenkeep"
TEST_TIMEOUT = 1800
STALE_CLONE_SECONDS = 24 * 3600  # a run takes about an hour; older clones lost their run
PYTEST_NO_TESTS = 5
CRASH = "Fatal Python error"  # faulthandler's header, which pytest turns on
OUTPUT_TAIL = 3000
UNSAFE_OK = "F841,RUF059"  # unused variable, unused unpacked variable
# a boost also lints for real improvements: complexity, too many branches or statements,
# simpler and faster idioms, newer syntax. The gate still needs every changed line covered.
BOOST_RULES = ("C901", "PLR0912", "PLR0915", "SIM", "PERF", "UP", "RUF")
# ambiguous-unicode rules flag every Cyrillic letter in the owner's Russian docstrings;
# RUF100 calls a `noqa` for a rule the repo never selects "unused" once RUF is on
BOOST_IGNORE = "RUF001,RUF002,RUF003,RUF100"
# addopts that would break coverage: pytest-cov is disabled (its tracer hijacks ours), and
# xdist workers run outside `coverage run`. Each name also covers its `--opt=value` form.
DROPPED_OPTS = ("--cov", "--no-cov", "--dist", "--numprocesses", "-n")
DROPPED_WITH_VALUE = frozenset({"--cov", "--cov-report", "--cov-config", "--cov-fail-under",
                                "--cov-context", "--dist", "--numprocesses", "-n", "-p"})
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
    origin: str = ""  # clone URL; its https form links issues and PRs
    tests: str = ""  # test root: where the suite runs from, relative to `root`
    extra_rules: tuple[str, ...] = ()  # ruff rules beyond the repo's own config (a boost)

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


def _origin(target: Target) -> str:
    src = target.path
    return sh(["git", "remote", "get-url", "origin"], src, check=False).stdout.strip() \
        if src else target.origin


def remote_head(target: Target) -> str:
    """The commit the target's origin HEAD points at, without cloning ("" if unreachable)."""
    origin = _origin(target)
    proc = sh(["git", "ls-remote", origin, "HEAD"], Path.home(), check=False) if origin else None
    return proc.stdout.split()[0] if proc and proc.returncode == 0 and proc.stdout else ""


@cache
def tool_head() -> str:
    """greenkeep's own commit ("" when not run from a git checkout)."""
    here = Path(__file__).parent
    proc = sh(["git", "rev-parse", "HEAD"], here, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def clone(target: Target) -> Workspace:
    """Clone the target's origin, borrowing objects (and .env) from its local checkout."""
    name, src = target.name, target.path
    origin = _origin(target)
    if not origin:
        raise WorkspaceError(f"{name}: local checkout has no origin to clone")
    run_dir = CACHE / f"run-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    root, scratch = run_dir / name, run_dir / "scratch"
    scratch.mkdir(parents=True)
    reference = ["--reference-if-able", str(src)] if src else []
    sh(["git", "clone", "-q", *reference, origin, str(root)], run_dir)
    if src and (src / ".env").is_file():
        shutil.copy2(src / ".env", root / ".env")
    ws = Workspace(name, root, scratch, origin=origin, tests=target.tests,
                   extra_rules=BOOST_RULES if target.boost else ())
    ws.base_branch = ws.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    return ws


def discard(ws: Workspace) -> None:
    shutil.rmtree(ws.root.parent, ignore_errors=True)


def sweep() -> None:
    """Remove the clones of runs that never reached `discard`: a shutdown's SIGTERM ends a
    run without its `finally`, and each such clone holds a synced venv (up to 450 MB)."""
    cutoff = time.time() - STALE_CLONE_SECONDS
    for run_dir in CACHE.glob("run-*"):
        with contextlib.suppress(OSError):  # another run swept it first
            if run_dir.stat().st_mtime < cutoff:
                log.info("removing a stale clone: %s", run_dir.name)
                shutil.rmtree(run_dir, ignore_errors=True)


def sync(ws: Workspace) -> None:
    if not (ws.root / "pyproject.toml").is_file():
        raise WorkspaceError("no pyproject.toml: needs revival (not in v1)")
    # extras too: many repos declare pytest plugins as a `dev` extra, not a dependency group;
    # all packages: a workspace member's tests need its own dependencies
    sh(["uv", "sync", "-q", "--all-extras", "--all-packages"], ws.root)
    # a test root that is its own project (not a workspace member) runs on its own venv;
    # inexact: on a workspace member this must not strip what the root sync installed
    if ws.tests and (ws.root / ws.tests / "pyproject.toml").is_file():
        sh(["uv", "sync", "-q", "--all-extras", "--inexact"], ws.root / ws.tests)


@dataclass(frozen=True)
class TestRun:
    ok: bool
    cov: dict
    output: str
    no_tests: bool = False  # pytest collected nothing (exit 5)
    crashed: bool = False  # the interpreter died (faulthandler's header in the output)


def kept_addopts(addopts: str | list[str]) -> list[str]:
    """A repo's addopts without the coverage and xdist options (and `-p pytest_cov`)."""
    tokens = shlex.split(addopts) if isinstance(addopts, str) else list(addopts)
    kept, skip_value = [], False
    for i, tok in enumerate(tokens):
        if skip_value:
            skip_value = False
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else "-"
        if tok == "-p" and "cov" in nxt or tok.startswith(DROPPED_OPTS):
            skip_value = tok in DROPPED_WITH_VALUE and not nxt.startswith("-")
            continue
        kept.append(tok)
    return kept


# where pytest looks, in its own order; "" is pyproject's [tool.pytest(.ini_options)]
PYTEST_CONFIGS = (("pytest.toml", "pytest"), (".pytest.toml", "pytest"),
                  ("pytest.ini", "pytest"), (".pytest.ini", "pytest"), ("pyproject.toml", ""),
                  ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest"))


def _config_addopts(path: Path, section: str) -> str | list[str] | None:
    """The file's addopts, or None when it has no pytest section (pytest.ini always counts)."""
    if path.suffix == ".toml":
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError:
            return None
        table = data.get("tool", {}).get("pytest") if not section else data.get(section)
        return None if table is None else table.get("ini_options", table).get("addopts", [])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    if parser.has_section(section):
        return parser.get(section, "addopts", fallback="")
    return "" if path.name in ("pytest.ini", ".pytest.ini") else None


def repo_addopts(root: Path) -> list[str]:
    """The addopts pytest would read from `root`, minus the options that break coverage."""
    for name, section in PYTEST_CONFIGS:
        if (root / name).is_file() and (found := _config_addopts(root / name, section)) is not None:
            return kept_addopts(found)
    return []


def _addopts(ws: Workspace) -> list[str]:
    return ["-o", f"addopts={shlex.join(repo_addopts(ws.root / ws.tests))}"]


PYTEST_FAILED = 1  # tests ran and some failed; 2+ is a collection error or interrupt


def test_file_exit(ws: Workspace, path: str) -> int:
    """pytest's exit code for one test file (repo-relative), run from the test root."""
    rel = path.removeprefix(f"{ws.tests}/") if ws.tests else path
    # `-m`, like run_tests: the test root importable the same way. `-B`: the unfixed
    # module's .pyc would outlive a same-size, same-second restore of the fixed one
    return sh(["uv", "run", "--with", "pytest", "python", "-B", "-m", "pytest", "-q",
               "-p", "no:cacheprovider",
               *_addopts(ws), rel], ws.root / ws.tests, check=False).returncode


def suite_tail(text: str) -> str:
    """The tail of a suite's output that the log keeps. A crash keeps faulthandler's header
    and the repo's own frames: library frames and the extension module list pushed both
    out of the tail."""
    if CRASH not in text:
        return text[-OUTPUT_TAIL:]
    header, *frames = [ln for ln in text[text.index(CRASH):].splitlines()
                       if "/site-packages/" not in ln and not ln.startswith("Extension modules:")]
    # the header first even when the frames overflow: it is what says "crash"
    return header + "\n" + "\n".join(frames)[-(OUTPUT_TAIL - len(header) - 1):]


def baseline_tests(ws: Workspace) -> TestRun:
    """The baseline suite; a crash (a flaky segfault in Qt tests) runs it once more. Only
    here: after a patch, a crash that comes and goes may be the patch's own."""
    run = run_tests(ws)
    if run.crashed:
        log.info("  the suite crashed, running it once more:\n%s", run.output)
        run = run_tests(ws)
    return run


def run_tests(ws: Workspace) -> TestRun:
    """Full suite under coverage. No tests at all counts as green with empty coverage."""
    data = ws.scratch / ".coverage"
    data.unlink(missing_ok=True)
    cwd = ws.root / ws.tests
    try:
        proc = sh(["uv", "run", "--with", "pytest", "--with", "coverage", "coverage", "run",
                   f"--data-file={data}", "--source=.", "--omit=tests/*,.venv/*",
                   "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   # pytest-cov would hijack the tracer (0% everywhere): off, and its
                   # options dropped from the repo's addopts, which otherwise still apply
                   "-p", "no:pytest_cov", *_addopts(ws)], cwd, check=False)
    except WorkspaceError as exc:  # a hanging test is a red suite, not a crashed run
        return TestRun(False, {}, str(exc))
    tail = suite_tail(proc.stdout + proc.stderr)
    if proc.returncode not in (0, PYTEST_NO_TESTS):
        return TestRun(False, {}, tail, crashed=CRASH in proc.stdout + proc.stderr)
    # --source makes never-imported modules show up at 0%: that is what bootstraps a repo
    # without tests. With nothing measured at all, coverage refuses to report.
    out = ws.scratch / "cov.json"
    rep = sh(["uv", "run", "--with", "coverage", "coverage", "json", f"--data-file={data}",
              "-o", str(out), "-q"], cwd, check=False)
    cov = json.loads(out.read_text()) if rep.returncode == 0 else {"files": {}}
    if ws.tests:  # coverage names files from the test root; everything else from the repo
        cov["files"] = {f"{ws.tests}/{k}": v for k, v in cov.get("files", {}).items()}
    return TestRun(True, cov, tail, no_tests=proc.returncode == PYTEST_NO_TESTS)


def ruff_json(ws: Workspace, *paths: str) -> list[dict]:
    extra = (["--extend-select", ",".join(ws.extra_rules), "--extend-ignore", BOOST_IGNORE]
             if ws.extra_rules else [])
    proc = sh(["uvx", "ruff", "check", "--output-format", "json", "--exit-zero", *extra,
               *(paths or ["."])], ws.root)
    return json.loads(proc.stdout or "[]")


def ruff_fix(ws: Workspace, path: str, rule: str | None = None) -> None:
    """Ruff's safe fixes on one file: one rule, or whatever the repo's own config selects."""
    select = ["--select", rule] if rule else []
    sh(["uvx", "ruff", "check", "--fix", *select, "--exit-zero", path], ws.root)


def ruff_fix_new_test(ws: Workspace, path: str) -> None:
    """A test file greenkeep just wrote: safe fixes, plus the unsafe ones that only drop
    an unused name. Not every unsafe fix: E721's turns `type(x) == int` into `int == int`."""
    ruff_fix(ws, path)
    sh(["uvx", "ruff", "check", "--fix", "--unsafe-fixes", "--select", UNSAFE_OK,
        "--exit-zero", path], ws.root)
