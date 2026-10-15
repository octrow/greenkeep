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
