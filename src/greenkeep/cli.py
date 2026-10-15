"""greenkeep: one run over the tended repos. With no repos named, the config's list is used.

Without --push it is a dry run: fixes are committed in throwaway clones only and no state
is kept. The scheduled run is `greenkeep --push`.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DEFAULT_BUDGET, PAUSE_FILE, Target, load, pause, paused
from .core import DEFAULT_MODEL, DEFAULT_REVIEW_MODEL, Options
from .digest import Digest, send
from .reverts import AutoPause
from .run import run_repo
from .state import State

log = logging.getLogger("greenkeep")

LOG_FILE = Path.home() / ".local/state/greenkeep/greenkeep.log"
LOG_BYTES = 5_000_000
LOG_BACKUPS = 3
PROBLEM_CHARS = 200


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="greenkeep", description=__doc__)
    p.add_argument("repos", nargs="*", help="local checkouts or owner/name (default: config)")
    p.add_argument("--push", action="store_true",
                   help="open issues/PRs and merge them (default: local dry run)")
    p.add_argument("--budget", type=int, help="merged fix PRs today (default: config)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="OmniRoute model that writes patches")
    p.add_argument("--review-model", default=DEFAULT_REVIEW_MODEL,
                   help="OmniRoute model that reviews each patch before it lands")
    p.add_argument("--keep", action="store_true", help="keep the throwaway clones")
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
    if a.repos:
        return [Target(r) for r in a.repos], a.budget or DEFAULT_BUDGET
    cfg = load()
    return cfg.repos, a.budget or cfg.budget_on(date.today())


def _run_all(targets: list[Target], opts: Options, state: State) -> Digest:
    digest = Digest()
    # least recently merged first: activity spreads over the repos instead of piling up
    for t in sorted(targets, key=lambda t: state.last_merge(t.name)):
        if state.merged_today() >= opts.budget:
            break
        try:
            run_repo(t, opts, state, digest)
        except AutoPause as stop:
            log.warning("auto-pause: %s", stop)
            if opts.push:
                pause(f"auto-paused {date.today()}: {stop}")
            digest.problems.append(f"AUTO-PAUSED: {stop}. Check it, then delete {PAUSE_FILE}")
            break
        except Exception as exc:  # one broken repo must not cost the others their run
            log.exception("%s: run failed", t.name)
            first = (str(exc).splitlines() or [type(exc).__name__])[0]
            digest.problems.append(f"{t.name}: {first[:PROBLEM_CHARS]}")
    return digest


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    _setup_logging()
    if paused():
        log.info("paused: %s exists", PAUSE_FILE)
        return 0
    targets, budget = _targets(a)
    opts = Options(a.push, budget, a.model, a.keep, a.review_model)
    # a dry run never touches the persistent state: attempts and budget stay unspent
    state = State() if a.push else State(Path(":memory:"))
    digest = _run_all(targets, opts, state)
    text = digest.text(max(opts.budget - state.merged_today(), 0))
    if a.push:
        send(text)
    else:
        log.info("%s", text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
