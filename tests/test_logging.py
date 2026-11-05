"""Logging checks: size cap, wide events, one-line records, redaction, the real handlers."""

import io
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import pytest

from greenkeep import events, logsetup
from greenkeep.logsetup import MB, NEWLINE_MARK, RedactFilter, _secrets, prune_logs


def _sparse(path: Path, size: int, mtime: float) -> None:
    with path.open("wb") as fh:
        fh.truncate(size)  # sparse: no real disk used
    os.utime(path, (mtime, mtime))


def _captured(logger: logging.Logger, write) -> list[str]:
    """Lines `write()` sends through `logger`, redaction filter applied."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(RedactFilter())
    logger.addHandler(h)
    try:
        write()
    finally:
        logger.removeHandler(h)
    return buf.getvalue().splitlines()


def test_prune_cap() -> None:
    """1.1 GB of sparse logs -> under the cap; live files survive, the oldest go first."""
    with tempfile.TemporaryDirectory() as d:
        logs, old = Path(d), time.time() - 3600
        for i, name in enumerate(
            (
                "a-cli.log",
                "b-cli.log",
                "greenkeep.log.1",
                "events.jsonl.1",
                "greenkeep.log",
                "events.jsonl",
            )
        ):
            _sparse(logs / name, 200 * MB, old + i)  # listed oldest -> newest
        _sparse(logs / "z-cli.log", 1, old - 30 * 86400)  # past keep_days
        prune_logs(logs, keep_days=14, max_bytes=500 * MB)
        left = {f.name for f in logs.iterdir()}
        assert sum(f.stat().st_size for f in logs.iterdir()) <= 500 * MB, left
        assert {"greenkeep.log", "events.jsonl"} <= left, left
        assert "z-cli.log" not in left and "a-cli.log" not in left, left


def _sample_events() -> None:
    with events.wide("request", ref="u42:r7", user_id=42):
        events.add(decision="sent", note="key SECRETTOKEN42")
        events.add_call(service="llm", ms=3, ok=True)
    try:
        with events.wide("request", ref="x"):
            raise ValueError("boom")
    except ValueError:
        pass
    else:
        raise AssertionError("wide must re-raise")
    events.add(ignored=True)  # outside a unit: no-op


def test_wide_events() -> None:
    _secrets.add("SECRETTOKEN42")
    try:
        lines = _captured(events.log, _sample_events)
    finally:
        _secrets.discard("SECRETTOKEN42")
    assert len(lines) == 2, lines
    ok, err = (json.loads(ln) for ln in lines)
    assert ok["outcome"] == "ok" and ok["ref"] == "u42:r7" and "duration_ms" in ok, ok
    assert ok["calls"] == [{"service": "llm", "ms": 3, "ok": True}], ok
    assert ok["note"] == "key ***" and ok["git_sha"], ok
    assert err["outcome"] == "error", err
    assert err["error"]["type"] == "ValueError" and err["error"]["message"] == "boom", err
    assert "raise ValueError" in err["error"]["traceback"], err


def test_one_line() -> None:
    lg = logging.getLogger("test.oneline")
    lg.propagate = False
    lines = _captured(lg, lambda: lg.warning("got %s", "line one\nline two"))
    assert lines == [f"got line one{NEWLINE_MARK}line two"], lines


def test_budget_fits() -> None:
    """The scheduled run's own capped files fit the folder budget with room for manual runs."""
    ls = logsetup
    daemon = ls.DAEMON_MAX_BYTES * (ls.DAEMON_BACKUPS + 1) + ls.EVENTS_MAX_BYTES * (
        ls.EVENTS_BACKUPS + 1
    )
    assert daemon + ls.CLI_MAX_BYTES * 2 <= ls.LOGS_MAX_BYTES, daemon


@pytest.fixture
def real_setup(tmp_path, monkeypatch):
    """setup_logging on a temp dir; the project loggers are restored afterwards."""
    monkeypatch.setattr(logsetup, "LOGS", tmp_path)
    saved = {
        n: (lg.handlers[:], lg.propagate, lg.level)
        for n in ("greenkeep", "greenkeep.events")
        if (lg := logging.getLogger(n))
    }
    hooks = logsetup.sys.excepthook, logsetup.threading.excepthook
    yield tmp_path
    for n, (h, p, lv) in saved.items():
        lg = logging.getLogger(n)
        lg.handlers, lg.propagate = h, p
        lg.setLevel(lv)
    logsetup.sys.excepthook, logsetup.threading.excepthook = hooks


def test_scheduled_run_files_and_redaction(real_setup) -> None:
    """The scheduled run writes greenkeep.log + events.jsonl, one event line, token masked."""
    logsetup.setup_logging("run")
    _secrets.add("ghp_SECRETVALUE99")
    try:
        with events.wide("repo_run", ref="r:x"):
            events.add(note="token ghp_SECRETVALUE99")
        logsetup.log.warning("multi\nline ghp_SECRETVALUE99")
        try:
            raise ValueError("boom ghp_SECRETVALUE99")
        except ValueError:
            logsetup.log.exception("failed")
    finally:
        _secrets.discard("ghp_SECRETVALUE99")
    ev = (real_setup / "events.jsonl").read_text().splitlines()
    assert len(ev) == 1 and json.loads(ev[0])["note"] == "token ***", ev
    log = (real_setup / "greenkeep.log").read_text()
    assert "ghp_SECRETVALUE99" not in log and f"multi{NEWLINE_MARK}line ***" in log, log


def test_manual_run_gets_its_own_file(real_setup) -> None:
    path = logsetup.setup_logging("manual")
    assert path.name.endswith("-manual.log") and path.parent == real_setup


def test_excepthooks_log_redacted(real_setup, monkeypatch) -> None:
    logsetup.setup_logging("run")
    _secrets.add("ghp_SECRETVALUE99")
    try:
        try:
            raise RuntimeError("leak ghp_SECRETVALUE99")
        except RuntimeError:
            logsetup.sys.excepthook(*logsetup.sys.exc_info())
    finally:
        _secrets.discard("ghp_SECRETVALUE99")
    text = (real_setup / "greenkeep.log").read_text()
    assert "uncaught exception" in text and "leak ***" in text and "SECRETVALUE" not in text, text
