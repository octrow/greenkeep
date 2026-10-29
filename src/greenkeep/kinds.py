"""Finding kinds: how each kind is fixed, what its change may touch, what its PR says.

The run loop and the shared gate checks (no suppressions, no new lint, suite green, LLM
review) are the same for every kind; only what lives here differs.
"""

from __future__ import annotations

import ast
import glob
import logging
import re
import tomllib
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path

from . import deps, llm, ready
from .core import Change, GateFailure, Job, lint
from .edits import EditError, apply_edits, parse_edits, python_block
from .findings import Finding, rank
from .gate import coverage_violations, is_test_path, line_is_covered
from .github import web_url
from .workspace import (
    PYTEST_FAILED,
    TestRun,
    Workspace,
    WorkspaceError,
    ruff_fix,
    ruff_fix_new_test,
    test_file_exit,
)

log = logging.getLogger("greenkeep")

URGENT_RANK = 2  # S/F/B ruff rules are bugs: "fix:", and they go before tests
OUTDATED_MIN_COVERAGE = 60.0  # below it a green suite says too little about an upgrade
TEST_LINES_RATIO = 1.5  # new tests: at most 1.5x the module, but never below the floor
# the patcher overshoots a line budget by 5-20%: it is asked for less than the gate allows
PROMPT_BUDGET_SHARE = 0.85
MIN_TEST_LINES = 120
CONTEXT_LINES = 200  # per file shown to the patcher
MAX_CONTEXT_FILES = 4
# work order: security, lint bugs, missing tests, upgrades, readiness, lint style
SECURITY, BUGS, MISSING_TESTS, UPGRADES, READINESS, STYLE = range(6)
# clients of services a clone does not run: tests for such a module only mock them
SERVICE_CLIENTS = frozenset({
    "psycopg", "psycopg2", "asyncpg", "pymysql", "aiomysql", "redis", "pymongo", "motor",
    "pika", "aio_pika", "aiokafka", "kafka", "elasticsearch", "clickhouse_driver", "boto3"})


def _percent(cov: dict, path: str) -> float:
    return cov.get("files", {}).get(path, {}).get("summary", {}).get("percent_covered", 0.0)


# where the patcher fails most: new tests for untested code, bug fixes, complexity refactors
HARD_KINDS = frozenset({"untested", "bug"})
HARD_RULES = frozenset({"C901", "PLR0912", "PLR0915"})


def is_hard(f: Finding) -> bool:
    return f.kind in HARD_KINDS or f.rule in HARD_RULES


def patch(job: Job, system: str, prompt: str, hard: bool) -> llm.Reply:
    """The patcher's reply; during a boost a hard change gets the planner's plan first."""
    if not (hard and job.opts.planner):
        return llm.ask(system, prompt, job.opts.model, job.ws.root)
    steps = llm.plan(prompt, job.opts.planner, job.ws.root)
    log.debug("  plan by %s:\n%s", job.opts.planner, steps)
    reply = llm.ask(system, f"{prompt}\n\nFollow this plan from a senior engineer who read "
                    f"the repository:\n{steps}", job.opts.model, job.ws.root)
    return replace(reply, model=f"{reply.model} + plan by {job.opts.planner}")


class Kind(ABC):
    """Defaults: an LLM-reviewed change with no extra rules. Kinds override what differs."""
    gate_note = ""
    exempt_from_budget = False
    retries = True  # False: the same input gives the same failure, a retry only costs time

    @abstractmethod
    def priority(self, f: Finding) -> int:
        """Work order across kinds, lowest first."""

    def commit_type(self, f: Finding) -> str:
        return "chore"

    def attemptable(self, job: Job, f: Finding) -> bool:
        return True

    @abstractmethod
    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        """Make the change in the clean clone, or raise GateFailure / EditError."""

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        """Called before the suite runs; raise GateFailure for a change out of bounds."""

    def check_after(self, job: Job, f: Finding, after: TestRun) -> None:
        """Called after a green suite, with its coverage."""


