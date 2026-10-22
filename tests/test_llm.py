from types import SimpleNamespace

from greenkeep import llm
from greenkeep.findings import Finding


def test_patcher_reads_the_clone_with_read_only_tools(monkeypatch, tmp_path):
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw)
        return SimpleNamespace(text="```python\n```")
    monkeypatch.setattr(llm, "run_claude", fake)
    llm.ask("sys", "prompt", "m", tmp_path)
    assert seen["tools"] == llm.READ_TOOLS and seen["cwd"] == tmp_path
    assert seen["setting_sources"] == ""  # none of the owner's hooks, plugins or CLAUDE.md


def test_the_import_name_is_relative_to_the_test_root():
    # jobs runs its suite from pipeline/, and its tests import `gmail_api_client`
    assert llm.module_name("pipeline/gmail_api_client.py", "pipeline") == "gmail_api_client"
    assert llm.module_name("src/pkg/db/repo.py") == "pkg.db.repo"


def test_a_retry_of_a_test_file_corrects_the_previous_one_instead_of_starting_over():
    f = Finding("untested", "coverage", "pkg/a.py", 1, "10% covered", "untested:pkg/a.py")
    first = llm.characterization_prompt(f, "x = 1", {}, 120, "")
    retry = llm.characterization_prompt(f, "x = 1", {}, 120, "1 failed", previous="def test_x(): ...")
    assert "def test_x(): ..." not in first
    assert "def test_x(): ..." in retry and "1 failed" in retry
