# govee-claude plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code plugin that drives a Govee H6004 bulb as a status indicator (yellow on Stop, red on Notification, white on SessionEnd, blue↔aqua breathe while working).

**Architecture:** Persistent daemon owns bulb state and the flash loop, listens on `AF_UNIX` socket. Hooks are tiny stdlib-only clients that send a one-word command and exit. Cloud-default with opportunistic LAN; mode-aware flash period.

**Tech Stack:** Python 3.11+, `uv` for venv/deps, `httpx` for cloud HTTP, stdlib `socket`/`threading`/`logging` for everything else, `pytest` for tests.

**Spec:** [`docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`](../specs/2026-05-09-govee-claude-plugin-design.md)

---

## File structure

```
.claude-plugin/plugin.json           # plugin manifest
hooks/hooks.json                     # SessionStart/UserPromptSubmit/Stop/Notification/SessionEnd
scripts/
  send.py                            # hook client — stdlib only
  daemon.py                          # long-running daemon — uses httpx via project venv
  setup.py                           # discovery + config writer
  govee/
    __init__.py                      # empty
    client.py                        # BulbClient Protocol + RGB helpers
    cloud.py                         # CloudClient (httpx)
    lan.py                           # LanClient (UDP)
tests/
  __init__.py                        # empty
  conftest.py                        # adds scripts/ to sys.path
  fakes.py                           # FakeBulbClient, helpers
  test_client_helpers.py             # hex_to_rgb_int etc.
  test_cloud.py                      # CloudClient with httpx MockTransport
  test_lan.py                        # LanClient with patched socket
  test_daemon.py                     # Daemon state machine
  test_send.py                       # send.py behavior
  test_setup.py                      # setup.py discovery & validation
  test_integration.py                # opt-in real-socket end-to-end
config/config.example.json           # example config
docs/manual-test.md                  # manual end-to-end checklist
pyproject.toml                       # uv project + deps
.gitignore                           # ignore __pycache__, .venv, runtime files
```

Runtime files (created at runtime, not in repo): `~/.claude/govee-claude/{config.json, daemon.sock, daemon.pid, daemon.log, hook.log, last_command}`.

---

## Task 1: Project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Create: `scripts/govee/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "govee-claude"
version = "0.1.0"
description = "Govee bulb status indicator for Claude Code"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: opt-in tests using real sockets/subprocesses",
]
addopts = "-m 'not integration'"
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
*.egg-info/
.coverage
```

- [ ] **Step 3: Create empty package/test markers**

```bash
mkdir -p scripts/govee tests
touch scripts/govee/__init__.py tests/__init__.py
```

- [ ] **Step 4: Write `tests/conftest.py` to expose `scripts/` on `sys.path`**

```python
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
```

- [ ] **Step 5: Sync the venv**

Run: `uv sync`
Expected: creates `.venv/`, installs httpx + pytest, no errors.

- [ ] **Step 6: Confirm pytest runs (zero tests yet)**

Run: `uv run pytest`
Expected: `no tests ran` (exit 5 is fine — no tests collected).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore scripts/govee/__init__.py tests/__init__.py tests/conftest.py uv.lock
git commit -m "feat: project skeleton with uv, pytest, package layout"
```

---

## Task 2: BulbClient interface + RGB helpers (TDD)

**Files:**
- Create: `scripts/govee/client.py`
- Create: `tests/test_client_helpers.py`

- [ ] **Step 1: Write the failing test**

