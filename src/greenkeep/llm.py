"""The patcher role: fixed prompts, one finding per call, through the OmniRoute HTTP API.

No temperature is sent: on OmniRoute that switches on a semantic cache whose key
ignores the system prompt. Predictability comes from the pinned model and prompts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from claude_cli.http_api import omniroute_chat

from .findings import Finding

LLM_TIMEOUT = 300
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
- Reply ONLY with one ```python block containing the complete test file.
"""


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    prompt_sha: str


def ask(system: str, prompt: str, model: str) -> Reply:
    res = omniroute_chat(prompt, model=model, system_prompt=system, timeout=LLM_TIMEOUT)
    sha = hashlib.sha1((system + prompt).encode()).hexdigest()[:12]
    return Reply(res.text, str(res.raw.get("model") or model), sha)


def _excerpt(source: str, line: int) -> str:
    lines = source.splitlines()
    if len(lines) <= WHOLE_FILE_LINES:
        return source
    lo, hi = max(line - EXCERPT_RADIUS, 1), min(line + EXCERPT_RADIUS, len(lines))
    return "\n".join(lines[lo - 1:hi])


def fix_prompt(f: Finding, source: str, line_text: str, feedback: str) -> str:
    parts = [f"Finding: {f.rule} {f.message}", f"File: {f.path}, line {f.line}:",
             f"    {line_text.strip()}", "", f"Current contents of {f.path}:",
             "```python", _excerpt(source, f.line), "```"]
    if feedback:
        parts += ["", f"Your previous attempt failed: {feedback}"]
    return "\n".join(parts)


def module_name(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def characterization_prompt(f: Finding, source: str, feedback: str) -> str:
    parts = [f"Module {f.path} (import as `{module_name(f.path)}`) is {f.message}.",
             "Write characterization tests for its public functions and classes.",
             "", "```python", source, "```"]
    if feedback:
        parts += ["", f"Your previous attempt failed: {feedback}"]
    return "\n".join(parts)
