"""Which port the dashboard serves on.

Busy with another app: take the next free one. Busy with a greenkeep dashboard already:
offer to restart it (default No) and otherwise point at the running one.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

HOST = "127.0.0.1"
MARKER_PATH = "/__greenkeep"
MARKER_APP = "greenkeep-dashboard"
PORT_TRIES = 20
PROBE_TIMEOUT = 2
STOP_WAIT_SECS = 10
STOP_POLL_SECS = 0.2


@dataclass(frozen=True)
class Choice:
    port: int
    serve: bool  # False: a greenkeep dashboard already runs there, just open it

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"


def marker() -> dict:
    """What MARKER_PATH answers: lets a second start recognise the first one."""
    return {"app": MARKER_APP, "pid": os.getpid()}


def is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # as uvicorn binds: TIME_WAIT leftovers of a stopped server do not count as busy,
        # a live listener still does
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def running_dashboard(port: int) -> int | None:
    """PID of the greenkeep dashboard on this port, or None if something else holds it."""
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}{MARKER_PATH}", timeout=PROBE_TIMEOUT
        ) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("app") == MARKER_APP:
        return int(data.get("pid", 0)) or None
    return None


def _stop(pid: int, port: int, kill: Callable[[int, int], None]) -> bool:
    kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_WAIT_SECS
    while time.monotonic() < deadline:
        if is_free(port):
            return True
        time.sleep(STOP_POLL_SECS)
    return False


def choose(
    port: int, ask: Callable[[str], bool], kill: Callable[[int, int], None] = os.kill
) -> Choice:
    """The port to serve on, or the running dashboard to open instead."""
    if is_free(port):
        return Choice(port, serve=True)
    pid = running_dashboard(port)
    if pid is None:
        return Choice(_next_free(port), serve=True)
    url = Choice(port, serve=False).url
    restart = ask(f"greenkeep dashboard already runs at {url} (pid {pid}). Restart it?")
    return Choice(port, serve=restart and _stop(pid, port, kill))


def _next_free(port: int) -> int:
    for candidate in range(port + 1, port + 1 + PORT_TRIES):
        if is_free(candidate):
            return candidate
    raise OSError(f"ports {port}-{port + PORT_TRIES} are all busy")


def ask_tty(question: str) -> bool:
    """[y/N] on a terminal; anything but y/yes, or no terminal at all, means No."""
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False
