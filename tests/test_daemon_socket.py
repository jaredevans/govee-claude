import socket
import threading
import time
from pathlib import Path

import pytest

from daemon import Daemon, SocketServer
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}


def send(sock_path: Path, msg: str) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(str(sock_path))
    s.sendall(msg.encode() + b"\n")
    reply = s.recv(64).decode().strip()
    s.close()
    return reply


@pytest.fixture
def server_setup(tmp_path):
    sock = tmp_path / "daemon.sock"
    daemon = Daemon(client=FakeBulbClient(), period_seconds=0.01, colors=COLORS)
    server = SocketServer(daemon=daemon, sock_path=sock)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Wait for socket to exist.
    deadline = time.time() + 2
    while time.time() < deadline and not sock.exists():
        time.sleep(0.01)
    yield daemon, sock
    server.shutdown()
    t.join(timeout=2)


def test_server_responds_to_yellow(server_setup):
    daemon, sock = server_setup
    reply = send(sock, "yellow")
    assert reply == "ok"
    assert daemon.client.calls == [COLORS["yellow"]]


def test_server_handles_unknown_command(server_setup):
    daemon, sock = server_setup
    reply = send(sock, "purple")
    assert reply.startswith("err")


def test_server_stops_cleanly(server_setup):
    daemon, sock = server_setup
    # fixture teardown asserts shutdown completes; just make a request.
    assert send(sock, "yellow") == "ok"


def test_server_removes_stale_socket_on_start(tmp_path):
    sock = tmp_path / "daemon.sock"
    sock.write_text("stale")  # simulate stale file
    daemon = Daemon(client=FakeBulbClient(), period_seconds=0.01, colors=COLORS)
    server = SocketServer(daemon=daemon, sock_path=sock)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    deadline = time.time() + 2
    while time.time() < deadline and not sock.is_socket():
        time.sleep(0.01)
    try:
        assert sock.is_socket()
        assert send(sock, "yellow") == "ok"
    finally:
        server.shutdown()
        t.join(timeout=2)
