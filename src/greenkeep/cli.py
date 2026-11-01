"""greenkeep: one run over the tended repos. With no repos named, the config's list is used.

Without --push it is a dry run: fixes are committed in throwaway clones only and no state
is kept. The scheduled run is `greenkeep --push`.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DEFAULT_BUDGET, PAUSE_FILE, Config, ConfigError, Target, load, pause, paused
from .core import DEFAULT_MODEL, DEFAULT_REVIEW_MODEL, Options
from .digest import Digest, send
from .llm import unavailable
from .reverts import AutoPause
from .run import run_repo
from .state import State
from .workspace import sweep

log = logging.getLogger("greenkeep")

LOG_FILE = Path.home() / ".local/state/greenkeep/greenkeep.log"
LOG_BYTES = 5_000_000
LOG_BACKUPS = 3
PROBLEM_CHARS = 200


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="greenkeep", description=__doc__)
    p.add_argument("repos", nargs="*", help="local checkouts or owner/name (default: config)")
    p.add_argument(
        "--push",
        action="store_true",
        help="open issues/PRs and merge them (default: local dry run)",
    )
    p.add_argument("--budget", type=int, help="merged fix PRs today (default: config)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Claude Code model that writes patches")
    p.add_argument(
        "--review-model",
        default=DEFAULT_REVIEW_MODEL,
        help="Claude Code model that reviews each patch inside the clone",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="keep the throwaway clones (the next run a day later removes them)",
    )
    return p.parse_args(argv)


def _setup_logging() -> None:
    """Console gets the story, the file gets every failure in full (issues point to it)."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    file = RotatingFileHandler(LOG_FILE, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS)
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.DEBUG, format="%(message)s", handlers=[console, file])


def _targets(a: argparse.Namespace) -> tuple[list[Target], int]:
    cfg = _config() if a.repos else load()
    now = datetime.now().astimezone()
    if a.repos:
        # a repo in the config keeps its entry (test root, hunt), so a dry run matches
        known = {t.path or t.spec: t for t in (cfg.repos if cfg else [])}
        repos = [known.get(t.path or t.spec, t) for t in map(Target, a.repos)]
    else:
        repos = cfg.repos
    # a boost spends the subscription: every repo lints with extra rules (complexity,
    # simplify…) and bug-hunts once nothing else is left
    if cfg and cfg.boosting(now):
        repos = [replace(t, hunt=True, boost=True) for t in repos]
    if a.budget is not None:  # 0 is a budget too: nothing merged today
        return repos, a.budget
    return repos, cfg.budget_at(now) if cfg else DEFAULT_BUDGET


def _boosting() -> bool:
    try:
        return load().boosting(datetime.now().astimezone())
    except ConfigError:
        return False


def _config() -> Config | None:
    try:
        return load()
    except ConfigError:  # no config: a dry run on named repos needs none
        return None


def _run_all(targets: list[Target], opts: Options, state: State) -> Digest:
    digest = Digest()
    # Focus repos first, then least recently merged: activity spreads over the repos
    # instead of piling up. ponytail: Focus can take the whole budget and starve the rest
    # (security still bypasses it); reserve a slot for the others if that starts to show.
    # A spent budget still visits every repo: security findings do not wait for it.
    for t in sorted(targets, key=lambda t: (not t.focus, state.last_merge(t.name))):
        try:
            run_repo(t, opts, state, digest)
        except AutoPause as stop:
            log.warning("auto-pause: %s", stop)
            if opts.push:
                pause(f"auto-paused {datetime.now():%Y-%m-%d}: {stop}")
            digest.problems.append(f"AUTO-PAUSED: {stop}. Check it, then delete {PAUSE_FILE}")
            break
        except Exception as exc:  # one broken repo must not cost the others their run
            first = (str(exc).splitlines() or [type(exc).__name__])[0][:PROBLEM_CHARS]
            if unavailable(exc):  # every later repo would hit the same wall
                log.warning("%s: %s; run stopped, the next hourly run tries again", t.name, exc)
                digest.problems.append(f"Claude unavailable at {t.name}, run stopped: {first}")
                digest.limited = True
                break
            log.exception("%s: run failed", t.name)
            digest.problems.append(f"{t.name}: {first}")
    return digest


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    _setup_logging()
    if paused():
        log.info("paused: %s exists", PAUSE_FILE)
        return 0
    # the timer fires hourly; one scheduled run per day, an interrupted one starts over
    scheduled = a.push and not a.repos
    # a dry run never touches the persistent state: attempts and budget stay unspent
    state = State() if a.push else State(Path(":memory:"))
    if scheduled and state.ran_today() and not _boosting():
        log.debug("already ran today")
        return 0
    started = date.today()  # a run past midnight still counts for the day it began
    sweep()
    try:
        targets, budget = _targets(a)
    except ConfigError as exc:  # every hourly run would fail the same way: say it once a day
        log.error("config: %s", exc)
        if a.push and state.mark("config-error") != started.isoformat():
            send(f"greenkeep: no run, the config is broken: {exc}")
            state.set_mark("config-error", started.isoformat())
        return 1
    # a boost: the reviewer's model also plans each hard change before the patcher writes it
    planner = a.review_model if _boosting() else ""
    opts = Options(a.push, budget, a.model, a.keep, a.review_model, planner)
    digest = _run_all(targets, opts, state)
    text = digest.text(max(opts.budget - state.merged_today(), 0))
    if a.push:
        send(text)
        if scheduled and not digest.limited and (digest.reached or not targets):
            state.record_run(started)
    else:
        log.info("%s", text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
