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
APPROVE = '{"approve": true, "reason": "fine"}'
REJECT = '{"approve": false, "reason": "tests assert nothing real"}'
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


class FakeLLM:
    """Patcher replies in order; the reviewer answers with `verdicts` in order, then approves."""

    def __init__(self, patches: list[str], verdicts: list[str] = ()) -> None:
        self.patches, self.verdicts = list(patches), list(verdicts)
        self.patch_prompts: list[str] = []
        self.reviews = 0

    def __call__(self, system: str, prompt: str, model: str) -> llm.Reply:
        if system == llm.REVIEW_SYSTEM:
            self.reviews += 1
            verdict = self.verdicts.pop(0) if self.verdicts else APPROVE
            return llm.Reply(verdict, "reviewer", "sha")
        self.patch_prompts.append(prompt)
        text = self.patches[min(len(self.patch_prompts), len(self.patches)) - 1]
        return llm.Reply(text, "patcher", "sha")


def fake_llm(monkeypatch, patches: list[str], verdicts: list[str] = ()) -> FakeLLM:
    fake = FakeLLM(patches, verdicts)
    monkeypatch.setattr(llm, "ask", fake)
    return fake


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
    fake = fake_llm(monkeypatch, [CHAR_TEST])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and fake.reviews == 1
    assert git(clone, "log", "-1", "--format=%s").startswith("test: Add characterization")
    assert (clone / "tests/test_demo_characterization.py").is_file()


def test_bad_llm_reply_parks_finding_after_three_attempts(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake = fake_llm(monkeypatch, ["no"])
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert len(fake.patch_prompts) == run.MAX_ATTEMPTS and fake.reviews == 0
    assert state.status("demo", "untested:demo.py") == "parked"


def test_patch_that_adds_lint_findings_is_rejected_then_retried(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    # F841 has no safe automatic fix, so only a new attempt can get rid of it
    dirty = CHAR_TEST.replace("def test_a():\n", "def test_a():\n    unused = a(1)\n")
    prompts = fake_llm(monkeypatch, [dirty, CHAR_TEST]).patch_prompts
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
    prompts = fake_llm(monkeypatch, [NOQA_FIX, REAL_FIX]).patch_prompts
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert "silences tools" in prompts[1]
    assert "total" not in (clone / "demo.py").read_text()


def test_redact_masks_env_values():
    assert run.redact("token=abcdef123 failed", ["abcdef123"]) == "token=*** failed"


def test_safe_lint_in_generated_tests_is_fixed_without_an_attempt(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    dirty = CHAR_TEST.replace("```python\n", "```python\nimport os\n")
    fake = fake_llm(monkeypatch, [dirty])
    state, _ = dry_run(src)
    assert state.merged_today() == 1 and len(fake.patch_prompts) == 1


def test_summary_picks_pytest_closing_line():
    out = "____ test_x ____\nassert 1 == 2\n==== 1 failed, 3 passed in 0.1s ====\n"
    assert run._summary(out) == "1 failed, 3 passed in 0.1s"


def test_summary_ignores_failed_inside_assertion_text():
    out = "E   assert {'failed': 1} == {}\nsome noise failed here\n1 failed, 2 passed in 3.40s\n"
    assert run._summary(out) == "1 failed, 2 passed in 3.40s"


def test_reviewer_rejection_is_fed_back_and_recorded(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake = fake_llm(monkeypatch, [CHAR_TEST], verdicts=[REJECT])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and fake.reviews == 2
    assert "reviewer (reviewer) rejected" in fake.patch_prompts[1]
    assert "reviewed by: `reviewer`" in git(clone, "log", "-1", "--format=%B")


def test_unreadable_review_is_a_rejection(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake_llm(monkeypatch, [CHAR_TEST], verdicts=["looks good to me"] * run.MAX_ATTEMPTS)
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert state.status("demo", "untested:demo.py") == "parked"


def test_deterministic_ruff_fix_skips_review(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE, "tests/test_demo.py": TEST})
    fake = fake_llm(monkeypatch, ["unused"])
    state, _ = dry_run(src)
    assert state.merged_today() == 1 and fake.reviews == 0 and not fake.patch_prompts


def test_clip_keeps_reason_line_and_tail():
    text = "test suite failed: 1 failed in 2s\n" + "x" * 5000 + "\nE   assert 1 == 2"
    clipped = run.clip(text)
    assert clipped.startswith("test suite failed: 1 failed in 2s\n…\n")
    assert clipped.endswith("assert 1 == 2") and len(clipped) < 1600
