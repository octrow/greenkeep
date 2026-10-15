from datetime import date

import pytest

from greenkeep.config import ConfigError, Target, load, paused
from greenkeep.digest import Digest


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


def test_target_that_is_neither_path_nor_slug_is_rejected():
    with pytest.raises(ConfigError):
        _ = Target("/no/such/dir/anywhere").path


def test_pause_file(tmp_path):
    assert not paused(tmp_path / "PAUSE")
    (tmp_path / "PAUSE").touch()
    assert paused(tmp_path / "PAUSE")


def test_digest_text_lists_only_nonempty_sections():
    d = Digest(merged=["repo-scout: tests for x (PR #10)"], problems=["jobs: red baseline"])
    text = d.text(budget_left=2)
    assert text.startswith("greenkeep: 1 merged, 0 parked, 1 problems; budget left today: 2")
    assert "Merged:\n• repo-scout" in text and "Needs you" not in text


def test_config_with_unknown_repo_fails_at_load(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('repos = ["/no/such/checkout"]\n')
    with pytest.raises(ConfigError, match="neither a local checkout"):
        load(cfg)
