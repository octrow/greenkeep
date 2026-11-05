"""Wide events: one JSON line per unit of work (one repo's maintenance run).

Steps inside the unit add fields to the current event (`add`, `add_call`) instead of
writing their own lines; `wide` writes it once, in `finally`, with duration_ms and outcome.
A failure's traceback goes into the event too, so the caller that catches it logs nothing
more. logsetup.setup_logging routes the "greenkeep.events" logger to events.jsonl.
`jq -c 'select(.ref=="<run>:<repo>")' events.jsonl` finds one unit.
"""

import json
import logging
import os
import subprocess
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

APP_LOGGER = "greenkeep"  # keep in sync with logsetup.APP_LOGGER
ERROR_TEXT_MAX = 300
TRACEBACK_MAX = 4000  # the tail: the raising frame and the exception line matter most
log = logging.getLogger(f"{APP_LOGGER}.events")
log.setLevel(logging.INFO)  # not root's WARNING: events are INFO, even before setup_logging
_current: ContextVar[dict | None] = ContextVar("wide_event", default=None)


def _git_sha() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                timeout=5,
                cwd=Path(__file__).resolve().parent,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "?"
        )
    except (OSError, subprocess.TimeoutExpired):
        return "?"


GIT_SHA = _git_sha()
RUN_ID = time.strftime("%Y%m%d-%H%M%S")  # one per process: the key to a run's events


def emit(kind: str, **fields) -> None:
    """A standalone event (something that outlives its unit: a reply later, a GUI window)."""
    ev = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "kind": kind,
        "git_sha": GIT_SHA,
        "run": RUN_ID,
        "pid": os.getpid(),
        **fields,
    }
    log.info(json.dumps(ev, ensure_ascii=False, default=str))


def add(**fields) -> None:
    """Fields for the current event; a no-op outside `wide`."""
    if (ev := _current.get()) is not None:
        ev.update(fields)


def bump(key: str, n: int = 1) -> None:
    """Count a step into the current event (closed issues, failed attempts…); no-op outside."""
    if (ev := _current.get()) is not None:
        ev[key] = ev.get(key, 0) + n


def append(key: str, item) -> None:
    """One more item in the current event's list `key` (failures, merges…); no-op outside."""
    if (ev := _current.get()) is not None:
        ev.setdefault(key, []).append(item)


def add_call(**call) -> None:
    """One external call (service, ms, ok, cache_hit…) appended to the event's `calls` list."""
    if (ev := _current.get()) is not None:
        ev.setdefault("calls", []).append(call)


@contextmanager
def wide(kind: str, **fields) -> Iterator[dict]:
    """The unit's event: outcome ok/error (+ error type/message), duration_ms; re-raises."""
    ev = dict(fields)
    token = _current.set(ev)
    t0 = time.monotonic()
    try:
        yield ev
        ev.setdefault("outcome", "ok")
    except Exception as exc:
        ev["outcome"] = "error"
        # the traceback lives in the event: callers must not log the same failure again
        ev["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:ERROR_TEXT_MAX],
            "traceback": traceback.format_exc()[-TRACEBACK_MAX:],
        }
        raise
    finally:
        _current.reset(token)
        emit(kind, duration_ms=round((time.monotonic() - t0) * 1000), **ev)
