from greenkeep.state import MAX_ATTEMPTS, CoolDown, State


def test_attempts_park_finding_after_limit(tmp_path):
    st = State(tmp_path / "s.db")
    for _ in range(MAX_ATTEMPTS):
        assert st.is_workable("r", "fp")
        st.add_attempt("r", "fp")
    assert st.status("r", "fp") == "parked"
    assert not st.is_workable("r", "fp")


def test_merged_today_counts_only_merged(tmp_path):
    st = State(tmp_path / "s.db")
    st.set("r", "a", status="merged", pr=1)
    st.set("r", "b", status="open")
    assert st.merged_today() == 1
    assert not st.is_workable("r", "a")


def test_last_merge_orders_never_touched_repos_first(tmp_path):
    st = State(tmp_path / "s.db")
    st.set("a", "x", status="merged")
    assert st.last_merge("b") == "" < st.last_merge("a")


def test_set_rejects_unknown_columns(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="unknown finding columns"):
        State(tmp_path / "s.db").set("r", "fp", **{"status='x'; --": 1})


def test_rows_for_the_dashboard(tmp_path):
    st = State(tmp_path / "s.db")
    st.set("r", "fp", status="merged", pr=3, title="Bump x", url="https://github.com/o/r")
    [row] = st.rows()
    assert row["title"] == "Bump x" and row["pr"] == 3 and row["status"] == "merged"


def test_security_merges_do_not_spend_the_budget(tmp_path):
    st = State(tmp_path / "s.db")
    st.set("r", "vuln:anyio:PYSEC-1", status="merged", pr=1)
    st.set("r", "ruff:SIM115:x.py:1", status="merged", pr=2)
    assert st.merged_today() == 1


def test_cool_down_remembers_until_when_and_both_heads(tmp_path):
    from datetime import date, timedelta
    st = State(tmp_path / "s.db")
    assert st.cool_down_of("r") is None
    st.cool_down("r", "repo-sha", "tool-sha")
    cd = st.cool_down_of("r")
    assert cd == CoolDown(date.today() + timedelta(days=7), "repo-sha", "tool-sha")
    st.lift_cool_down("r")
    assert st.cool_down_of("r") is None


def test_a_run_is_done_for_the_day_once_recorded(tmp_path):
    st = State(tmp_path / "s.db")
    assert not st.ran_today()
    st.record_run()
    assert State(tmp_path / "s.db").ran_today()


def test_a_later_write_to_an_old_merge_does_not_make_it_todays(tmp_path):
    st = State(tmp_path / "s.db")
    st.set("r", "a", status="merged", pr=1)
    st.db.execute("UPDATE finding SET merged_at='2026-10-15 18:24:25', updated=merged_at")
    st.set("r", "a", sha="abc")  # e.g. the revert check backfilling the merge commit
    assert st.merged_today() == 0
    assert st.last_merge("r") == "2026-10-15 18:24:25"


def test_old_database_gets_merge_times_from_updated(tmp_path):
    import sqlite3
    db = tmp_path / "s.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE finding (repo TEXT, fp TEXT, status TEXT DEFAULT 'open', "
                "attempts INT DEFAULT 0, issue INT, pr INT, updated TEXT, "
                "PRIMARY KEY (repo, fp))")
    old.execute("INSERT INTO finding VALUES ('r', 'a', 'merged', 0, 1, 2, '2026-10-15 18:24:25')")
    old.commit()
    old.close()
    State(db).db.close()
    assert State(db).last_merge("r") == "2026-10-15 18:24:25"  # survived reopening
