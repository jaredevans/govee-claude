from __future__ import annotations

import logging
import os
import socket as _socket
import stat
import threading
from pathlib import Path
from typing import Protocol


log = logging.getLogger("govee-claude.daemon")


class _SupportsSetRgb(Protocol):
    def set_rgb(self, rgb: int) -> None: ...


VALID_MODES = {"idle", "flash", "yellow", "red", "white"}


class Daemon:
    """Owns the bulb's mode and the optional flash worker.

    State is single-threaded: handle() is called from one thread (the socket
    accept loop), the flash worker only writes to the bulb via the client.
    """

    def __init__(
        self,
        *,
        client: _SupportsSetRgb,
        period_seconds: float,
        colors: dict,
    ) -> None:
        self.client = client
        self.period_seconds = period_seconds
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
            if self._stop_event.wait(timeout=self.period_seconds):
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

    def shutdown(self) -> None:
        self._stop.set()
