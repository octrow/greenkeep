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
    assert apply_edits(tmp_path, edits) == ["pkg/a.py"]
    assert (tmp_path / "pkg/a.py").read_text() == "import sys\nprint(sys)\n"


def test_apply_rejects_ambiguous_or_missing_search(tmp_path):
    (tmp_path / "a.py").write_text("x\nx\n")
    with pytest.raises(EditError):
        apply_edits(tmp_path, parse_edits("a.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE"))
    with pytest.raises(EditError):
        apply_edits(tmp_path, parse_edits("a.py\n<<<<<<< SEARCH\nz\n=======\ny\n>>>>>>> REPLACE"))


def test_apply_refuses_paths_outside_root(tmp_path):
    with pytest.raises(EditError):
        apply_edits(tmp_path, parse_edits("../x.py\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE"))


def test_parse_edits_without_blocks_raises():
    with pytest.raises(EditError):
        parse_edits("I could not fix it")


def test_python_block():
    assert python_block("text\n```python\ndef test_x():\n    pass\n```\n") == (
        "def test_x():\n    pass\n")
    with pytest.raises(EditError):
        python_block("no code")
