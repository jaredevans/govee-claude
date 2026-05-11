from __future__ import annotations

import fcntl
import json as _json
import logging
import os
import signal as _signal
import socket as _socket
import stat
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol


log = logging.getLogger("govee-claude.daemon")

FLASH_HALF_PERIOD = 1.0  # seconds each color is held in the flash alternation


class _SupportsSetRgb(Protocol):
    def set_rgb(self, rgb: int) -> None: ...


VALID_MODES = {"idle", "flash", "yellow", "red", "white"}


class Daemon:
    """Owns the bulb's mode and the optional flash worker."""

    def __init__(self, *, client: _SupportsSetRgb, colors: dict) -> None:
        self.client = client
        self.colors = colors
        self.mode = "idle"
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    def handle(self, cmd: str) -> str:
        if cmd in ("yellow", "red", "white"):
            self._stop_flash()
            self.mode = cmd
            self._safe_set(self.colors[cmd])
        elif cmd == "flash":
            if self.mode == "flash" and self._worker and self._worker.is_alive():
                return "ok"
            self._stop_flash()
            self.mode = "flash"
            self._stop_event.clear()
            self._worker = threading.Thread(target=self._flash_loop, daemon=True)
            self._worker.start()
        elif cmd == "quit":
            self._stop_flash()
            self.mode = "idle"
        else:
            return f"err: unknown command {cmd!r}"
        return "ok"

    def _stop_flash(self) -> None:
        if self._worker and self._worker.is_alive():
            self._stop_event.set()
            self._worker.join(timeout=0.2)
        self._worker = None

    def _flash_loop(self) -> None:
        toggle = 0
        while True:
            color_name = "blue" if toggle == 0 else "aqua"
            self._safe_set(self.colors[color_name])
            toggle ^= 1
            if self._stop_event.wait(timeout=FLASH_HALF_PERIOD):
                return

    def _safe_set(self, rgb: int) -> None:
        try:
            self.client.set_rgb(rgb)
        except Exception:
            log.exception("set_rgb failed (rgb=0x%06X)", rgb)


class SocketServer:
    """AF_UNIX server that serializes commands to a Daemon."""

    def __init__(self, *, daemon: Daemon, sock_path: Path) -> None:
        self.daemon = daemon
        self.sock_path = Path(sock_path)
        self._server: _socket.socket | None = None
        self._stop = threading.Event()

    def _bind(self) -> _socket.socket:
        if self.sock_path.exists():
            try:
                if stat.S_ISSOCK(self.sock_path.stat().st_mode):
                    self.sock_path.unlink()
                else:
                    self.sock_path.unlink()
            except FileNotFoundError:
                pass
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        srv.listen(8)
        srv.settimeout(0.2)
        return srv

    def serve_forever(self) -> None:
        self._server = self._bind()
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = self._server.accept()
                except _socket.timeout:
                    continue
                with conn:
                    self._handle_conn(conn)
        finally:
            self._server.close()
            self._server = None
            try:
                self.sock_path.unlink()
            except FileNotFoundError:
                pass

    def _handle_conn(self, conn: _socket.socket) -> None:
        conn.settimeout(2.0)
        try:
            data = conn.recv(64)
        except _socket.timeout:
            return
        cmd = data.decode(errors="replace").strip()
        reply = self.daemon.handle(cmd)
        try:
            conn.sendall((reply + "\n").encode())
        except OSError:
            log.warning("client closed before ack")
        if cmd == "quit":
            self.shutdown()

    def shutdown(self) -> None:
        self._stop.set()


def _runtime_dir() -> Path:
    p = os.environ.get("GOVEE_CLAUDE_RUNTIME_DIR")
    if p:
        return Path(p)
    return Path.home() / ".claude" / "govee-claude"


def _setup_logging(runtime: Path) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        runtime / "daemon.log", maxBytes=1_000_000, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


def _acquire_singleton(pid_path: Path):
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(pid_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd  # keep open for the process lifetime


class _RecordingClient:
    """Used in tests via GOVEE_CLAUDE_FAKE_BULB env var. Appends JSONL."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def set_rgb(self, rgb: int) -> None:
        line = _json.dumps({"call": "set_rgb", "rgb": rgb})
        with open(self.path, "a") as f:
            f.write(line + "\n")


def _build_client(config: dict):
    fake = os.environ.get("GOVEE_CLAUDE_FAKE_BULB")
    if fake:
        return _RecordingClient(Path(fake))
    if config["mode"] == "lan":
        from govee.lan import LanClient
        return LanClient(device_ip=config["device_ip"])
    if config["mode"] == "cloud":
        from govee.cloud import CloudClient
        api_key = Path(config["api_key_path"]).read_text().strip()
        return CloudClient(
            api_key=api_key,
            sku=config["sku"],
            device_id=config["device_id"],
        )
    raise SystemExit(f"unknown mode in config: {config['mode']!r}")


def main() -> int:
    runtime = _runtime_dir()
    _setup_logging(runtime)
    log.info("daemon starting (pid=%d)", os.getpid())

    pid_path = runtime / "daemon.pid"
    lock_fd = _acquire_singleton(pid_path)
    if lock_fd is None:
        log.error("another daemon already running — exiting")
        return 2

    cfg_path = runtime / "config.json"
    if not cfg_path.exists():
        log.error("no config at %s — run setup.py first", cfg_path)
        return 3
    config = _json.loads(cfg_path.read_text())

    client = _build_client(config)
    daemon = Daemon(
        client=client,
        colors={k: int(v.lstrip("#"), 16) for k, v in config["colors"].items()},
    )

    last_cmd_path = runtime / "last_command"
    if last_cmd_path.exists():
        cmd = last_cmd_path.read_text().strip()
        log.info("applying buffered last_command=%r", cmd)
        try:
            daemon.handle(cmd)
        finally:
            last_cmd_path.unlink(missing_ok=True)

    server = SocketServer(daemon=daemon, sock_path=runtime / "daemon.sock")

    def _shutdown(_signum, _frame):
        log.info("signal received, shutting down")
        server.shutdown()
        daemon.handle("quit")

    _signal.signal(_signal.SIGTERM, _shutdown)
    _signal.signal(_signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    finally:
        log.info("daemon exiting")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
