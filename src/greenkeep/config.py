"""The owner's config: which repos are tended and how much greenkeep may merge a day.

Lives outside any repo (`~/.config/greenkeep/config.toml`): it names private repos.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CONFIG_DIR = Path.home() / ".config/greenkeep"
CONFIG_FILE = CONFIG_DIR / "config.toml"
PAUSE_FILE = CONFIG_DIR / "PAUSE"
DEFAULT_BUDGET = 3
DEFAULT_WEEKEND_BUDGET = 1
SATURDAY = 5
SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")


class ConfigError(ValueError):
    """The config file is missing or malformed."""


@dataclass(frozen=True)
class Target:
    """A tended repo: a local checkout, or `owner/name` cloned straight from GitHub."""
    spec: str

    @property
    def path(self) -> Path | None:
        p = Path(self.spec).expanduser()
        if p.is_dir():
            return p.resolve()
        if SLUG.match(self.spec):
            return None
        raise ConfigError(f"neither a local checkout nor owner/name: {self.spec}")

    @property
    def name(self) -> str:
        return self.path.name if self.path else self.spec.split("/")[1]

    @property
    def origin(self) -> str | None:
        """The clone URL when there is no local checkout to read it from."""
        return None if self.path else f"https://github.com/{self.spec}.git"


@dataclass(frozen=True)
class Config:
    repos: list[Target]
    budget: int = DEFAULT_BUDGET
    weekend_budget: int = DEFAULT_WEEKEND_BUDGET

    def budget_on(self, day: date) -> int:
        return self.weekend_budget if day.weekday() >= SATURDAY else self.budget


def load(path: Path = CONFIG_FILE) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    repos = data.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
        raise ConfigError(f"{path}: `repos` must be a list of paths or owner/name strings")
    targets = [Target(r) for r in repos]
    for t in targets:
        _ = t.path  # a typo in the config fails at load, not halfway through a run
    return Config(targets,
                  int(data.get("budget", DEFAULT_BUDGET)),
                  int(data.get("weekend_budget", DEFAULT_WEEKEND_BUDGET)))


def paused(pause_file: Path = PAUSE_FILE) -> bool:
    return pause_file.exists()


def pause(reason: str, pause_file: Path = PAUSE_FILE) -> None:
    """Auto-pause: the file's text says why; deleting it resumes greenkeep."""
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.write_text(reason + "\n")
