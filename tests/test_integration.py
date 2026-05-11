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
        "sku": "H6004",
        "api_key_path": str(rt / "fake-key.txt"),
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "aqua": "#00FFFF", "white": "#FFFFFF",
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
        # Give the worker time to emit at least one full cycle: blue + aqua.
        # FLASH_HALF_PERIOD is 1.0 s, so 2.5 s is enough for >=2 emissions.
        time.sleep(2.5)
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
