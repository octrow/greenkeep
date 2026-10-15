"""Whole dry runs on throwaway repos: real git, uv, ruff and pytest; only the LLM is faked."""

import subprocess
from pathlib import Path

import pytest

from greenkeep import llm, run, workspace

PYPROJECT = '[project]\nname = "demo"\nversion = "0"\nrequires-python = ">=3.12"\n'
CODE = "import os\n\n\ndef add(a, b):\n    return a + b\n"
TEST = "from demo import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
BIG = "def a(x):\n    if x:\n        return 1\n    return 2\n\n\ndef b(y):\n    z = y * 2\n" \
      "    return z + 1\n"
CHAR_TEST = "```python\nfrom demo import a, b\n\n\ndef test_a():\n    assert a(1) == 1\n" \
            "    assert a(0) == 2\n\n\ndef test_b():\n    assert b(1) == 3\n```"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def make_repo(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    """A local checkout whose origin is a bare repo, like the owner's ~/dev clones."""
    monkeypatch.setattr(workspace, "CACHE", tmp_path / "cache")
    origin, src = tmp_path / "origin.git", tmp_path / "demo"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    for rel, text in {"pyproject.toml": PYPROJECT, **files}.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-qm", "init"],
                 ["remote", "add", "origin", str(origin)], ["push", "-q", "origin", "main"]):
        git(src, *args)
    return src


def dry_run(src: Path) -> tuple[run.State, Path]:
    state = run.State(Path(":memory:"))
    run.run_repo(src, run.Options(push=False, budget=3, model="x", keep=True), state)
    return state, next(workspace.CACHE.glob("run-demo-*/demo"))


def test_covered_lint_finding_is_fixed_by_ruff_and_checkout_untouched(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE, "tests/test_demo.py": TEST})
    monkeypatch.setattr(llm, "ask", lambda *a: pytest.fail("LLM must not be needed"))
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert "import os" not in (clone / "demo.py").read_text()
    msg = git(clone, "log", "-1", "--format=%B")
    assert msg.startswith("fix: F401") and run.TRAILER in msg
    assert "import os" in (src / "demo.py").read_text()


def test_repo_without_tests_gets_characterization_tests(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    monkeypatch.setattr(llm, "ask", lambda *a: llm.Reply(CHAR_TEST, "fake", "sha"))
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert git(clone, "log", "-1", "--format=%s").startswith("test: Add characterization")
    assert (clone / "tests/test_demo_characterization.py").is_file()


def test_bad_llm_reply_parks_finding_after_three_attempts(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    calls = []
    monkeypatch.setattr(llm, "ask", lambda *a: calls.append(a) or llm.Reply("no", "f", "s"))
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert len(calls) == run.MAX_ATTEMPTS
    assert state.status("demo", "untested:demo.py") == "parked"


def test_patch_that_adds_lint_findings_is_rejected_then_retried(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    # F841 has no safe automatic fix, so only a new attempt can get rid of it
    dirty = CHAR_TEST.replace("def test_a():\n", "def test_a():\n    unused = a(1)\n")
    replies, prompts = [dirty, CHAR_TEST], []

    def fake_ask(system, prompt, model):
        prompts.append(prompt)
        return llm.Reply(replies[len(prompts) - 1], "fake", "sha")

    monkeypatch.setattr(llm, "ask", fake_ask)
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert "adds lint findings" in prompts[1] and "F841" in prompts[1]
    assert "unused = a(1)" not in (clone / "tests/test_demo_characterization.py").read_text()


UNUSED_VAR = "def add(a, b):\n    total = 0\n    return a + b\n"
NOQA_FIX = "demo.py\n<<<<<<< SEARCH\n    total = 0\n=======\n    total = 0  # noqa: F841\n" \
           ">>>>>>> REPLACE"
REAL_FIX = "demo.py\n<<<<<<< SEARCH\n    total = 0\n    return\n=======\n    return\n" \
           ">>>>>>> REPLACE"


def test_silencing_the_linter_is_not_a_fix(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": UNUSED_VAR, "tests/test_demo.py": TEST})
    replies, prompts = [NOQA_FIX, REAL_FIX], []

    def fake_ask(system, prompt, model):
        prompts.append(prompt)
        return llm.Reply(replies[len(prompts) - 1], "fake", "sha")

    monkeypatch.setattr(llm, "ask", fake_ask)
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert "silences tools" in prompts[1]
    assert "total" not in (clone / "demo.py").read_text()


def test_redact_masks_env_values():
    assert run.redact("token=abcdef123 failed", ["abcdef123"]) == "token=*** failed"


def test_safe_lint_in_generated_tests_is_fixed_without_an_attempt(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    dirty = CHAR_TEST.replace("```python\n", "```python\nimport os\n")
    calls = []
    monkeypatch.setattr(llm, "ask", lambda *a: calls.append(a) or llm.Reply(dirty, "f", "s"))
    state, _ = dry_run(src)
    assert state.merged_today() == 1 and len(calls) == 1


def test_summary_picks_pytest_closing_line():
    out = "____ test_x ____\nassert 1 == 2\n==== 1 failed, 3 passed in 0.1s ====\n"
    assert run._summary(out) == "1 failed, 3 passed in 0.1s"
