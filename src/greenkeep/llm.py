"""The patcher and reviewer roles: fixed prompts, one finding per call, on Claude Code.

The patcher runs without tools and sees only what the prompt carries. The reviewer runs
inside the clone with read-only tools, so it judges the change against the repo itself
(existing tests, style, fixtures), not the diff alone. Predictability comes from the
pinned models and prompts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from claude_cli import run_claude
from claude_cli.parsing import extract_json_or_none

from .findings import Finding

LLM_TIMEOUT = 300
REVIEW_TIMEOUT = 900
REVIEW_TOOLS = "Read,Grep,Glob"
EXCERPT_RADIUS = 40
WHOLE_FILE_LINES = 300

FIX_SYSTEM = """You fix exactly one lint finding in a Python repository.
Rules:
- Change as little code as possible. Never change behaviour, public names or formatting
  elsewhere in the file.
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
- Reply ONLY with one ```python block containing the complete test file.
"""


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    prompt_sha: str


def ask(system: str, prompt: str, model: str) -> Reply:
    res = run_claude(f"{system}\n{prompt}", model=model, effort="medium", backend="cli",
                     timeout=LLM_TIMEOUT)
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


REVIEW_SYSTEM = """You review one automated change to a Python repository before it is merged
without a human. The change already passed: full test suite green, changed lines were
covered by tests before the change, no new lint findings, no tool suppressions.
Approve only if ALL hold:
- It addresses the stated finding and nothing else (no drive-by edits).
- A lint fix keeps runtime behaviour identical.
- New tests assert real current behaviour of the module: no tautologies (`assert True`,
  asserting a mock returns what it was told), the unit under test is not mocked away,
  and they are deterministic (no network, real clock, sleep, randomness without seed).
- New tests read like the repo's own: same place, naming, fixtures and style; they do
  not duplicate existing tests, restate constants, pin accidental crashes or bloat.
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
    res = run_claude(f"{REVIEW_SYSTEM}\n{review_prompt(f, diff)}", model=model, effort="high",
                     tools=REVIEW_TOOLS, cwd=root, backend="cli", timeout=REVIEW_TIMEOUT)
    data = extract_json_or_none(res.text)
    if not isinstance(data, dict):
        return Verdict(False, f"unreadable review reply: {res.text[:200]!r}", model)
    return Verdict(data.get("approve") is True, str(data.get("reason", "")), model)
