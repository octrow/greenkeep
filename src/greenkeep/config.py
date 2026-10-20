"""The owner's config: which repos are tended, which come first, and the daily merge budget.

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
    focus: bool = False  # a Focus repo: run before the others every run
    tests: str = ""  # its test root: the folder its suite runs from, relative to the repo

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
    entries = data.get("repos")
    if not isinstance(entries, list) or not all(_entry_ok(r) for r in entries):
        raise ConfigError(f"{path}: `repos` must list paths or owner/name strings, or "
                          "{ path = ..., tests = ... } tables")
    # a table names the repo's test root; a plain string runs its suite from the root
    repos = [r if isinstance(r, str) else r["path"] for r in entries]
    tests = {r["path"]: r.get("tests", "") for r in entries if isinstance(r, dict)}
    focus = data.get("focus", [])
    if not isinstance(focus, list) or not all(isinstance(r, str) for r in focus):
        raise ConfigError(f"{path}: `focus` must be a list of entries from `repos`")
    if stray := sorted(set(focus) - set(repos)):
        raise ConfigError(f"{path}: focus entries not in `repos`: {', '.join(stray)}")
    targets = [Target(r, r in focus, tests.get(r, "")) for r in repos]
    for t in targets:
        _ = t.path  # a typo in the config fails at load, not halfway through a run
    return Config(targets,
                  int(data.get("budget", DEFAULT_BUDGET)),
                  int(data.get("weekend_budget", DEFAULT_WEEKEND_BUDGET)))


def _entry_ok(entry: object) -> bool:
    if isinstance(entry, dict):
        return (isinstance(entry.get("path"), str) and isinstance(entry.get("tests", ""), str)
                and set(entry) <= {"path", "tests"})
    return isinstance(entry, str)


def paused(pause_file: Path = PAUSE_FILE) -> bool:
    return pause_file.exists()


def pause(reason: str, pause_file: Path = PAUSE_FILE) -> None:
    """Auto-pause: the file's text says why; deleting it resumes greenkeep."""
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.write_text(reason + "\n")
