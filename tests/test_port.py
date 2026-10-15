import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from greenkeep import port


@pytest.fixture
def busy_port():
    """A port held by some other app (a bare bound socket, no HTTP)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((port.HOST, 0))
        s.listen()
        yield s.getsockname()[1]


@pytest.fixture
def dashboard():
    """A fake greenkeep dashboard: answers the marker like the real one."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"app": port.MARKER_APP, "pid": 4242}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer((port.HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def never(question):
    pytest.fail(f"must not ask: {question}")


def test_free_port_is_used_as_is(busy_port):
    with socket.socket() as s:
        s.bind((port.HOST, 0))
        free = s.getsockname()[1]
    assert port.choose(free, ask=never) == port.Choice(free, serve=True)


def test_port_of_another_app_moves_to_the_next_free_one(busy_port):
    choice = port.choose(busy_port, ask=never)
    assert choice.serve and choice.port > busy_port


def test_running_dashboard_is_reused_by_default(dashboard):
    p = dashboard.server_address[1]
    questions = []
    choice = port.choose(p, ask=lambda q: questions.append(q) or False)
    assert choice == port.Choice(p, serve=False)
    assert "already runs at http://127.0.0.1:" in questions[0] and "pid 4242" in questions[0]


def test_restart_kills_the_running_dashboard_and_takes_its_port(dashboard):
    p = dashboard.server_address[1]
    killed = []

    def kill(pid, sig):
        killed.append(pid)
        dashboard.shutdown()
        dashboard.server_close()

    choice = port.choose(p, ask=lambda q: True, kill=kill)
    assert killed == [4242] and choice == port.Choice(p, serve=True)


def test_ask_tty_defaults_to_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert port.ask_tty("Restart?") is False
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert port.ask_tty("Restart?") is True