class Lint(Kind):
    gate_note = ("full test suite green; every changed line was covered before the change; "
                 "no new lint findings.")

    def commit_type(self, f: Finding) -> str:
        return "fix" if rank(f.rule) <= URGENT_RANK else "chore"

    def priority(self, f: Finding) -> int:
        return BUGS if rank(f.rule) <= URGENT_RANK else STYLE

    def attemptable(self, job: Job, f: Finding) -> bool:
        # an uncovered line is not attempted: it becomes workable once tests cover it
        return line_is_covered(job.base.tests.cov, f.path, f.line)

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        if f.fixable and not feedback:
            ruff_fix(job.ws, f.path, f.rule)
            # the fix may leave lint of its own (UP035 -> unsorted imports): fix only that,
            # never what was there before, so the PR still resolves one finding
            new = {a.rule for a in lint(job.ws, f.path)
                   if a.fixable and a.fingerprint not in job.base.lint}
            for rule in sorted(new):
                ruff_fix(job.ws, f.path, rule)
            return Change([f.path], "ruff --fix", deterministic=True)
        source = (job.ws.root / f.path).read_text()
        reply = patch(job, llm.FIX_SYSTEM, llm.fix_prompt(f, source, feedback), is_hard(f))
        edited = apply_edits(job.ws.root, parse_edits(reply.text), job.ws.tracked())
        return Change(edited, reply.model, reply.prompt_sha)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        if paths != [f.path]:
            raise GateFailure(f"a lint fix may change only {f.path}: {paths}")
        if bad := coverage_violations(job.ws.git("diff", "-U0"), job.base.tests.cov):
            raise GateFailure(f"changed lines were not covered before the change: {bad}")
        if any(a.fingerprint == f.fingerprint for a in lint(job.ws, f.path)):
            raise GateFailure("ruff still reports the finding")


def characterization_path(f: Finding, root: Path, test_root: str = "") -> str:
    """Where the repo would put it: tests/<subpackage>/test_<module>.py under the test root
    when that folder exists, else tests/, or the test root itself when the repo has no
    tests/ but keeps test files there; the full dotted name if a test file anywhere in the
    repo has the short one (pytest's default import mode fails on two same-named files)."""
    parts = llm.module_name(f.path, test_root).split(".")
    folder = Path(test_root, "tests", *parts[1:-1])
    if not (root / folder).is_dir():
        folder = Path(test_root, "tests")
    if not (root / folder).is_dir() and any((root / test_root).glob("test_*.py")):
        folder = Path(test_root)  # the repo keeps its tests at the test root, not in tests/
    name = f"test_{parts[-1]}.py"
    if glob.glob(f"**/{name}", root_dir=root, recursive=True):  # skips .venv, .git
        name = f"test_{'_'.join(parts)}.py"
    return str(folder / name)


def line_budget(source: str) -> int:
    return max(MIN_TEST_LINES, int(len(source.splitlines()) * TEST_LINES_RATIO))


def _head(text: str) -> str:
    return "\n".join(text.splitlines()[:CONTEXT_LINES])


def related_context(ws: Workspace, f: Finding) -> dict[str, str]:
    """What the patcher needs to write tests like the repo's own: the tests that already
    import the module, the conftests on the way to its test file, pytest/ruff settings."""
    return {**_related_tests(ws, f), **_tool_settings(ws.root)}


def _related_tests(ws: Workspace, f: Finding) -> dict[str, str]:
    dotted = llm.module_name(f.path, ws.tests)
    parent, _, leaf = dotted.rpartition(".")
    imports = re.compile(rf"\b{re.escape(dotted)}\b|from {re.escape(parent)} import .*\b{leaf}\b")
    on_the_way = Path(characterization_path(f, ws.root, ws.tests)).parents
    found = {}
    for path in sorted(p for p in ws.tracked() if is_test_path(p)):
        if len(found) >= MAX_CONTEXT_FILES:
            break
        text = (ws.root / path).read_text(errors="replace")
        if Path(path).name == "conftest.py":
            wanted = Path(path).parent in on_the_way
        else:
            wanted = bool(imports.search(text))
        if wanted:
            found[path] = _head(text)
    return found


