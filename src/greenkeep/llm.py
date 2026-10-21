"""The patcher, reviewer and bug hunt roles: fixed prompts, one finding per call, on Claude Code.

Both run inside the clone with read-only tools. The patcher reads what the module it tests
leans on (without that it wrote tool calls as text and no code); the reviewer judges the
change against the repo itself (existing tests, style, fixtures), not the diff alone. Predictability comes from the
pinned models and prompts.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from claude_cli import run_claude
from claude_cli.parsing import extract_json_or_none

from .findings import Finding

LLM_TIMEOUT = 600  # claude -p on a 20k-char prompt has hit 300s
REVIEW_TIMEOUT = 900
READ_TOOLS = "Read,Grep,Glob"  # neither role may write: greenkeep applies the reply
# none of the owner's Claude Code settings: their hooks, plugins and CLAUDE.md would make a
# role's prompt depend on the machine it runs on (and cost a third more time and tokens)
SETTING_SOURCES = ""

log = logging.getLogger("greenkeep")


def _claude(prompt: str, model: str, effort: str, root: Path, timeout: int):
    """One read-only Claude Code call in the clone; its duration goes to the log."""
    start = time.monotonic()
    try:
        return run_claude(prompt, model=model, effort=effort, tools=READ_TOOLS, cwd=root,
                          backend="cli", timeout=timeout, setting_sources=SETTING_SOURCES)
    finally:
        log.debug("  %s (%s) took %.0fs", model, effort, time.monotonic() - start)
EXCERPT_RADIUS = 40
WHOLE_FILE_LINES = 300
MAX_CLAIMS = 3  # per hunt: the Skeptic and the patcher pay for every one

FIX_SYSTEM = """You fix exactly one lint finding in a Python repository.
Rules:
- Change as little code as possible. Never change behaviour, public names or formatting
  elsewhere in the file.
- You may read the repository with the read-only tools first.
- Reply ONLY with one or more SEARCH/REPLACE blocks in this exact format:
path/to/file.py
<<<<<<< SEARCH
exact lines copied from the file
=======
replacement lines
>>>>>>> REPLACE
- SEARCH must copy the current lines character for character and be unique in the file.
"""

TEST_SYSTEM = """You write characterization tests: pytest tests that pin down what the
code does today, so it can be changed safely later.
Rules:
- Test the current behaviour as it is, even if it looks wrong. Do not modify the code.
- Pure, fast, deterministic tests: no network, no real external services, no sleeping,
  no writing outside pytest's tmp_path. Mock subprocess, HTTP clients and clocks.
- Import the module the way the package is installed (the import path is given).
- Match the repo's existing tests (shown when there are any): their style, fixtures,
  conftest, async style and pytest settings. Do not repeat what they already cover.
- Few, meaningful tests: the main behaviours and edge cases, within the line budget given.
  No tests that restate constants or re-implement the code under test to compare with it.
  Do not pin accidental crashes (ZeroDivisionError, KeyError, IndexError on bad input);
  test raised exceptions only when the code raises them on purpose.
- Do not mock the unit under test or replace a database fixture the repo already has
  with mocks; mock only the outside world.