`tests/test_client_helpers.py`:
```python
import pytest
from govee.client import hex_to_rgb_int, rgb_int_to_tuple


@pytest.mark.parametrize("hex_str,expected", [
    ("#FF0000", 0xFF0000),
    ("#00FF00", 0x00FF00),
    ("#0000FF", 0x0000FF),
    ("#FFFFFF", 0xFFFFFF),
    ("#000000", 0x000000),
    ("FFFF00", 0xFFFF00),       # leading-# optional
    ("#00ffff", 0x00FFFF),      # case-insensitive
])
def test_hex_to_rgb_int(hex_str, expected):
    assert hex_to_rgb_int(hex_str) == expected


def test_hex_to_rgb_int_rejects_bad_input():
    for bad in ["", "#FFF", "#GGGGGG", "12345", "#1234567"]:
        with pytest.raises(ValueError):
            hex_to_rgb_int(bad)


def test_rgb_int_to_tuple():
    assert rgb_int_to_tuple(0xFF0000) == (255, 0, 0)
    assert rgb_int_to_tuple(0x00FF00) == (0, 255, 0)
    assert rgb_int_to_tuple(0x0000FF) == (0, 0, 255)
    assert rgb_int_to_tuple(0xFFFFFF) == (255, 255, 255)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'govee.client'`.

- [ ] **Step 3: Write minimal implementation**

`scripts/govee/client.py`:
```python
from __future__ import annotations

from typing import Protocol


class BulbClient(Protocol):
    def set_rgb(self, rgb: int) -> None: ...


def hex_to_rgb_int(hex_str: str) -> int:
    s = hex_str.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"invalid hex color: {hex_str!r}")
    return int(s, 16)


def rgb_int_to_tuple(rgb: int) -> tuple[int, int, int]:
    return ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_helpers.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/govee/client.py tests/test_client_helpers.py
git commit -m "feat(govee): BulbClient protocol and hex/RGB helpers"
```

---

## Task 3: CloudClient (TDD with httpx MockTransport)

**Files:**
- Create: `scripts/govee/cloud.py`
- Create: `tests/test_cloud.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cloud.py`:
```python
import json

import httpx
import pytest

from govee.cloud import CloudClient, CloudAuthError, CloudRateLimited


def make_client(handler, api_key="test-key", sku="H6004",
                device_id="DE:AD:BE:EF:CA:FE:00:01"):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, timeout=5.0)
    return CloudClient(api_key=api_key, sku=sku, device_id=device_id, http=http)


def test_set_rgb_sends_correct_request():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 200, "msg": "success"})

    c = make_client(handler)
    c.set_rgb(0xFF0000)

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/router/api/v1/device/control"
    assert req.headers["Govee-API-Key"] == "test-key"
    body = json.loads(req.content)
    assert body["payload"]["sku"] == "H6004"
    assert body["payload"]["device"] == "DE:AD:BE:EF:CA:FE:00:01"
    assert body["payload"]["capability"]["instance"] == "colorRgb"
    assert body["payload"]["capability"]["value"] == 0xFF0000
    assert "requestId" in body


def test_retries_once_on_5xx():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": 200})

    c = make_client(handler)
    c.set_rgb(0x00FF00)
    assert calls["n"] == 2


def test_does_not_retry_on_400():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"message": "bad"})

    c = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        c.set_rgb(0x0000FF)
    assert calls["n"] == 1


def test_401_raises_auth_error():
    def handler(request):
        return httpx.Response(401, json={"message": "bad key"})

    c = make_client(handler)
    with pytest.raises(CloudAuthError):
        c.set_rgb(0x0000FF)


def test_429_raises_rate_limited():
    def handler(request):
        return httpx.Response(429, json={"message": "slow down"})

    c = make_client(handler)
    with pytest.raises(CloudRateLimited):
        c.set_rgb(0x0000FF)


def test_list_devices_returns_payload():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/router/api/v1/user/devices"
        return httpx.Response(200, json={
            "code": 200,
            "data": [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}],
        })

    c = make_client(handler)
    devices = c.list_devices()
    assert devices == [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cloud.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'govee.cloud'`.

- [ ] **Step 3: Write minimal implementation**

