"""Logging setup: one project logger, size-capped files, wide events, redaction, excepthooks.

`setup_logging(cmd)` once at process start, `register_log_secrets(...)` once the env or a
clone's .env is read. The scheduled run (DAEMON_CMDS) gets the size-rotated greenkeep.log +
events.jsonl; every other run gets its own <ts>-<cmd>.log (several processes must never
share a rotating file: stdlib rotation is not multi-process safe).
"""

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --- CONFIG: adapt per project ------------------------------------------------
APP_LOGGER = "greenkeep"  # the single project logger; modules use getLogger(APP_LOGGER)
LOGS = Path.home() / ".local/state/greenkeep"
DAEMON_CMDS = ("run",)  # the scheduled run: rotating greenkeep.log + events.jsonl
DAEMON_LOG = "greenkeep.log"  # the dashboard tails this file
LIB_LEVELS = (("asyncio", logging.WARNING), ("claude_cli", logging.WARNING))
MB = 1024 * 1024
# Budget: the whole state dir, 200 MB. The scheduled run alone stays under it by its
# handlers (LOG 20x5 + EVENTS 5x6 = 130 MB); the start-up prune trims manual runs to the rest.
LOGS_MAX_BYTES = 200 * MB
LOG_KEEP_DAYS = 14
DAEMON_MAX_BYTES, DAEMON_BACKUPS = 20 * MB, 4
EVENTS_MAX_BYTES, EVENTS_BACKUPS = 5 * MB, 5
CLI_MAX_BYTES = 10 * MB
SECRET_ENV = ("GREENKEEP_TG_BOT_TOKEN",)  # env vars whose values are masked in every record
# -------------------------------------------------------------------------------

LOG_FILE_FMT = "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s]: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"  # files span days: every line carries the date
NEWLINE_MARK = " ⏎ "  # one record = one line (file tracebacks stay multi-line)
# systemd sets JOURNAL_STREAM when stderr goes to journald, which splits multi-line output
# into separate entries: there the console folds tracebacks into the record's line too.
UNDER_JOURNALD = bool(os.environ.get("JOURNAL_STREAM"))
MIN_SECRET_LEN = 8  # shorter values would match ordinary text
LIVE_FILES = (DAEMON_LOG, "events.jsonl")

log = logging.getLogger(APP_LOGGER)
_secrets: set[str] = set()


def register_log_secrets(*values: str) -> None:
    """Values to mask (config tokens); SECRET_ENV values are added automatically."""
    env = (os.environ.get(k, "") for k in SECRET_ENV)
    _secrets.update(v for v in (*values, *env) if v and len(v) >= MIN_SECRET_LEN)


def _redact(text: str) -> str:
    for sec in _secrets:
        text = text.replace(sec, "***")
    return text


class RedactFilter(logging.Filter):
    """Masks secrets (args and tracebacks included); newlines in the message -> NEWLINE_MARK."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _secrets or "\n" in msg:
            record.msg, record.args = _redact(msg).replace("\n", NEWLINE_MARK), None
        # Formatter reuses a set exc_text instead of re-rendering exc_info
        if _secrets and record.exc_info and not record.exc_text:
            record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
        return True


class OneLineFormatter(logging.Formatter):
    """The whole formatted record, traceback included, on one line (for journald)."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace("\n", NEWLINE_MARK)


def prune_logs(
    logs: Path = LOGS, keep_days: int = LOG_KEEP_DAYS, max_bytes: int = LOGS_MAX_BYTES
) -> None:
    """Drop files older than keep_days, then the oldest until the folder fits max_bytes.
    The live daemon files are never dropped (their handlers cap them)."""
    _trim_to(_drop_stale(logs, time.time() - keep_days * 86400), max_bytes)


