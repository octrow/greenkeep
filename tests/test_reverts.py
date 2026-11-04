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


def repo_with_merge(tmp_path):
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
    return ws, st, sha


@pytest.fixture
def ci(monkeypatch):
    """CI verdicts by sha (red when listed); no revert is published for real."""
    red, published = set(), []
    monkeypatch.setattr(reverts.github, "ci_red", lambda ws, sha: sha in red)
    monkeypatch.setattr(reverts.github, "ci_not_started", lambda ws, sha: False)

    def publish(ws, branch, title, body):
        published.append((branch, title, ws.git("log", "-1", "--format=%B", branch)))
        return 8

    monkeypatch.setattr(reverts.github, "publish", publish)
    return red, published


def test_a_merge_reverted_by_someone_is_marked_and_reported_once(tmp_path, ci):
    ws, st, sha = repo_with_merge(tmp_path)
    assert reverts.check(ws, st, push=True) == []  # not reverted yet

    git(ws.root, "revert", "--no-edit", sha)
    assert reverts.check(ws, st, push=True) == ["demo: greenkeep merge #7 was reverted"]
    assert st.status("demo", "fp") == "reverted"
    assert reverts.check(ws, st, push=True) == []


def test_a_merge_that_turned_main_red_is_reverted_by_greenkeep(tmp_path, ci):
    red, published = ci
    ws, st, sha = repo_with_merge(tmp_path)
    red.add(sha)
    assert reverts.check(ws, st, push=True) == [
        "demo: CI on main went red after greenkeep merge #7; reverted in #8"
    ]
    branch, title, body = published[0]
    assert branch.startswith("greenkeep/revert-")
    assert "#7" in title
    assert f"This reverts commit {sha}" in body
    assert st.status("demo", "fp") == "reverted"


def test_main_already_red_before_the_merge_is_not_greenkeeps_to_revert(tmp_path, ci):
    red, published = ci
    ws, st, sha = repo_with_merge(tmp_path)
    red.update({sha, git(ws.root, "rev-parse", f"{sha}^")})
    assert reverts.check(ws, st, push=True) == []
    assert not published
    assert st.status("demo", "fp") == "merged"


def test_ci_is_watched_only_for_recent_merges(tmp_path, ci):
    red, published = ci
    ws, st, sha = repo_with_merge(tmp_path)
    red.add(sha)
    st.db.execute("UPDATE finding SET merged_at=datetime('now', '-4 days')")
    assert reverts.check(ws, st, push=True) == []
    assert not published


def test_a_dry_run_only_says_it_would_revert(tmp_path, ci):
    red, published = ci
    ws, st, sha = repo_with_merge(tmp_path)
    red.add(sha)
    assert reverts.check(ws, st, push=False) == [
        "demo: CI on main went red after greenkeep merge #7; would revert"
    ]
    assert not published
    assert st.status("demo", "fp") == "merged"


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
