import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_PATH = REPO_ROOT / "scripts" / "daemon.py"


def write_config(runtime_dir: Path, sku="H6004", device_id="DE:AD:BE:EF:CA:FE:00:01"):
    cfg = {
        "mode": "fake",
        "device_ip": None,
        "device_id": device_id,
        "sku": sku,
        "api_key_path": str(runtime_dir / "fake-key.txt"),
        "flash_period_seconds": 0.01,
        "colors": {
            "yellow": "#FFFF00",
            "red": "#FF0000",
            "blue": "#0000FF",
            "aqua": "#00FFFF",
            "white": "#FFFFFF",
        },
    }
    cfg_path = runtime_dir / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    (runtime_dir / "fake-key.txt").write_text("unused")
    return cfg_path


def start_daemon(runtime_dir: Path, recording: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(runtime_dir)
    env["GOVEE_CLAUDE_FAKE_BULB"] = str(recording)
    return subprocess.Popen(
        [sys.executable, str(DAEMON_PATH)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def send(sock_path: Path, msg: str, retries=20) -> str:
    last_err = None
    for _ in range(retries):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(str(sock_path))
            s.sendall(msg.encode() + b"\n")
            reply = s.recv(64).decode().strip()
            s.close()
            return reply
        except (FileNotFoundError, ConnectionRefusedError) as e:
            last_err = e
            time.sleep(0.05)
    raise RuntimeError(f"could not connect to daemon: {last_err}")


@pytest.mark.integration
def test_daemon_main_starts_and_responds(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    write_config(runtime)
    recording = tmp_path / "recording.jsonl"

    p = start_daemon(runtime, recording)
    try:
        reply = send(runtime / "daemon.sock", "yellow")
        assert reply == "ok"
        time.sleep(0.05)
        lines = recording.read_text().splitlines()
        assert any('"set_rgb"' in line and "16776960" in line for line in lines)
    finally:
        send(runtime / "daemon.sock", "quit")
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=2)


@pytest.mark.integration
def test_second_daemon_exits_quickly(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    write_config(runtime)
    recording = tmp_path / "recording.jsonl"

    p1 = start_daemon(runtime, recording)
    try:
        send(runtime / "daemon.sock", "yellow")  # ensure first is up
        p2 = start_daemon(runtime, recording)
        rc = p2.wait(timeout=5)
        assert rc != 0  # singleton should refuse
    finally:
        send(runtime / "daemon.sock", "quit")
        p1.wait(timeout=3)


@pytest.mark.integration
def test_daemon_picks_up_last_command_on_start(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    write_config(runtime)
    (runtime / "last_command").write_text("red")
    recording = tmp_path / "recording.jsonl"

    p = start_daemon(runtime, recording)
    try:
        # Daemon should apply "red" before accepting any connection.
        send(runtime / "daemon.sock", "yellow")  # forces us to wait until socket up
        time.sleep(0.05)
        lines = recording.read_text().splitlines()
        red_seen = any("16711680" in line for line in lines)
        assert red_seen
        assert not (runtime / "last_command").exists()
    finally:
        send(runtime / "daemon.sock", "quit")
        p.wait(timeout=3)