- A plain test file: no section banners, no "characterization" wording.
- You may read the repository with the read-only tools first.
- Reply ONLY with one ```python block containing the complete test file.
"""


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    prompt_sha: str


def ask(system: str, prompt: str, model: str, root: Path) -> Reply:
    """The patcher, in the clone at `root`: it may read the repo, its reply is the change."""
    res = _claude(f"{system}\n{prompt}", model, "medium", root, LLM_TIMEOUT)
    sha = hashlib.sha1((system + prompt).encode()).hexdigest()[:12]
    return Reply(res.text, model, sha)


def _excerpt(source: str, line: int) -> str:
    lines = source.splitlines()
    if len(lines) <= WHOLE_FILE_LINES:
        return source
    lo, hi = max(line - EXCERPT_RADIUS, 1), min(line + EXCERPT_RADIUS, len(lines))
    return "\n".join(lines[lo - 1:hi])


def _with_feedback(parts: list[str], feedback: str) -> str:
    """The prompt, plus why the previous attempt failed: the only thing that differs
    between attempts, so a retry is a correction rather than a re-roll."""
    return "\n".join([*parts, "", f"Your previous attempt failed: {feedback}"] if feedback
                     else parts)


def fix_prompt(f: Finding, source: str, feedback: str) -> str:
    lines = source.splitlines()
    line_text = lines[f.line - 1] if 0 < f.line <= len(lines) else ""
    parts = [f"Finding: {f.rule} {f.message}", f"File: {f.path}, line {f.line}:",
             f"    {line_text.strip()}", "", f"Current contents of {f.path}:",
             "```python", _excerpt(source, f.line), "```"]
    return _with_feedback(parts, feedback)


def module_name(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def characterization_prompt(f: Finding, source: str, context: dict[str, str], budget: int,
                            feedback: str) -> str:
    """`context`: path -> text of the repo's related tests, conftest and pytest settings."""
    parts = [f"Module {f.path} (import as `{module_name(f.path)}`) is {f.message}.",
             f"Write tests for its public functions and classes, at most {budget} lines.",
             "", "```python", source, "```"]
    for path, text in context.items():
        parts += ["", f"Existing {path}:", "```", text, "```"]
    return _with_feedback(parts, feedback)


BUG_SYSTEM = """You fix exactly one bug in a Python repository, test first.
Rules:
- Add one pytest test that fails on the current code because of the stated bug and
  passes once it is fixed, in a NEW test file under the repo's tests folder (never edit
  existing tests or conftest.py); match the repo's test style.
- Fix the bug in the stated file with the smallest change. Do not touch other code,
  public names or formatting.
- Pure, fast, deterministic test: no network, no real external services, no sleeping.
- You may read the repository with the read-only tools first.
- Reply ONLY with SEARCH/REPLACE blocks in this exact format:
path/to/file.py
<<<<<<< SEARCH
exact lines copied from the file
=======
replacement lines
>>>>>>> REPLACE
- SEARCH must copy the current lines character for character and be unique in the file.
  For the new test file leave SEARCH empty and put the whole file in the replacement.
"""


def bug_prompt(f: Finding, source: str, context: dict[str, str], feedback: str) -> str:
    parts = [f"Bug in {f.path} (import as `{module_name(f.path)}`), line {f.line}:",
             f.message, "", f"Current contents of {f.path}:", "```python", source, "```"]
    for path, text in context.items():
        parts += ["", f"Existing {path}:", "```", text, "```"]
    return _with_feedback(parts, feedback)


HUNT_SYSTEM = """You look for real bugs in one module of a Python repository.
A bug is code that gives a wrong result, raises, or corrupts data on an input or state
its callers can really produce. Not a bug: style, naming, missing type hints, performance,
missing validation of inputs no caller passes, behaviour the docstring or tests intend.
Not reportable here: races, timing and ordering between threads, tasks or processes,
signals, and anything else a test can only show with sleeps or luck.
Report only bugs a short, deterministic pytest test can show through the module's own
functions, without network or external services. Read the module, its callers and its tests with
the read-only tools first. Report at most 3; none is a fine answer.
Reply ONLY with JSON: {"bugs": [{"line": <int>, "title": "<under 60 chars>",
"why": "<the input, what happens, what should happen>"}]}
"""

SKEPTIC_SYSTEM = """Another model claims these bugs in one module of a Python repository.
Try to refute each claim: read the code, its callers and its tests with the read-only
tools. A claim stands only if you can name a concrete input a caller can really pass,
what the code does with it, and why that is wrong rather than intended. Reject anything
speculative, stylistic or already handled. Reject a claim when a comment, docstring,
test or the surrounding code suggests the behaviour is a choice (a cancel that wins over
a clean exit, a default that drops data on purpose). Reject races and timing claims: no
short deterministic test can show them.
Reply ONLY with JSON: {"keep": [<indexes of the claims that stand>]}
"""


@dataclass(frozen=True)
class Claim:
    line: int
    title: str
    why: str


def _claude_json(system: str, prompt: str, model: str, root: Path) -> object:
    res = _claude(f"{system}\n{prompt}", model, "high", root, REVIEW_TIMEOUT)
    return extract_json_or_none(res.text)


def hunt(path: str, model: str, root: Path) -> list[Claim] | None:
    """The Hunter: suspected bugs in one module; None for a reply it cannot read."""
    data = _claude_json(HUNT_SYSTEM, f"Module: {path}", model, root)
    bugs = data.get("bugs") if isinstance(data, dict) else None
    if not isinstance(bugs, list):
        return None
    return [Claim(b["line"], str(b["title"]), str(b["why"])) for b in bugs
            if isinstance(b, dict) and isinstance(b.get("line"), int)
            and b.get("title") and b.get("why")][:MAX_CLAIMS]


def doubt(path: str, claims: list[Claim], model: str, root: Path) -> list[Claim] | None:
    """The Skeptic: only the claims it could not refute; None for a reply it cannot read."""
    listed = "\n".join(f"{i}. line {c.line}: {c.title}: {c.why}" for i, c in enumerate(claims))
    data = _claude_json(SKEPTIC_SYSTEM, f"Module: {path}\nClaims:\n{listed}", model, root)
    keep = data.get("keep") if isinstance(data, dict) else None
    if not isinstance(keep, list):
        return None
    return [c for i, c in enumerate(claims) if i in keep]


READY_SYSTEM = """You improve how one repository looks to a stranger: one change to one file.
Rules:
- Write only facts you checked in the repository with the read-only tools: the real
  install command, entry points from pyproject.toml, what the code does. Never invent
  features, badges, links or commands.
- Match the file's language (English or Russian), tone and heading style. Keep it short:
  a README section is a heading, one sentence and a code block; a description is one line.
- Touch nothing else in the file.
- Reply ONLY with SEARCH/REPLACE blocks in this exact format:
path/to/file
<<<<<<< SEARCH
exact lines copied from the file
=======
replacement lines
>>>>>>> REPLACE
- SEARCH must copy the current lines character for character and be unique in the file.
"""


def ready_prompt(f: Finding, text: str, feedback: str) -> str:
    return _with_feedback([f"Finding: {f.message}", "", f"Current contents of {f.path}:",
                           "```", text, "```"], feedback)


REVIEW_SYSTEM = """You review one automated change to a Python repository before it is merged
without a human. The change already passed: full test suite green, changed lines were
covered by tests before the change, no new lint findings, no tool suppressions.
Approve only if ALL hold:
- It addresses the stated finding and nothing else (no drive-by edits).
- A lint fix keeps runtime behaviour identical.
- A bug fix: the new test shows the stated bug through the module's real API, and the
  fix is the smallest change that makes it pass without changing other behaviour.
- New tests assert real current behaviour of the module: no tautologies (`assert True`,
  asserting a mock returns what it was told), the unit under test is not mocked away,
  and they are deterministic (no network, real clock, sleep, randomness without seed).
- New tests read like the repo's own: same place, naming, fixtures and style; they do
  not duplicate existing tests, restate constants, pin accidental crashes or bloat.
- A README or pyproject.toml change states only what is true of the repo (commands,
  entry points, features) and matches the file's language and style.
- The repo would look better to a senior reviewer with this change than without it.
You are inside the repository with the change applied: read the files you need (the
changed ones, the code they touch, neighbouring tests). Change nothing. Never open .env.
Reply ONLY with JSON: {"approve": true|false, "reason": "<one sentence>"}
"""


@dataclass(frozen=True)
class Verdict:
    approve: bool
    reason: str
    model: str


def review_prompt(f: Finding, diff: str) -> str:
    return "\n".join([f"Finding: {f.rule} at {f.path}:{f.line}: {f.message}", "",
                      "Change (git diff):", "```diff", diff, "```"])


def review(f: Finding, diff: str, model: str, root: Path) -> Verdict:
    """Claude Code in the clone at `root`. Fail closed: a reply that is not a clear
    approval is a rejection."""
    res = _claude(f"{REVIEW_SYSTEM}\n{review_prompt(f, diff)}", model, "high", root,
                  REVIEW_TIMEOUT)
    data = extract_json_or_none(res.text)
    if not isinstance(data, dict):
        return Verdict(False, f"unreadable review reply: {res.text[:200]!r}", model)
    return Verdict(data.get("approve") is True, str(data.get("reason", "")), model)
