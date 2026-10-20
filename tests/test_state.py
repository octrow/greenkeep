from greenkeep.state import MAX_ATTEMPTS, State


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


def test_cool_down_keeps_a_repo_off_for_seven_days(tmp_path):
    from datetime import date, timedelta
    st = State(tmp_path / "s.db")
    assert not st.cooling("r")
    until = st.cool_down("r")
    assert until == date.today() + timedelta(days=7)
    assert st.cooling("r") and not st.cooling("other")


def test_a_run_is_done_for_the_day_once_recorded(tmp_path):
    st = State(tmp_path / "s.db")
    assert not st.ran_today()
    st.record_run()
    assert State(tmp_path / "s.db").ran_today()