`scripts/govee/cloud.py`:
```python
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

API_BASE = "https://openapi.api.govee.com"


class CloudAuthError(RuntimeError):
    """Govee API rejected the API key (401/403)."""


class CloudRateLimited(RuntimeError):
    """Govee API returned 429."""


class CloudClient:
    def __init__(
        self,
        *,
        api_key: str,
        sku: str,
        device_id: str,
        http: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.sku = sku
        self.device_id = device_id
        self.http = http or httpx.Client(base_url=API_BASE, timeout=15.0)
        # When user passes a transport-only client, base_url may be empty —
        # we use absolute URLs in requests so it works either way.

    def _headers(self) -> dict[str, str]:
        return {
            "Govee-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _post_with_retry(self, url: str, body: dict[str, Any]) -> httpx.Response:
        for attempt in (1, 2):
            try:
                resp = self.http.post(url, headers=self._headers(), json=body)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                continue
            if resp.status_code in (401, 403):
                raise CloudAuthError(f"Govee API auth failed: {resp.status_code} {resp.text}")
            if resp.status_code == 429:
                raise CloudRateLimited("Govee API rate limited (429)")
            if 500 <= resp.status_code < 600 and attempt == 1:
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    def set_rgb(self, rgb: int) -> None:
        body = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": self.sku,
                "device": self.device_id,
                "capability": {
                    "type": "devices.capabilities.color_setting",
                    "instance": "colorRgb",
                    "value": rgb,
                },
            },
        }
        self._post_with_retry(f"{API_BASE}/router/api/v1/device/control", body)

    def list_devices(self) -> list[dict[str, Any]]:
        for attempt in (1, 2):
            try:
                resp = self.http.get(
                    f"{API_BASE}/router/api/v1/user/devices",
                    headers=self._headers(),
                )
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                continue
            if resp.status_code in (401, 403):
                raise CloudAuthError(f"Govee API auth failed: {resp.status_code}")
            if 500 <= resp.status_code < 600 and attempt == 1:
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("data", []))
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cloud.py -v`
Expected: PASS (6 passed). Note: tests using `time.sleep(0.5)` in retry path will add ~0.5s to one test. Acceptable.

- [ ] **Step 5: Commit**

```bash
git add scripts/govee/cloud.py tests/test_cloud.py
git commit -m "feat(govee): CloudClient with retry, auth/rate-limit error classes"
```

---

## Task 4: LanClient (TDD with patched socket)

**Files:**
- Create: `scripts/govee/lan.py`
- Create: `tests/test_lan.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_lan.py`:
```python
import json

from govee.lan import LanClient


class FakeUDPSocket:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


def test_set_rgb_sends_lan_packet():
    fake = FakeUDPSocket()
    c = LanClient(device_ip="10.0.0.42", socket_factory=lambda: fake)
    c.set_rgb(0xFF0000)

    assert len(fake.sent) == 1
    data, addr = fake.sent[0]
    assert addr == ("10.0.0.42", 4003)
    payload = json.loads(data)
    assert payload["msg"]["cmd"] == "colorwc"
    assert payload["msg"]["data"]["color"] == {"r": 255, "g": 0, "b": 0}
    assert payload["msg"]["data"]["colorTemInKelvin"] == 0


def test_set_rgb_close_socket():
    fake = FakeUDPSocket()
    c = LanClient(device_ip="10.0.0.42", socket_factory=lambda: fake)
    c.set_rgb(0x00FFFF)
    assert fake.closed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'govee.lan'`.

- [ ] **Step 3: Write minimal implementation**

`scripts/govee/lan.py`:
```python
from __future__ import annotations

import json
import socket
from typing import Callable

from .client import rgb_int_to_tuple

LAN_CMD_PORT = 4003


def _default_socket_factory() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


class LanClient:
    def __init__(
        self,
        *,
        device_ip: str,
        socket_factory: Callable[[], socket.socket] = _default_socket_factory,
    ) -> None:
        self.device_ip = device_ip
        self._socket_factory = socket_factory

    def set_rgb(self, rgb: int) -> None:
        r, g, b = rgb_int_to_tuple(rgb)
        payload = {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": r, "g": g, "b": b},
                    "colorTemInKelvin": 0,
                },
            }
        }
        sock = self._socket_factory()
        try:
            sock.sendto(json.dumps(payload).encode(), (self.device_ip, LAN_CMD_PORT))
        finally:
            sock.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lan.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/govee/lan.py tests/test_lan.py
git commit -m "feat(govee): LanClient sends colorwc packets over UDP"
```