def _drop_stale(logs: Path, cutoff: float) -> list[tuple[float, int, Path]]:
    """Delete log files modified before `cutoff`; return the rest as (mtime, size, path)."""
    kept = []
    for f in [*logs.glob("*.log*"), *logs.glob("events.jsonl*")]:
        try:
            st = f.stat()
            if st.st_mtime < cutoff:
                f.unlink()
            else:
                kept.append((st.st_mtime, st.st_size, f))
        except OSError:
            pass  # a vanished/locked old log is not worth a warning
    return kept


def _trim_to(files: list[tuple[float, int, Path]], max_bytes: int) -> None:
    """Delete the oldest non-live files until their total size fits max_bytes."""
    total = sum(size for _, size, _ in files)
    for _, size, f in sorted(files, key=lambda x: x[0]):
        if total <= max_bytes:
            return
        if f.name in LIVE_FILES:
            continue
        try:
            f.unlink()
            total -= size
        except OSError:
            pass


def _install_excepthooks() -> None:
    """Uncaught errors in main / worker threads go through the logger (and its redaction)."""

    def _main_hook(et, ev, tb):
        if issubclass(et, KeyboardInterrupt):
            sys.__excepthook__(et, ev, tb)
            return
        log.critical("uncaught exception", exc_info=(et, ev, tb))

    sys.excepthook = _main_hook
    threading.excepthook = lambda a: log.error(
        "uncaught exception in thread %s",
        a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback),
    )


def asyncio_exception_handler(loop, context: dict) -> None:
    """loop.set_exception_handler(asyncio_exception_handler): fire-and-forget task deaths."""
    exc = context.get("exception")
    log.error(
        "asyncio: %s",
        context.get("message", "unhandled error"),
        exc_info=(type(exc), exc, exc.__traceback__) if exc else None,
    )


def _file_handlers(cmd: str) -> tuple[Path, logging.Handler, logging.Handler]:
    """(log path, main file handler, events handler) for this kind of run."""
    if cmd in DAEMON_CMDS:
        path = LOGS / DAEMON_LOG
        fh = RotatingFileHandler(
            path, maxBytes=DAEMON_MAX_BYTES, backupCount=DAEMON_BACKUPS, encoding="utf-8"
        )
        eh = RotatingFileHandler(
            LOGS / "events.jsonl",
            maxBytes=EVENTS_MAX_BYTES,
            backupCount=EVENTS_BACKUPS,
            encoding="utf-8",
        )
        eh.setFormatter(logging.Formatter("%(message)s"))
        eh.addFilter(RedactFilter())
        return path, fh, eh
    path = LOGS / f"{time.strftime('%Y-%m-%d-%H-%M-%S')}-{cmd}.log"
    fh = RotatingFileHandler(path, maxBytes=CLI_MAX_BYTES, backupCount=1, encoding="utf-8")
    return path, fh, fh  # a CLI run keeps its event line in its own log


def _route(name: str, handlers: list[logging.Handler], level: int) -> None:
    lg = logging.getLogger(name)
    for old in lg.handlers:
        if old not in handlers:
            old.close()
    lg.handlers, lg.propagate = handlers, False
    lg.setLevel(level)


def setup_logging(cmd: str = "run") -> Path:
    """File DEBUG (complete record) + console INFO (-> journald under systemd)."""
    LOGS.mkdir(parents=True, exist_ok=True)
    prune_logs()
    path, fh, eh = _file_handlers(cmd)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FILE_FMT, LOG_DATEFMT))
    fh.addFilter(RedactFilter())
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    console_fmt = OneLineFormatter if UNDER_JOURNALD else logging.Formatter
    ch.setFormatter(console_fmt("[%(asctime)s] %(message)s", "%H:%M:%S"))
    ch.addFilter(RedactFilter())
    _route(APP_LOGGER, [fh, ch], logging.DEBUG)
    _route(f"{APP_LOGGER}.events", [eh], logging.INFO)  # wide events: only their own file
    for name, level in LIB_LEVELS:
        _route(name, [fh, ch], level)
    _install_excepthooks()
    log.info("%s start (pid %d), logging to %s", cmd, os.getpid(), path)
    return path
