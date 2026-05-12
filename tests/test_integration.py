"""End-to-end integration: real daemon subprocess + real send.py + recording client."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_PATH = REPO_ROOT / "scripts" / "daemon.py"
SEND_PATH = REPO_ROOT / "scripts" / "send.py"


def _write_config(rt: Path):
    cfg = {
        "mode": "fake",
        "device_ip": None,
        "device_id": "DE:AD",
        "sku": "H6006",
        "api_key_path": str(rt / "fake-key.txt"),
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "aqua": "#00FFFF", "white": "#FFFFFF",
            "purple": "#8000FF",
        },
    }
    (rt / "config.json").write_text(json.dumps(cfg))
    (rt / "fake-key.txt").write_text("unused")


@pytest.mark.integration
def test_full_session_flow(tmp_path):
    rt = tmp_path / "rt"
    rt.mkdir()
    _write_config(rt)
    rec = tmp_path / "rec.jsonl"

    env = {**os.environ, "GOVEE_CLAUDE_RUNTIME_DIR": str(rt),
           "GOVEE_CLAUDE_FAKE_BULB": str(rec)}

    p = subprocess.Popen([sys.executable, str(DAEMON_PATH)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # Wait for socket.
        deadline = time.time() + 3
        while time.time() < deadline and not (rt / "daemon.sock").exists():
            time.sleep(0.02)

        def send(cmd):
            r = subprocess.run([sys.executable, str(SEND_PATH), cmd],
                               env=env, capture_output=True, timeout=3)
            assert r.returncode == 0

        send("flash")
        # Give the worker time to emit at least one full cycle: blue (2.0 s) + aqua (0.5 s).
        # 3.0 s leaves a small margin past the 2.5 s cycle for >=2 emissions.
        time.sleep(3.0)
        send("yellow")
        time.sleep(0.1)
        send("red")
        time.sleep(0.1)
        send("white")
        time.sleep(0.1)

        lines = [json.loads(l) for l in rec.read_text().splitlines()]
        rgbs = [e["rgb"] for e in lines]
        assert 0x0000FF in rgbs, f"expected blue in {rgbs!r}"
        assert 0x00FFFF in rgbs, f"expected aqua in {rgbs!r}"
        # After flash stops, the last three calls are the three solid colors in order.
        assert rgbs[-3:] == [0xFFFF00, 0xFF0000, 0xFFFFFF]
    finally:
        subprocess.run([sys.executable, str(SEND_PATH), "quit"], env=env, timeout=3)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=2)


@pytest.mark.integration
def test_notify_drives_purple_for_waiting_and_red_for_permission(tmp_path):
    """Full path: real daemon subprocess + real send.py 'notify' + stdin JSON,
    asserting both classifier branches land at the recording bulb."""
    rt = tmp_path / "rt"
    rt.mkdir()
    _write_config(rt)
    rec = tmp_path / "rec.jsonl"

    env = {**os.environ, "GOVEE_CLAUDE_RUNTIME_DIR": str(rt),
           "GOVEE_CLAUDE_FAKE_BULB": str(rec)}

    p = subprocess.Popen([sys.executable, str(DAEMON_PATH)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not (rt / "daemon.sock").exists():
            time.sleep(0.02)

        def notify(payload: bytes):
            r = subprocess.run(
                [sys.executable, str(SEND_PATH), "notify"],
                env=env,
                input=payload,
                capture_output=True,
                timeout=3,
            )
            assert r.returncode == 0

        notify(b'{"message": "Claude is waiting for your input"}')
        time.sleep(0.1)
        notify(b'{"message": "Claude needs your permission to use Bash"}')
        time.sleep(0.1)

        lines = [json.loads(l) for l in rec.read_text().splitlines()]
        rgbs = [e["rgb"] for e in lines]
        assert 0x8000FF in rgbs, f"expected purple (8388863) in {rgbs!r}"
        assert 0xFF0000 in rgbs, f"expected red (16711680) in {rgbs!r}"
        # Order: purple (waiting) before red (permission).
        purple_idx = rgbs.index(0x8000FF)
        red_idx = rgbs.index(0xFF0000, purple_idx + 1)
        assert purple_idx < red_idx
    finally:
        subprocess.run([sys.executable, str(SEND_PATH), "quit"], env=env, timeout=3)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=2)