---

## Task 5: Daemon state machine (TDD, no socket)

**Files:**
- Create: `scripts/daemon.py` (state-machine portion only this task)
- Create: `tests/fakes.py`
- Create: `tests/test_daemon.py`

- [ ] **Step 1: Write `tests/fakes.py`**

```python
from __future__ import annotations

import threading


class FakeBulbClient:
    """Records every set_rgb call. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[int] = []
        self.fail_next: int = 0

    def set_rgb(self, rgb: int) -> None:
        with self._lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise RuntimeError("simulated failure")
            self.calls.append(rgb)

    def snapshot(self) -> list[int]:
        with self._lock:
            return list(self.calls)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_daemon.py`:
```python
import time

import pytest

from daemon import Daemon
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}


def make_daemon(period=0.01):
    return Daemon(client=FakeBulbClient(), period_seconds=period, colors=COLORS)


def wait_for(predicate, timeout=1.0, interval=0.005):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_initial_mode_is_idle():
    d = make_daemon()
    assert d.mode == "idle"
    assert d.client.calls == []


def test_yellow_sets_solid_yellow():
    d = make_daemon()
    d.handle("yellow")
    assert d.mode == "yellow"
    assert d.client.calls == [COLORS["yellow"]]


def test_red_sets_solid_red():
    d = make_daemon()
    d.handle("red")
    assert d.client.calls == [COLORS["red"]]


def test_white_sets_solid_white():
    d = make_daemon()
    d.handle("white")
    assert d.client.calls == [COLORS["white"]]


def test_flash_emits_blue_and_aqua():
    d = make_daemon(period=0.01)
    try:
        d.handle("flash")
        assert wait_for(lambda: COLORS["blue"] in d.client.calls and COLORS["aqua"] in d.client.calls)
    finally:
        d.handle("quit")


def test_flash_to_yellow_stops_cleanly():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("yellow")

    # No more emissions after the final yellow.
    final_calls = d.client.snapshot()
    assert final_calls[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final_calls


def test_flash_to_red_stops_cleanly():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("red")
    final_calls = d.client.snapshot()
    assert final_calls[-1] == COLORS["red"]
    time.sleep(0.05)
    assert d.client.snapshot() == final_calls


def test_red_then_flash_then_yellow_ends_yellow():
    d = make_daemon(period=0.01)
    d.handle("red")
    d.handle("flash")
    assert wait_for(lambda: COLORS["blue"] in d.client.calls or COLORS["aqua"] in d.client.calls)
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_double_flash_is_idempotent():
    d = make_daemon(period=0.01)
    d.handle("flash")
    first_worker = d._worker  # noqa: SLF001
    d.handle("flash")
    assert d._worker is first_worker  # noqa: SLF001
    d.handle("quit")


def test_set_rgb_failure_does_not_crash_flash_loop():
    d = make_daemon(period=0.005)
    d.client.fail_next = 2
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 3)
    d.handle("quit")


def test_quit_stops_worker():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: d._worker is not None and d._worker.is_alive())  # noqa: SLF001
    d.handle("quit")
    time.sleep(0.05)
    assert d._worker is None or not d._worker.is_alive()  # noqa: SLF001
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'daemon'`.

- [ ] **Step 4: Write minimal implementation**

`scripts/daemon.py`:
```python
from __future__ import annotations

import logging
import threading
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: PASS (10 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/daemon.py tests/fakes.py tests/test_daemon.py
git commit -m "feat(daemon): state machine with flash worker, TDD"
```

---

## Task 6: Daemon socket layer + main entry point

**Files:**
- Modify: `scripts/daemon.py`
- Create: `tests/test_daemon_socket.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_daemon_socket.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon_socket.py -v`
Expected: FAIL with `ImportError: cannot import name 'SocketServer'`.

