"""Whole dry runs on throwaway repos: real git, uv, ruff and pytest; only the LLM is faked."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from greenkeep import github, llm, run, workspace
from greenkeep.config import Target
from greenkeep.core import DEFAULT_REVIEW_MODEL
from greenkeep.digest import Digest

PYPROJECT = '[project]\nname = "demo"\nversion = "0"\ndescription = "Demo"\nrequires-python = ">=3.12"\n'
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

    def review(self, prompt: str, **kw) -> SimpleNamespace:
        self.reviews += 1
        return SimpleNamespace(text=self.verdicts.pop(0) if self.verdicts else APPROVE)

    def __call__(self, system: str, prompt: str, model: str, root: Path) -> llm.Reply:
        self.patch_prompts.append(prompt)
        text = self.patches[min(len(self.patch_prompts), len(self.patches)) - 1]
        return llm.Reply(text, "patcher", "sha")


def fake_llm(monkeypatch, patches: list[str], verdicts: list[str] = ()) -> FakeLLM:
    fake = FakeLLM(patches, verdicts)
    monkeypatch.setattr(llm, "ask", fake)
    monkeypatch.setattr(llm, "run_claude", fake.review)
    return fake


def dry_run(src: Path) -> tuple[run.State, Path]:
    state = run.State(Path(":memory:"))
    opts = run.Options(push=False, budget=3, model="x", keep=True)
    run.run_repo(Target(str(src)), opts, state, Digest())
    return state, next(workspace.CACHE.glob("run-demo-*/demo"))


def test_covered_lint_finding_is_fixed_by_ruff_and_checkout_untouched(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE, "tests/test_demo.py": TEST})
    fake = fake_llm(monkeypatch, ["unused"])
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert not fake.patch_prompts and fake.reviews == 0  # deterministic: no patcher, no review
    assert "import os" not in (clone / "demo.py").read_text()
    msg = git(clone, "log", "-1", "--format=%B")
    assert msg.startswith("fix: F401") and "(demo.py:1)" in msg and run.TRAILER in msg
    assert "import os" in (src / "demo.py").read_text()


def test_repo_without_tests_gets_characterization_tests(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake = fake_llm(monkeypatch, [CHAR_TEST])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and fake.reviews == 1
    assert git(clone, "log", "-1", "--format=%s").startswith("test: Add tests for")
    assert (clone / "tests/test_demo.py").is_file()


def test_bad_llm_reply_parks_finding_after_three_attempts(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake = fake_llm(monkeypatch, ["no"])
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert len(fake.patch_prompts) == run.MAX_ATTEMPTS and fake.reviews == 0
    assert state.status("demo", "untested:demo.py") == "parked"


def test_patch_that_adds_lint_findings_is_rejected_then_retried(tmp_path, monkeypatch):
    pyproject = PYPROJECT + '[tool.ruff.lint]\nextend-select = ["E721"]\n'
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG, "pyproject.toml": pyproject})
    # E721's fix is unsafe and not one greenkeep applies, so only a new attempt can fix it
    dirty = CHAR_TEST.replace("def test_a():\n", "def test_a():\n    assert type(a(1)) == int\n")
    prompts = fake_llm(monkeypatch, [dirty, CHAR_TEST]).patch_prompts
    state, clone = dry_run(src)
    assert state.merged_today() == 1
    assert "adds lint findings" in prompts[1] and "E721" in prompts[1]
    assert "type(a(1))" not in (clone / "tests/test_demo.py").read_text()


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


def test_fixable_lint_in_generated_tests_is_fixed_without_an_attempt(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    # F401 has a safe fix, F841 only an unsafe one: both are fine on a brand-new test file
    dirty = CHAR_TEST.replace("```python\n", "```python\nimport os\n").replace(
        "def test_a():\n", "def test_a():\n    unused = a(1)\n")
    fake = fake_llm(monkeypatch, [dirty])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and len(fake.patch_prompts) == 1
    assert "unused" not in (clone / "tests/test_demo.py").read_text()


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
    assert f"reviewer ({DEFAULT_REVIEW_MODEL}) rejected" in fake.patch_prompts[1]
    assert f"reviewed by: `{DEFAULT_REVIEW_MODEL}`" in git(clone, "log", "-1", "--format=%B")


def test_unreadable_review_is_a_rejection(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    fake_llm(monkeypatch, [CHAR_TEST], verdicts=["looks good to me"] * run.MAX_ATTEMPTS)
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert state.status("demo", "untested:demo.py") == "parked"



def test_clip_keeps_reason_line_and_tail():
    text = "test suite failed: 1 failed in 2s\n" + "x" * 5000 + "\nE   assert 1 == 2"
    clipped = run.clip(text)
    assert clipped.startswith("test suite failed: 1 failed in 2s\n…\n")
    assert clipped.endswith("assert 1 == 2") and len(clipped) < 1600


def test_web_url_from_remotes():
    assert github.web_url("https://github.com/o/r.git") == "https://github.com/o/r"
    assert github.web_url("git@github.com:o/r.git") == "https://github.com/o/r"
    assert github.web_url("/tmp/origin.git") == ""


def test_timed_out_attempt_counts_and_the_finding_waits_for_the_next_run(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    calls = []

    def hang(*_):
        calls.append(1)
        raise run.ClaudeCliError("claude timed out after 600s")
    monkeypatch.setattr(llm, "ask", hang)
    state, _ = dry_run(src)
    assert len(calls) == 1
    assert state.attempts("demo", "untested:demo.py") == 1
    assert state.status("demo", "untested:demo.py") == "open"


def test_parked_findings_in_a_row_put_the_repo_in_cool_down(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG, "other.py": BIG})
    fake_llm(monkeypatch, ["no"])
    state, digest = run.State(Path(":memory:")), Digest()
    run.run_repo(Target(str(src)), run.Options(False, 3, "x", True), state, digest)
    assert state.cool_down_of("demo").repo_head == git(src, "rev-parse", "HEAD").strip()
    assert "cool-down until" in digest.problems[0]


def test_repo_in_cool_down_is_not_even_cloned(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    state, digest = run.State(Path(":memory:")), Digest()
    state.cool_down("demo", git(src, "rev-parse", "HEAD").strip(), workspace.tool_head())
    run.run_repo(Target(str(src)), run.Options(False, 3, "x", True), state, digest)
    assert digest.cooling == 1 and not workspace.CACHE.exists()


def test_a_new_commit_in_the_repo_lifts_its_cool_down(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    state, digest = run.State(Path(":memory:")), Digest()
    state.cool_down("demo", "an-older-head", workspace.tool_head())
    fake_llm(monkeypatch, [CHAR_TEST])
    run.run_repo(Target(str(src)), run.Options(False, 3, "x", True), state, digest)
    assert state.cool_down_of("demo") is None and digest.cooling == 0


def test_an_updated_greenkeep_lifts_every_cool_down(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": BIG})
    state, digest = run.State(Path(":memory:")), Digest()
    state.cool_down("demo", git(src, "rev-parse", "HEAD").strip(), "an-older-greenkeep")
    fake_llm(monkeypatch, [CHAR_TEST])
    run.run_repo(Target(str(src)), run.Options(False, 3, "x", True), state, digest)
    assert state.cool_down_of("demo") is None


def test_test_root_runs_the_suite_from_its_folder(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"app/demo.py": CODE, "app/test_demo.py": TEST})
    ws = workspace.clone(Target(str(src), tests="app"))
    workspace.sync(ws)
    tests = workspace.run_tests(ws)
    assert tests.ok and "app/demo.py" in tests.cov["files"]  # paths stay repo-relative


def test_a_test_root_with_its_own_project_gets_its_extras(tmp_path, monkeypatch):
    # chat-watch's bot/: a separate uv project whose pytest plugins live in its own `dev` extra
    bot = PYPROJECT.replace('"demo"', '"bot"') + \
        '\n[project.optional-dependencies]\ndev = ["pytest-timeout"]\n'
    plugin_test = "import pytest_timeout\n\n\ndef test_plugin():\n    assert pytest_timeout\n"
    src = make_repo(tmp_path, monkeypatch, {"bot/pyproject.toml": bot,
                                            "bot/tests/test_plugin.py": plugin_test})
    ws = workspace.clone(Target(str(src), tests="bot"))
    workspace.sync(ws)
    assert workspace.run_tests(ws).ok


RUFF_UP_I = '\n[tool.ruff.lint]\nselect = ["UP", "I"]\n'
OLD_IMPORT = "from os import sep\nfrom typing import Iterator\n\n\n" \
             "def parts(p: str) -> Iterator[str]:\n    yield from p.split(sep)\n"
PARTS_TEST = "from demo import parts\n\n\ndef test_parts():\n    assert list(parts('a')) == ['a']\n"


def test_ruff_fix_also_fixes_the_lint_it_introduces(tmp_path, monkeypatch):
    # UP035 rewrites the import in place, which leaves the import block unsorted (I001)
    src = make_repo(tmp_path, monkeypatch, {"pyproject.toml": PYPROJECT + RUFF_UP_I,
                                            "demo.py": OLD_IMPORT,
                                            "tests/test_demo.py": PARTS_TEST})
    fake = fake_llm(monkeypatch, ["unused"])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and not fake.patch_prompts
    assert (clone / "demo.py").read_text().startswith(
        "from collections.abc import Iterator\nfrom os import sep\n")


CLAMP = ("def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n"
         "        return lo\n    return x\n")
CLAMP_TEST = ("from demo import clamp\n\n\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n"
              "    assert clamp(-1, 0, 10) == 0\n    assert clamp(11, 0, 10) is not None\n")
CLAIMS = ('{"bugs": [{"line": 5, "title": "clamp returns lo above hi", '
          '"why": "clamp(11, 0, 10) gives 0, should be 10"}]}')
FIX = "demo.py\n<<<<<<< SEARCH\n    if x > hi:\n        return lo\n=======\n" \
      "    if x > hi:\n        return hi\n>>>>>>> REPLACE\n"


def bug_test(body: str) -> str:
    return ("tests/test_clamp_above.py\n<<<<<<< SEARCH\n=======\nfrom demo import clamp\n\n\n"
            f"def test_clamp_above_hi():\n    {body}\n>>>>>>> REPLACE\n")


def hunt_run(tmp_path, monkeypatch, patch: str) -> tuple[run.State, FakeLLM]:
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CLAMP, "tests/test_demo.py": CLAMP_TEST})
    fake = fake_llm(monkeypatch, [patch])
    replies = {llm.HUNT_SYSTEM: CLAIMS, llm.SKEPTIC_SYSTEM: '{"keep": [0]}'}

    def claude(prompt: str, **kw) -> SimpleNamespace:
        for system, text in replies.items():
            if prompt.startswith(system):
                return SimpleNamespace(text=text)
        return fake.review(prompt, **kw)

    monkeypatch.setattr(llm, "run_claude", claude)
    state = run.State(Path(":memory:"))
    opts = run.Options(push=False, budget=3, model="x", keep=True)
    run.run_repo(Target(str(src), hunt=True), opts, state, Digest())
    return state, fake


def test_hunted_bug_is_fixed_test_first(tmp_path, monkeypatch):
    state, fake = hunt_run(tmp_path, monkeypatch,
                           FIX + bug_test("assert clamp(11, 0, 10) == 10"))
    assert state.merged_today() == 1 and fake.reviews == 1
    clone = next(workspace.CACHE.glob("run-demo-*/demo"))
    assert "return hi" in (clone / "demo.py").read_text()
    assert (clone / "tests/test_clamp_above.py").exists()


def test_a_test_that_passes_without_the_fix_does_not_prove_the_bug(tmp_path, monkeypatch):
    state, fake = hunt_run(tmp_path, monkeypatch,
                           FIX + bug_test("assert clamp(5, 0, 10) == 5"))
    assert state.merged_today() == 0 and fake.reviews == 0
    assert "does not show the bug" in fake.patch_prompts[-1]


def test_nothing_hunted_when_the_skeptic_refutes_every_claim(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CLAMP, "tests/test_demo.py": CLAMP_TEST})
    fake = fake_llm(monkeypatch, ["unused"])
    calls = []

    def claude(prompt: str, **kw) -> SimpleNamespace:
        calls.append(prompt)
        return SimpleNamespace(text=CLAIMS if prompt.startswith(llm.HUNT_SYSTEM)
                               else '{"keep": []}')

    monkeypatch.setattr(llm, "run_claude", claude)
    state = run.State(Path(":memory:"))
    opts = run.Options(push=False, budget=3, model="x", keep=False)
    for _ in range(3):  # two empty hunts, then the unchanged module is left alone
        run.run_repo(Target(str(src), hunt=True), opts, state, Digest())
    assert len(calls) == 4 and not fake.patch_prompts


def test_a_bug_fix_may_not_edit_an_existing_test(tmp_path, monkeypatch):
    edit = ("tests/test_demo.py\n<<<<<<< SEARCH\n    assert clamp(11, 0, 10) is not None\n"
            "=======\n    assert clamp(11, 0, 10) == 10\n>>>>>>> REPLACE\n")
    state, fake = hunt_run(tmp_path, monkeypatch, FIX + edit)
    assert state.merged_today() == 0
    assert "never changed or removed ones" in fake.patch_prompts[-1]


def test_an_unreadable_hunter_reply_leaves_the_module_for_the_next_run(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CLAMP, "tests/test_demo.py": CLAMP_TEST})
    fake_llm(monkeypatch, ["unused"])
    calls = []

    def claude(prompt: str, **kw) -> SimpleNamespace:
        calls.append(prompt)
        return SimpleNamespace(text="I could not decide.")

    monkeypatch.setattr(llm, "run_claude", claude)
    state = run.State(Path(":memory:"))
    opts = run.Options(push=False, budget=3, model="x", keep=False)
    for _ in range(2):
        run.run_repo(Target(str(src), hunt=True), opts, state, Digest())
    assert len(calls) == 2


def test_the_repos_addopts_still_apply_without_its_coverage_options(tmp_path, monkeypatch):
    pyproject = PYPROJECT + ('[tool.pytest.ini_options]\naddopts = "--cov=demo -m \'not slow\'"\n'
                             'markers = ["slow"]\n')
    slow = "import pytest\n\n\n@pytest.mark.slow\ndef test_needs_a_server():\n    assert False\n"
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE, "tests/test_demo.py": TEST,
                                            "tests/test_slow.py": slow,
                                            "pyproject.toml": pyproject})
    fake_llm(monkeypatch, ["unused"])
    state, _ = dry_run(src)
    assert state.merged_today() == 1  # baseline green: the slow test stayed deselected


NO_DESCRIPTION = PYPROJECT.replace('description = "Demo"\n', "")
DESCRIBE = ('pyproject.toml\n<<<<<<< SEARCH\nversion = "0"\n=======\nversion = "0"\n'
            'description = "Adds numbers."\n>>>>>>> REPLACE\n')


def test_missing_description_is_written_reviewed_and_merged(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE.replace("import os\n\n\n", ""),
                                            "tests/test_demo.py": TEST,
                                            "pyproject.toml": NO_DESCRIPTION})
    fake = fake_llm(monkeypatch, [DESCRIBE])
    state, clone = dry_run(src)
    assert state.merged_today() == 1 and fake.reviews == 1
    assert 'description = "Adds numbers."' in (clone / "pyproject.toml").read_text()


def test_a_readiness_fix_may_change_only_its_own_field(tmp_path, monkeypatch):
    src = make_repo(tmp_path, monkeypatch, {"demo.py": CODE.replace("import os\n\n\n", ""),
                                            "tests/test_demo.py": TEST,
                                            "pyproject.toml": NO_DESCRIPTION})
    # inside [project], but not the field the finding is about
    sneaky = DESCRIBE + ('pyproject.toml\n<<<<<<< SEARCH\nrequires-python = ">=3.12"\n=======\n'
                         'requires-python = ">=3.11"\n>>>>>>> REPLACE\n')
    fake = fake_llm(monkeypatch, [sneaky])
    state, _ = dry_run(src)
    assert state.merged_today() == 0
    assert "only [project] description" in fake.patch_prompts[-1]


def test_owner_hears_about_a_missing_license_once(tmp_path):
    ws = SimpleNamespace(name="demo", root=tmp_path)
    state = run.State(Path(":memory:"))
    digests = [Digest(), Digest()]
    for digest in digests:
        run._tell_owner(ws, state, digest)
    assert digests[0].owner == ["demo: has no LICENSE: pick one", "demo: has no README.md"]
    assert digests[1].owner == []


def test_a_bug_test_may_be_appended_where_the_repo_keeps_its_tests(tmp_path, monkeypatch):
    append = ("tests/test_demo.py\n<<<<<<< SEARCH\n    assert clamp(11, 0, 10) is not None\n"
              "=======\n    assert clamp(11, 0, 10) is not None\n\n\n"
              "def test_clamp_above_hi():\n    assert clamp(11, 0, 10) == 10\n>>>>>>> REPLACE\n")
    state, _ = hunt_run(tmp_path, monkeypatch, FIX + append)
    assert state.merged_today() == 1