def _tool_settings(root: Path) -> dict[str, str]:
    try:
        tool = tomllib.loads((root / "pyproject.toml").read_text()).get("tool", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    settings = {k: tool[k] for k in ("pytest", "ruff") if k in tool}
    return {"pyproject.toml [tool.pytest] / [tool.ruff]": repr(settings)} if settings else {}


def needs_service(source: str) -> bool:
    """The module imports a database or broker client, so only a live service covers it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or ""]
        else:
            continue
        if any(n.split(".")[0] in SERVICE_CLIENTS for n in names):
            return True
    return False


class Untested(Kind):
    gate_note = ("only test files added, within the line budget; full test suite green; "
                 "module coverage grew; no new lint findings.")

    def commit_type(self, f: Finding) -> str:
        return "test"

    def priority(self, f: Finding) -> int:
        return MISSING_TESTS

    def attemptable(self, job: Job, f: Finding) -> bool:
        if (job.ws.root / characterization_path(f, job.ws.root, job.ws.tests)).exists():
            return False
        return not needs_service((job.ws.root / f.path).read_text())

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        rel = characterization_path(f, job.ws.root, job.ws.tests)
        target = job.ws.root / rel
        if target.exists():
            raise GateFailure(f"{rel} already exists")
        source = (job.ws.root / f.path).read_text()
        context = related_context(job.ws, f)
        # the reset between attempts removes the file; scratch/ outlives it
        kept = job.ws.scratch / f"previous-{target.name}"
        previous = kept.read_text() if feedback and kept.is_file() else ""
        asked = int(line_budget(source) * PROMPT_BUDGET_SHARE)
        prompt = llm.characterization_prompt(f, source, context, asked, feedback,
                                             job.ws.tests, previous)
        reply = patch(job, llm.TEST_SYSTEM, prompt, hard=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            code = python_block(reply.text)
        except EditError:
            log.debug("  reply without a code block:\n%s", reply.text)
            raise
        target.write_text(code)
        ruff_fix_new_test(job.ws, rel)  # an unused import or variable must not cost an attempt
        kept.write_text(target.read_text())
        return Change([rel], reply.model, reply.prompt_sha)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        if not all(is_test_path(p) for p in paths):
            raise GateFailure(f"characterization tests may only add test files: {paths}")
        budget = line_budget((job.ws.root / f.path).read_text())
        for p in paths:
            if (n := len((job.ws.root / p).read_text().splitlines())) > budget:
                raise GateFailure(f"{p} has {n} lines, the budget is {budget}: keep only "
                                  "the tests that matter")

    def check_after(self, job: Job, f: Finding, after: TestRun) -> None:
        if _percent(after.cov, f.path) <= _percent(job.base.tests.cov, f.path):
            raise GateFailure(f"coverage of {f.path} did not grow")


class Dependency(Kind):
    """A lockfile-only upgrade of one package; `uv lock` writes it, so nothing to review."""
    gate_note = "only uv.lock changed; full test suite green on the upgraded lock."
    # ponytail: a network error in `uv lock` parks it too; tell them apart if that bites
    retries = False

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        deps.upgrade(job.ws, f.rule)
        return Change([deps.LOCK], "uv lock", deterministic=True)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        if paths != [deps.LOCK]:
            raise GateFailure(f"an upgrade may only change {deps.LOCK}: {paths}")


class Vulnerability(Dependency):
    exempt_from_budget = True  # security does not wait for tomorrow's budget

    def commit_type(self, f: Finding) -> str:
        return "fix"

    def priority(self, f: Finding) -> int:
        return SECURITY


class Outdated(Dependency):
    def priority(self, f: Finding) -> int:
        return UPGRADES

    def attemptable(self, job: Job, f: Finding) -> bool:
        tests = job.base.tests
        total = tests.cov.get("totals", {}).get("percent_covered", 0.0)
        return not tests.no_tests and total >= OUTDATED_MIN_COVERAGE


class Bug(Kind):
    """A bug the hunt found: a test that fails before the fix and passes after it is the
    proof, so no model has the last word on whether the bug is real."""
    gate_note = ("the new test fails without the fix; full test suite green with it; every "
                 "changed source line was covered before the change; no new lint findings.")

    def commit_type(self, f: Finding) -> str:
        return "fix"

    def priority(self, f: Finding) -> int:
        return BUGS

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        source = (job.ws.root / f.path).read_text()
        prompt = llm.bug_prompt(f, source, related_context(job.ws, f), feedback, job.ws.tests)
        reply = patch(job, llm.BUG_SYSTEM, prompt, hard=True)
        tracked = job.ws.tracked()
        edited = apply_edits(job.ws.root, parse_edits(reply.text), tracked, new_tests=True)
        for path in set(edited) - tracked:  # the new test file
            ruff_fix_new_test(job.ws, path)
        return Change(edited, reply.model, reply.prompt_sha)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        tests = [p for p in paths if p != f.path]
        # tests may only be added: an edited existing test or conftest could redefine
        # "correct", but a test appended where the repo keeps them is what a reviewer wants
        added = [p for p in tests if is_test_path(p) and Path(p).name != "conftest.py"
                 and _only_added(job, p)]
        if f.path not in paths or not tests or added != tests:
            raise GateFailure(f"a bug fix changes {f.path} and adds tests (new lines in test "
                              f"files, never changed or removed ones), nothing else: {paths}")
        if bad := coverage_violations(job.ws.git("diff", "-U0", "--", f.path),
                                      job.base.tests.cov):
            raise GateFailure(f"changed lines were not covered before the change: {bad}")
        for test in added:
            if (code := _exit_without_fix(job, f, test)) != PYTEST_FAILED:
                raise GateFailure(f"{test} does not fail on the unfixed code (pytest exit "
                                  f"{code}; a collection error or a timeout is not a "
                                  "failure): it does not show the bug")


def _only_added(job: Job, path: str) -> bool:
    """A new file, or an existing one whose diff removes and changes no line."""
    diff = job.ws.git("diff", "-U0", "HEAD", "--", path)
    return not any(line.startswith("-") and not line.startswith("---")
                   for line in diff.splitlines())


def _exit_without_fix(job: Job, f: Finding, test: str) -> int:
    fixed = (job.ws.root / f.path).read_text()
    try:
        job.ws.git("checkout", "-q", "HEAD", "--", f.path)
        return test_file_exit(job.ws, test)
    except WorkspaceError:
        return -1  # a hang without the fix proves nothing
    finally:
        (job.ws.root / f.path).write_text(fixed)


class Readiness(Kind):
    """A failed readiness check: README or pyproject.toml only, never code."""
    gate_note = ("only README.md or pyproject.toml's [project] changed; the check passes now; "
                 "full test suite green.")

    def commit_type(self, f: Finding) -> str:
        return "docs"

    def priority(self, f: Finding) -> int:
        return READINESS

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        if fix := ready.FIXES.get(f.rule):
            fix(job.ws.root, web_url(job.ws.origin))
            return Change([f.path], "greenkeep", deterministic=True)
        prompt = llm.ready_prompt(f, (job.ws.root / f.path).read_text(), feedback)
        reply = llm.ask(llm.READY_SYSTEM, prompt, job.opts.model, job.ws.root)
        edited = apply_edits(job.ws.root, parse_edits(reply.text), job.ws.tracked())
        return Change(edited, reply.model, reply.prompt_sha)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        if paths != [f.path]:
            raise GateFailure(f"a readiness fix changes {f.path} only: {paths}")
        if field := ready.FIELDS.get(f.rule):
            _check_one_field(job, f.path, field)
        if any(a.fingerprint == f.fingerprint
               for a in ready.collect(job.ws.root, web_url(job.ws.origin))):
            raise GateFailure(f"the check still fails: {f.message}")


def _check_one_field(job: Job, path: str, field: str) -> None:
    """pyproject.toml may change in the finding's own [project] field and nowhere else."""
    try:
        before = tomllib.loads(job.ws.git("show", f"HEAD:{path}"))
        after = tomllib.loads((job.ws.root / path).read_text())
    except tomllib.TOMLDecodeError as exc:
        raise GateFailure(f"{path} is no longer valid TOML: {exc}") from exc

    def rest(data: dict) -> dict:
        return {**data, "project": {**data.get("project", {}), field: None}}

    if rest(before) != rest(after):
        raise GateFailure(f"only [project] {field} of {path} may change")


KINDS: dict[str, Kind] = {"bug": Bug(), "ready": Readiness(), "ruff": Lint(),
                          "untested": Untested(), "vuln": Vulnerability(), "outdated": Outdated()}


def kind_of(f: Finding) -> Kind:
    return KINDS[f.kind]