- [ ] **Step 3: Add `SocketServer` to `scripts/daemon.py`**

Append to `scripts/daemon.py`:
```python
import os
import socket as _socket
import stat
from pathlib import Path


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon_socket.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/daemon.py tests/test_daemon_socket.py
git commit -m "feat(daemon): AF_UNIX SocketServer with stale-socket recovery"
```

---

## Task 7: Daemon main() — singleton, last_command, log rotation, signal handling

**Files:**
- Modify: `scripts/daemon.py`
- Create: `tests/test_daemon_main.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_daemon_main.py`:
```python
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
```

- [ ] **Step 2: Add `main()`, singleton lock, FakeBulbClient bridge, last_command, log rotation to `scripts/daemon.py`**

Append/modify in `scripts/daemon.py`:
```python
import fcntl
import json as _json
import signal as _signal
import sys
from logging.handlers import RotatingFileHandler


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


def _resolve_period(config: dict) -> float:
    p = float(config.get("flash_period_seconds", 6.0))
    floor = 1.0 if config.get("mode") == "lan" else 6.0
    if os.environ.get("GOVEE_CLAUDE_FAKE_BULB"):
        floor = 0.0  # tests want fast period
    return max(p, floor)


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
        period_seconds=_resolve_period(config),
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
```

Note: `RecordingClient` writes JSONL with the integer rgb value. That's why the integration test asserts on the integer (e.g., `16776960` = `0xFFFF00`).

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest -m integration tests/test_daemon_main.py -v`
Expected: PASS (3 passed).

Also run unit tests to ensure nothing regressed: `uv run pytest`
Expected: PASS (all non-integration tests still green).

- [ ] **Step 4: Commit**

```bash
git add scripts/daemon.py tests/test_daemon_main.py
git commit -m "feat(daemon): main() with singleton lock, last_command replay, log rotation"
```

---

## Task 8: send.py — hook client

**Files:**
- Create: `scripts/send.py`
- Create: `tests/test_send.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_send.py`:
```python
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


@pytest.mark.integration
def test_send_writes_last_command_when_daemon_absent(tmp_path):
    r = run_send(["yellow"], tmp_path)
    assert r.returncode == 0  # never break Claude
    assert (tmp_path / "last_command").read_text().strip() == "yellow"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -m integration tests/test_send.py -v`
Expected: FAIL — `send.py` doesn't exist.

- [ ] **Step 3: Implement `scripts/send.py`**

`scripts/send.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -m integration tests/test_send.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/send.py tests/test_send.py
git commit -m "feat(send): hook client with last_command buffering and daemon spawn"
```

---

## Task 9: setup.py — discovery + cloud validation

**Files:**
- Create: `scripts/setup.py`
- Create: `tests/test_setup.py`
- Create: `config/config.example.json`

- [ ] **Step 1: Write `config/config.example.json`**

```json
{
  "mode": "cloud",
  "device_ip": null,
  "device_id": "REPLACE_ME",
  "sku": "H6004",
  "api_key_path": "REPLACE_ME",
  "flash_period_seconds": 6.0,
  "colors": {
    "yellow": "#FFFF00",
    "red":    "#FF0000",
    "blue":   "#0000FF",
    "aqua":   "#00FFFF",
    "white":  "#FFFFFF"
  }
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_setup.py`:
```python
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import setup as setup_mod


def test_lan_discovery_returns_ip_when_device_responds(monkeypatch):
    fake_response = {
        "msg": {
            "cmd": "scan",
            "data": {
                "ip": "10.0.0.42",
                "device": "DE:AD:BE:EF:CA:FE:00:01",
                "sku": "H6004",
            },
        }
    }
    monkeypatch.setattr(setup_mod, "_collect_lan_responses",
                        lambda local_ip, timeout: [fake_response])
    ip = setup_mod.lan_discover(target_sku="H6004", target_device="DE:AD:BE:EF:CA:FE:00:01",
                                local_ip="10.0.0.58", timeout=0.1)
    assert ip == "10.0.0.42"


def test_lan_discovery_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr(setup_mod, "_collect_lan_responses",
                        lambda local_ip, timeout: [])
    assert setup_mod.lan_discover("H6004", "DE:AD", "10.0.0.58", timeout=0.1) is None


def test_validate_cloud_passes_when_device_present():
    def handler(request):
        return httpx.Response(200, json={
            "code": 200,
            "data": [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}],
        })
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, timeout=5.0)
    assert setup_mod.validate_cloud("k", "H6004", "DE:AD:BE:EF:CA:FE:00:01", http=http) is True


