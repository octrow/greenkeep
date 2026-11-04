import sqlite3
import subprocess
from pathlib import Path

from greenkeep import backup
from greenkeep.state import State


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_state_is_pushed_as_sql_and_restores(tmp_path):
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "copy")
    copy = tmp_path / "copy"
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        git(copy, "config", key, value)
    state = State(tmp_path / "state.db")
    state.set("demo", "fp", status="merged", pr=7, sha="abc")

    backup.save(state, copy)
    backup.save(state, copy)  # nothing changed: no second commit, no error

    git(tmp_path, "clone", "-q", "origin.git", "check")
    assert git(tmp_path / "check", "rev-list", "--count", "HEAD") == "1"
    restored = sqlite3.connect(tmp_path / "restored.db")
    restored.executescript((tmp_path / "check" / backup.DUMP).read_text())
    assert restored.execute("SELECT repo, fp, status, pr FROM finding").fetchall() == [
        ("demo", "fp", "merged", 7)
    ]
