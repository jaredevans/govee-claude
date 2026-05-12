import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEND_PATH = REPO_ROOT / "scripts" / "send.py"


import send  # noqa: E402 — scripts/ is on sys.path via conftest.py


def test_classify_returns_purple_for_waiting_message():
    data = b'{"message": "Claude is waiting for your input"}'
    assert send.classify_notification(data) == "purple"


def test_classify_returns_red_for_permission_message():
    data = b'{"message": "Claude needs your permission to use Bash"}'
    assert send.classify_notification(data) == "red"


def test_classify_returns_red_for_empty_bytes():
    assert send.classify_notification(b"") == "red"


def test_classify_returns_red_for_malformed_json():
    assert send.classify_notification(b"not json") == "red"


def test_classify_returns_red_when_message_field_missing():
    assert send.classify_notification(b'{"other": "field"}') == "red"


def test_classify_returns_red_for_unknown_wording():
    data = b'{"message": "something else entirely"}'
    assert send.classify_notification(data) == "red"


def test_classify_is_case_insensitive_on_waiting():
    data = b'{"message": "Claude IS WAITING FOR YOUR INPUT"}'
    assert send.classify_notification(data) == "purple"


def test_classify_handles_non_dict_json():
    assert send.classify_notification(b'["not", "a", "dict"]') == "red"


def fake_daemon(sock_path: Path, log: list[str]):
    """Tiny one-shot AF_UNIX server that records the command and replies 'ok'."""
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.listen(1)
    s.settimeout(3.0)

    def serve():
        try:
            while True:
                try:
                    conn, _ = s.accept()
                except socket.timeout:
                    return
                with conn:
                    data = conn.recv(64).decode().strip()
                    log.append(data)
                    conn.sendall(b"ok\n")
        finally:
            s.close()
            try:
                sock_path.unlink()
            except FileNotFoundError:
                pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


def run_send(args, runtime_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(runtime_dir)
    return subprocess.run(
        [sys.executable, str(SEND_PATH), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )


@pytest.mark.integration
def test_send_yellow_reaches_daemon(tmp_path):
    log: list[str] = []
    fake_daemon(tmp_path / "daemon.sock", log)
    time.sleep(0.05)
    r = run_send(["yellow"], tmp_path)
    assert r.returncode == 0
    assert log == ["yellow"]


def _noop_daemon_cmd(tmp_path: Path) -> Path:
    script = tmp_path / "noop_daemon.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    return script


@pytest.mark.integration
def test_send_writes_last_command_when_daemon_absent(tmp_path):
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(tmp_path)
    env["GOVEE_CLAUDE_DAEMON_CMD"] = str(_noop_daemon_cmd(tmp_path))
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "yellow"],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0  # never break Claude
    assert (tmp_path / "last_command").read_text().strip() == "yellow"


@pytest.mark.integration
def test_send_self_heals_by_spawning_daemon_when_absent(tmp_path):
    """A non-ensure-running command must also spawn the daemon when the
    socket is unreachable, so a crashed/missing daemon recovers without
    waiting for the next SessionStart."""
    runtime = tmp_path
    spawn_marker = tmp_path / "spawned.log"
    spawn_script = tmp_path / "fake_daemon.sh"
    spawn_script.write_text(f'#!/bin/sh\necho started >> "{spawn_marker}"\nsleep 0.5\n')
    spawn_script.chmod(0o755)

    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(runtime)
    env["GOVEE_CLAUDE_DAEMON_CMD"] = str(spawn_script)
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "yellow"],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0
    assert (runtime / "last_command").read_text().strip() == "yellow"

    deadline = time.time() + 2
    while time.time() < deadline and not spawn_marker.exists():
        time.sleep(0.05)
    assert spawn_marker.exists(), "send.py did not spawn the daemon on send failure"


@pytest.mark.integration
def test_send_ensure_running_spawns_daemon(tmp_path, monkeypatch):
    # ensure-running's spawn target must be a real daemon — but for this test we
    # want to assert spawn happens, not run the real daemon. Override DAEMON_CMD.
    runtime = tmp_path
    fake_log = tmp_path / "spawned.log"
    spawn_script = tmp_path / "fake_daemon.sh"
    spawn_script.write_text(f'#!/bin/sh\necho started >> "{fake_log}"\nsleep 0.5\n')
    spawn_script.chmod(0o755)

    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(runtime)
    env["GOVEE_CLAUDE_DAEMON_CMD"] = str(spawn_script)
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "ensure-running"],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0
    # Wait briefly for the spawned subprocess to write.
    deadline = time.time() + 2
    while time.time() < deadline and not fake_log.exists():
        time.sleep(0.05)
    assert fake_log.exists()
