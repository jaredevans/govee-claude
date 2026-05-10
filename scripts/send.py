#!/usr/bin/env python3
"""Hook client for govee-claude. Stdlib-only.

Usage: send.py <command>

Commands: ensure-running | flash | yellow | red | white | quit
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import traceback
from pathlib import Path

VALID = {"ensure-running", "flash", "yellow", "red", "white", "quit"}


def runtime_dir() -> Path:
    p = os.environ.get("GOVEE_CLAUDE_RUNTIME_DIR")
    if p:
        return Path(p)
    return Path.home() / ".claude" / "govee-claude"


def hook_log(rt: Path, msg: str) -> None:
    try:
        rt.mkdir(parents=True, exist_ok=True)
        with open(rt / "hook.log", "a") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass  # never raise from a hook


def daemon_cmd() -> list[str]:
    override = os.environ.get("GOVEE_CLAUDE_DAEMON_CMD")
    if override:
        return [override]
    plugin_root = Path(__file__).resolve().parent.parent
    return ["uv", "run", "--project", str(plugin_root),
            "python", str(plugin_root / "scripts" / "daemon.py")]


def spawn_daemon(rt: Path) -> None:
    log_path = rt / "daemon.log"
    rt.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as logf:
        subprocess.Popen(
            daemon_cmd(),
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ},
        )


def try_send(sock_path: Path, msg: str, timeout=2.0) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(sock_path))
        s.sendall(msg.encode() + b"\n")
        s.recv(64)
        s.close()
        return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    rt = runtime_dir()
    if len(argv) != 2 or argv[1] not in VALID:
        hook_log(rt, f"bad invocation: {argv!r}")
        return 0  # never break Claude
    cmd = argv[1]
    sock = rt / "daemon.sock"

    try:
        if cmd == "ensure-running":
            if not _socket_alive(sock):
                spawn_daemon(rt)
            return 0

        if try_send(sock, cmd):
            return 0
        # Daemon not reachable — buffer the desired state.
        rt.mkdir(parents=True, exist_ok=True)
        (rt / "last_command").write_text(cmd)
        return 0
    except Exception:
        hook_log(rt, "send.py error:\n" + traceback.format_exc())
        return 0


def _socket_alive(sock_path: Path) -> bool:
    if not sock_path.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(str(sock_path))
        s.close()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv))
