"""Finding kinds: how each kind is fixed, what its change may touch, what its PR says.

The run loop and the shared gate checks (no suppressions, no new lint, suite green, LLM
review) are the same for every kind; only what lives here differs.
"""

from __future__ import annotations

import logging
import re
import tomllib
from abc import ABC, abstractmethod
from pathlib import Path

from . import deps, llm
from .core import Change, GateFailure, Job, lint
from .edits import EditError, apply_edits, parse_edits, python_block
from .findings import Finding, rank
from .gate import coverage_violations, is_test_path, line_is_covered
from .workspace import TestRun, Workspace, ruff_fix

log = logging.getLogger("greenkeep")

URGENT_RANK = 2  # S/F/B ruff rules are bugs: "fix:", and they go before tests
OUTDATED_MIN_COVERAGE = 60.0  # below it a green suite says too little about an upgrade
TEST_LINES_RATIO = 1.5  # new tests: at most 1.5x the module, but never below the floor
MIN_TEST_LINES = 120
CONTEXT_LINES = 200  # per file shown to the patcher
MAX_CONTEXT_FILES = 4
# work order: security, lint bugs, missing tests, upgrades, lint style
SECURITY, BUGS, MISSING_TESTS, UPGRADES, STYLE = range(5)


def _percent(cov: dict, path: str) -> float:
    return cov.get("files", {}).get(path, {}).get("summary", {}).get("percent_covered", 0.0)


class Kind(ABC):
    """Defaults: an LLM-reviewed change with no extra rules. Kinds override what differs."""
    gate_note = ""
    exempt_from_budget = False

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
            return Change([f.path], "ruff --fix", deterministic=True)
        source = (job.ws.root / f.path).read_text()
        reply = llm.ask(llm.FIX_SYSTEM, llm.fix_prompt(f, source, feedback), job.opts.model)
        edited = apply_edits(job.ws.root, parse_edits(reply.text), job.ws.tracked())
        return Change(edited, reply.model, reply.prompt_sha)

    def check_scope(self, job: Job, f: Finding, paths: list[str]) -> None:
        if not is_test_path(f.path) and any(map(is_test_path, paths)):
            raise GateFailure(f"a source fix may not touch tests: {paths}")
        if bad := coverage_violations(job.ws.git("diff", "-U0"), job.base.tests.cov):
            raise GateFailure(f"changed lines were not covered before the change: {bad}")
        if any(a.fingerprint == f.fingerprint for a in lint(job.ws, f.path)):
            raise GateFailure("ruff still reports the finding")


def characterization_path(f: Finding, root: Path) -> str:
    """Where the repo would put it: tests/<subpackage>/test_<module>.py when that folder
    exists, else tests/; the full dotted name only if the short one is taken."""
    parts = llm.module_name(f.path).split(".")
    folder = Path("tests", *parts[1:-1])
    if not (root / folder).is_dir():
        folder = Path("tests")
    name = f"test_{parts[-1]}.py"
    if (root / folder / name).exists():
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
    dotted = llm.module_name(f.path)
    parent, _, leaf = dotted.rpartition(".")
    imports = re.compile(rf"\b{re.escape(dotted)}\b|from {re.escape(parent)} import .*\b{leaf}\b")
    on_the_way = Path(characterization_path(f, ws.root)).parents
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


class Untested(Kind):
    gate_note = ("only test files added, within the line budget; full test suite green; "
                 "module coverage grew; no new lint findings.")

    def commit_type(self, f: Finding) -> str:
        return "test"

    def priority(self, f: Finding) -> int:
        return MISSING_TESTS

    def attemptable(self, job: Job, f: Finding) -> bool:
        return not (job.ws.root / characterization_path(f, job.ws.root)).exists()

    def produce(self, job: Job, f: Finding, feedback: str) -> Change:
        rel = characterization_path(f, job.ws.root)
        target = job.ws.root / rel
        if target.exists():
            raise GateFailure(f"{rel} already exists")
        source = (job.ws.root / f.path).read_text()
        context = related_context(job.ws, f)
        prompt = llm.characterization_prompt(f, source, context, line_budget(source), feedback)
        reply = llm.ask(llm.TEST_SYSTEM, prompt, job.opts.model)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            code = python_block(reply.text)
        except EditError:
            log.debug("  reply without a code block:\n%s", reply.text)
            raise
        target.write_text(code)
        ruff_fix(job.ws, rel)  # safe fixes only: an unused import must not cost an attempt
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


KINDS: dict[str, Kind] = {"ruff": Lint(), "untested": Untested(), "vuln": Vulnerability(),
                          "outdated": Outdated()}


def kind_of(f: Finding) -> Kind:
    return KINDS[f.kind]