def test_validate_cloud_returns_false_when_missing():
    def handler(request):
        return httpx.Response(200, json={"code": 200, "data": []})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert setup_mod.validate_cloud("k", "H6004", "DE:AD", http=http) is False


def test_validate_cloud_raises_on_auth_failure():
    def handler(request):
        return httpx.Response(401, json={"message": "bad"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(setup_mod.SetupError):
        setup_mod.validate_cloud("k", "H6004", "DE:AD", http=http)


def test_write_config_lan(tmp_path):
    setup_mod.write_config(tmp_path / "cfg.json", mode="lan",
                           device_ip="10.0.0.42",
                           device_id="DE:AD:BE:EF:CA:FE:00:01",
                           sku="H6004",
                           api_key_path="/tmp/k.txt")
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    assert cfg["mode"] == "lan"
    assert cfg["device_ip"] == "10.0.0.42"
    assert cfg["flash_period_seconds"] == 1.0


def test_write_config_cloud(tmp_path):
    setup_mod.write_config(tmp_path / "cfg.json", mode="cloud",
                           device_ip=None,
                           device_id="DE:AD",
                           sku="H6004",
                           api_key_path="/tmp/k.txt")
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    assert cfg["mode"] == "cloud"
    assert cfg["flash_period_seconds"] == 6.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'setup'`.

- [ ] **Step 4: Implement `scripts/setup.py`**

```python
#!/usr/bin/env python3
"""govee-claude setup: discover device on LAN or fall back to cloud, write config."""
from __future__ import annotations

import sys
from pathlib import Path

# Make `from govee.cloud import ...` work when this file is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
import os
import socket
import time
from typing import Iterable

import httpx

from govee.cloud import API_BASE


SCAN_PAYLOAD = json.dumps({
    "msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}
}).encode()


class SetupError(RuntimeError):
    pass


def _primary_local_ip() -> str:
    """Pick the local IP the kernel would use to reach the public internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _broadcast_for_24(ip: str) -> str:
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def _collect_lan_responses(local_ip: str, timeout: float) -> list[dict]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    listener.bind((local_ip, 4002))
    listener.settimeout(0.3)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sender.bind((local_ip, 0))

    targets = [
        ("239.255.255.250", 4001),
        (_broadcast_for_24(local_ip), 4001),
        ("255.255.255.255", 4001),
    ]
    for _ in range(3):
        for t in targets:
            try:
                sender.sendto(SCAN_PAYLOAD, t)
            except OSError:
                pass
        time.sleep(0.4)
    sender.close()

    end = time.time() + timeout
    out: list[dict] = []
    while time.time() < end:
        try:
            data, _ = listener.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            out.append(json.loads(data))
        except ValueError:
            pass
    listener.close()
    return out


def lan_discover(target_sku: str, target_device: str, local_ip: str | None = None,
                 timeout: float = 4.0) -> str | None:
    ip = local_ip or _primary_local_ip()
    for resp in _collect_lan_responses(ip, timeout):
        d = resp.get("msg", {}).get("data", {})
        if d.get("sku") == target_sku or d.get("device") == target_device:
            found_ip = d.get("ip")
            if found_ip:
                return found_ip
    return None


def validate_cloud(api_key: str, sku: str, device_id: str, *,
                   http: httpx.Client | None = None) -> bool:
    h = http or httpx.Client(timeout=10.0)
    try:
        resp = h.get(
            f"{API_BASE}/router/api/v1/user/devices",
            headers={"Govee-API-Key": api_key},
        )
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise SetupError(f"network error talking to Govee: {e}")
    if resp.status_code in (401, 403):
        raise SetupError(f"Govee API key rejected: {resp.status_code}")
    resp.raise_for_status()
    devices = resp.json().get("data", []) or []
    return any(d.get("sku") == sku and d.get("device") == device_id for d in devices)


def write_config(path: Path, *, mode: str, device_ip: str | None,
                 device_id: str, sku: str, api_key_path: str) -> None:
    period = 1.0 if mode == "lan" else 6.0
    cfg = {
        "mode": mode,
        "device_ip": device_ip,
        "device_id": device_id,
        "sku": sku,
        "api_key_path": api_key_path,
        "flash_period_seconds": period,
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "aqua":   "#00FFFF",
            "white":  "#FFFFFF",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    runtime = Path(os.environ.get("GOVEE_CLAUDE_RUNTIME_DIR",
                   Path.home() / ".claude" / "govee-claude"))
    runtime.mkdir(parents=True, exist_ok=True)

    sku = os.environ.get("GOVEE_SKU", "H6004")
    device_id = os.environ.get("GOVEE_DEVICE_ID")
    api_key_path = os.environ.get("GOVEE_API_KEY_PATH")
    if not device_id or not api_key_path:
        # Try to discover via cloud list-devices.
        if not api_key_path:
            print("error: set GOVEE_API_KEY_PATH (path to file with API key)",
                  file=sys.stderr)
            return 2
        api_key = Path(api_key_path).read_text().strip()
        h = httpx.Client(timeout=10.0)
        resp = h.get(f"{API_BASE}/router/api/v1/user/devices",
                     headers={"Govee-API-Key": api_key})
        resp.raise_for_status()
        devices = resp.json().get("data", []) or []
        match = next((d for d in devices if d.get("sku") == sku), None)
        if match is None:
            print(f"error: no device with sku={sku} on this account", file=sys.stderr)
            return 3
        device_id = match["device"]

    print(f"resolving best mode for sku={sku} device={device_id} ...")
    lan_ip = lan_discover(sku, device_id)
    if lan_ip:
        write_config(runtime / "config.json", mode="lan", device_ip=lan_ip,
                     device_id=device_id, sku=sku, api_key_path=api_key_path)
        print(f"LAN discovered at {lan_ip}; mode=lan")
        return 0

    api_key = Path(api_key_path).read_text().strip()
    if not validate_cloud(api_key, sku, device_id):
        print(f"error: device {device_id} not found via cloud — re-check IDs",
              file=sys.stderr)
        return 4
    write_config(runtime / "config.json", mode="cloud", device_ip=None,
                 device_id=device_id, sku=sku, api_key_path=api_key_path)
    print("LAN unavailable; mode=cloud")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/setup.py tests/test_setup.py config/config.example.json
git commit -m "feat(setup): LAN discovery + cloud validation + config writer"
```

---

## Task 10: Plugin manifest and hooks

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`

- [ ] **Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "govee-claude",
  "version": "0.1.0",
  "description": "Drives a Govee H6004 bulb as a status indicator for Claude Code (yellow on Stop, red on Notification, white on SessionEnd, blue/aqua breathe while working)."
}
```

- [ ] **Step 2: Write `hooks/hooks.json`**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" ensure-running",
            "timeout": 2
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" flash",
            "timeout": 2
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" yellow",
            "timeout": 2
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" red",
            "timeout": 2
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" white",
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Validate JSON**

Run: `python3 -c 'import json,sys; [json.loads(open(p).read()) for p in [".claude-plugin/plugin.json", "hooks/hooks.json"]]; print("ok")'`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .claude-plugin/plugin.json hooks/hooks.json
git commit -m "feat(plugin): manifest and hook definitions"
```

---

## Task 11: Manual test doc + README

**Files:**
- Create: `docs/manual-test.md`
- Modify: `README.md`

- [ ] **Step 1: Write `docs/manual-test.md`**

```markdown
# Manual end-to-end test

After installing the plugin in Claude Code:

1. **Run setup**

   ```bash
   GOVEE_API_KEY_PATH=/Users/jared/github_projects/govee-claude/govee-api-key.txt \
     uv run python scripts/setup.py
   ```

   Expected: prints `LAN unavailable; mode=cloud` (current state) or `LAN discovered at <ip>; mode=lan`. Writes `~/.claude/govee-claude/config.json`.

2. **Start a Claude Code session.** Bulb should not change yet (`SessionStart` only ensures the daemon is running).

3. **Submit a prompt.** Bulb starts breathing blue ↔ aqua at the configured period.

4. **Wait for Claude to finish.** Bulb goes solid yellow.

5. **Trigger a permission prompt** (e.g., a Bash command Claude needs to ask about). Bulb goes solid red.

6. **End the session** (`/exit`). Bulb goes solid white.

7. **Inspect logs:** `tail -f ~/.claude/govee-claude/daemon.log` should show one `set_rgb` log per transition.

If anything misbehaves, check `~/.claude/govee-claude/daemon.log` and `~/.claude/govee-claude/hook.log`.
```

- [ ] **Step 2: Update `README.md`**

```markdown
# govee-claude

A Claude Code plugin that drives a Govee H6004 smart bulb as a status indicator.

- **Working** (between `UserPromptSubmit` and `Stop`): bulb breathes blue ↔ aqua
- **Idle / done** (`Stop`): solid yellow
- **Needs attention** (`Notification`): solid red
- **Session ended** (`SessionEnd`): solid white

## Install

1. Clone this repo.
2. Put your Govee Developer API key in `govee-api-key.txt` at the repo root.
3. Run setup once:

   ```bash
   GOVEE_API_KEY_PATH="$PWD/govee-api-key.txt" uv run python scripts/setup.py
   ```

4. Add the plugin to Claude Code (the repo root contains `.claude-plugin/plugin.json`):

   ```bash
   /plugin marketplace add /path/to/govee-claude
   /plugin install govee-claude
   ```

## Architecture

See [`docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`](docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md).

## Development

```bash
uv sync                                      # set up venv
uv run pytest                                # unit tests
uv run pytest -m integration                 # plus integration tests
```

## Manual test

See [`docs/manual-test.md`](docs/manual-test.md).
```

- [ ] **Step 3: Commit**

```bash
git add docs/manual-test.md README.md
git commit -m "docs: README and manual test checklist"
```

---

## Task 12: Final integration smoke test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write the smoke test**

`tests/test_integration.py`:
```python
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
        "flash_period_seconds": 0.05,
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
        time.sleep(0.25)  # let it breathe a few times
        send("yellow")
        time.sleep(0.05)
        send("red")
        time.sleep(0.05)
        send("white")
        time.sleep(0.05)

        lines = [json.loads(l) for l in rec.read_text().splitlines()]
        rgbs = [e["rgb"] for e in lines]
        assert 0x0000FF in rgbs and 0x00FFFF in rgbs   # flash emitted
        # Yellow happened after flash and was the first solid set after flashing.
        # Red and white follow.
        assert rgbs[-1] == 0xFFFFFF
        assert 0xFFFF00 in rgbs and 0xFF0000 in rgbs
    finally:
        subprocess.run([sys.executable, str(SEND_PATH), "quit"], env=env, timeout=3)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=2)
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest -m integration tests/test_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite (incl. integration) one final time**

Run: `uv run pytest -m "not integration"` then `uv run pytest -m integration`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end integration smoke test"
```

---

## Done criteria

- All tests pass (`uv run pytest` and `uv run pytest -m integration`).
- `python3 scripts/send.py yellow` makes the bulb yellow.
- Plugin loads in Claude Code via `/plugin marketplace add` + `/plugin install`.
- Manual test in `docs/manual-test.md` walks through cleanly.
