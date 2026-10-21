import pytest

from greenkeep.edits import EditError, apply_edits, parse_edits, python_block

REPLY = """Here you go:
pkg/a.py
<<<<<<< SEARCH
import os
import sys
=======
import sys
>>>>>>> REPLACE
"""


def test_parse_and_apply_search_replace(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("import os\nimport sys\nprint(sys)\n")
    edits = parse_edits(REPLY)
    assert [e.path for e in edits] == ["pkg/a.py"]
    assert apply_edits(tmp_path, edits, frozenset({"pkg/a.py"})) == ["pkg/a.py"]
    assert (tmp_path / "pkg/a.py").read_text() == "import sys\nprint(sys)\n"


def block(path: str, search: str) -> str:
    return f"{path}\n<<<<<<< SEARCH\n{search}\n=======\ny\n>>>>>>> REPLACE"


@pytest.mark.parametrize(("path", "search", "why"), [
    ("a.py", "x", "matches 2 times"),
    ("a.py", "z", "matches 0 times"),
    ("a.py", "", "empty SEARCH"),
    ("../x.py", "a", "outside repo"),
    (".git/config", "a", "not a tracked file"),
    ("untracked.py", "a", "not a tracked file"),
])
def test_apply_rejects_unsafe_or_unclear_edits(tmp_path, path, search, why):
    (tmp_path / "a.py").write_text("x\nx\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("a")
    (tmp_path / "untracked.py").write_text("a")
    with pytest.raises(EditError, match=why):
        apply_edits(tmp_path, parse_edits(block(path, search)), frozenset({"a.py"}))


def test_parse_edits_without_blocks_raises():
    with pytest.raises(EditError):
        parse_edits("I could not fix it")


def test_python_block():
    assert python_block("text\n```python\ndef test_x():\n    pass\n```\n") == (
        "def test_x():\n    pass\n")
    with pytest.raises(EditError):
        python_block("no code")


def test_python_block_keeps_fences_inside_the_code():
    reply = '```python\ndef test_md():\n    assert render("```x```") == "x"\n```\n'
    assert python_block(reply) == 'def test_md():\n    assert render("```x```") == "x"\n'


NEW_TEST = "tests/test_b.py\n<<<<<<< SEARCH\n=======\ndef test_b():\n    pass\n>>>>>>> REPLACE\n"


def test_empty_search_creates_a_new_test_file_only_when_allowed(tmp_path):
    with pytest.raises(EditError):
        apply_edits(tmp_path, parse_edits(NEW_TEST), frozenset())
    assert apply_edits(tmp_path, parse_edits(NEW_TEST), frozenset(), new_tests=True) \
        == ["tests/test_b.py"]
    assert (tmp_path / "tests/test_b.py").read_text() == "def test_b():\n    pass\n"
    with pytest.raises(EditError):  # never over an existing file
        apply_edits(tmp_path, parse_edits(NEW_TEST), frozenset(), new_tests=True)
    source = NEW_TEST.replace("tests/test_b.py", "pkg/b.py")
    with pytest.raises(EditError):  # and never a source file
        apply_edits(tmp_path, parse_edits(source), frozenset(), new_tests=True)
