from datetime import date, datetime, timedelta, timezone

import pytest

from greenkeep.config import ConfigError, load, paused


def test_load_config_with_paths_and_slugs(tmp_path):
    (tmp_path / "repo-a").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'repos = ["{tmp_path}/repo-a", "octrow/LaptopLoot"]\nbudget = 2\n')
    c = load(cfg)
    assert [t.name for t in c.repos] == ["repo-a", "LaptopLoot"]
    assert c.repos[0].origin is None
    assert c.repos[1].origin == "https://github.com/octrow/LaptopLoot.git"
    assert c.budget_on(date(2026, 10, 16)) == 2  # Friday
    assert c.budget_on(date(2026, 10, 17)) == 1  # Saturday


@pytest.mark.parametrize("text", ["", "repos = 'x'", "repos = [1]", "not toml ["])
def test_bad_config_raises(tmp_path, text):
    cfg = tmp_path / "config.toml"
    cfg.write_text(text)
    with pytest.raises(ConfigError):
        load(cfg)


def test_pause_file(tmp_path):
    assert not paused(tmp_path / "PAUSE")
    (tmp_path / "PAUSE").touch()
    assert paused(tmp_path / "PAUSE")


def test_config_with_unknown_repo_fails_at_load(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('repos = ["/no/such/checkout"]\n')
    with pytest.raises(ConfigError, match="neither a local checkout"):
        load(cfg)


def test_focus_repos_are_flagged(tmp_path):
    (tmp_path / "a").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'repos = ["{tmp_path}/a", "octrow/b"]\nfocus = ["octrow/b"]\n')
    assert [(t.name, t.focus) for t in load(cfg).repos] == [("a", False), ("b", True)]


def test_focus_outside_repos_fails_at_load(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('repos = ["octrow/a"]\nfocus = ["octrow/typo"]\n')
    with pytest.raises(ConfigError, match="not in `repos`"):
        load(cfg)


def test_repo_entry_can_name_its_test_root(tmp_path):
    (tmp_path / "jobs").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'repos = [{{ path = "{tmp_path}/jobs", tests = "pipeline" }}]\n'
                   f'focus = ["{tmp_path}/jobs"]\n')
    [t] = load(cfg).repos
    assert t.name == "jobs" and t.tests == "pipeline" and t.focus


def test_bug_hunt_is_off_unless_a_repo_entry_turns_it_on(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'repos = [{{ path = "{tmp_path}/a", hunt = true }}, "{tmp_path}/b"]\n')
    assert [t.hunt for t in load(cfg).repos] == [True, False]
    cfg.write_text(f'repos = [{{ path = "{tmp_path}/a", hunt = "yes" }}]\n')
    with pytest.raises(ConfigError):
        load(cfg)


def test_boost_raises_the_budget_until_its_end(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('repos = ["octrow/a"]\nbudget = 3\n'
                   "boost_until = 2026-10-30T03:00:00+05:00\nboost_budget = 15\n")
    c = load(cfg)
    tz = timezone(timedelta(hours=5))
    assert c.boosting(datetime(2026, 10, 30, 2, 59, tzinfo=tz))
    assert c.budget_at(datetime(2026, 10, 29, 21, 0, tzinfo=tz)) == 15
    assert not c.boosting(datetime(2026, 10, 30, 3, 0, tzinfo=tz))
    assert c.budget_at(datetime(2026, 10, 30, 3, 0, tzinfo=tz)) == 3


def test_boost_until_without_an_offset_is_rejected(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('repos = ["octrow/a"]\nboost_until = 2026-10-30T03:00:00\n')
    with pytest.raises(ConfigError, match="boost_until"):
        load(cfg)
