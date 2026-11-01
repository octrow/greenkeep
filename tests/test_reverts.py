import subprocess

import pytest

from greenkeep import reverts
from greenkeep.state import State
from greenkeep.workspace import Workspace


def test_reverted_matches_full_and_abbreviated_shas():
    log = 'Revert "fix: x"\n\nThis reverts commit abc1234def.\n'
    assert reverts.reverted(log, {"abc1234def5678", "fff0000"}) == {"abc1234def5678"}
    assert reverts.reverted("nothing here", {"abc1234"}) == set()


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_check_pauses_on_a_reverted_merge_and_marks_it(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    (root / "a.py").write_text("x = 2\n")
    git(root, "commit", "-qam", "fix: greenkeep change")
    sha = git(root, "rev-parse", "HEAD")
    ws = Workspace("demo", root, tmp_path, base_branch="main")
    st = State(tmp_path / "s.db")
    st.set("demo", "fp", status="merged", pr=7, sha=sha)
    reverts.check(ws, st, backfill=False)  # not reverted yet: no pause

    git(root, "revert", "--no-edit", sha)
    with pytest.raises(reverts.AutoPause, match="#7 was reverted"):
        reverts.check(ws, st, backfill=False)
    assert st.status("demo", "fp") == "reverted"
    reverts.check(ws, st, backfill=False)  # reported once: resuming does not re-pause


def test_state_migrates_an_old_database(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute(
        "CREATE TABLE finding (repo TEXT, fp TEXT, status TEXT DEFAULT 'open', "
        "attempts INT DEFAULT 0, issue INT, pr INT, "
        "updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (repo, fp))"
    )
    db.execute("INSERT INTO finding (repo, fp, status, pr) VALUES ('r', 'f', 'merged', 10)")
    db.commit()
    db.close()
    assert State(tmp_path / "old.db").merges("r") == [("f", 10, None)]
