from datetime import date

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
