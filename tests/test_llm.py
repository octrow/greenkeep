from types import SimpleNamespace

from greenkeep import llm


def test_patcher_reads_the_clone_with_read_only_tools(monkeypatch, tmp_path):
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw)
        return SimpleNamespace(text="```python\n```")
    monkeypatch.setattr(llm, "run_claude", fake)
    llm.ask("sys", "prompt", "m", tmp_path)
    assert seen["tools"] == llm.READ_TOOLS and seen["cwd"] == tmp_path
    assert seen["setting_sources"] == ""  # none of the owner's hooks, plugins or CLAUDE.md
