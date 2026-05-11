# govee-claude plugin — design

**Date:** 2026-05-09 (updated to reflect shipped code through commit `aadff3f`)
**Status:** Implemented
**Target device:** Govee H6004 smart bulb ("Claude"), device id `39:24:60:74:F4:D7:A3:3E`

## Goal

A Claude Code plugin that drives a Govee bulb as a status indicator:

- **Claude is working** (between `UserPromptSubmit` and `Stop`): alternating blue/aqua (see `2026-05-11-flash-blue-aqua-alternation-design.md`)
- **Claude finished a turn** (`Stop`): solid yellow
- **Claude needs attention** (`Notification`): solid red, overrides the working state
- **Session ended** (`SessionEnd`): solid white
- **Session start** (`SessionStart`): no bulb change; daemon ensured-running

## Architecture

A single persistent daemon owns all bulb state. Hooks are tiny clients that send a one-word command over an `AF_UNIX` socket and exit. The daemon outlives individual Claude sessions (last-write-wins if multiple sessions are open). It is not a launchd/system service: it starts the first time a `SessionStart` hook fires after boot (or the first time any other hook fails to reach the socket), and dies on reboot or manual kill. Re-spawn is automatic.

```
Claude hook fires
   │
   ▼
hooks/send.py <command>          (≤2s, exits without blocking Claude)
   │
   ▼  AF_UNIX socket
 daemon.py  ───────►  BulbClient (abstract)
 (always-on)             ├── LanClient   (UDP — opportunistic, currently dormant)
                         └── CloudClient (HTTPS Govee Developer API — current default)
```

## Repo layout

```
.claude-plugin/
  plugin.json                  # name, version, description
  marketplace.json             # makes the repo loadable via /plugin marketplace add ./
hooks/
  hooks.json                   # SessionStart + UserPromptSubmit + Stop + Notification + SessionEnd
scripts/
  daemon.py                    # long-running; owns the socket + state machine
  send.py                      # one-shot client used by hooks
  setup.py                     # discovery + config write
  govee/
    __init__.py
    client.py                  # BulbClient interface + RGB helpers
    lan.py                     # UDP LAN client (opt-in)
    cloud.py                   # HTTPS Developer-API client
tests/
  conftest.py
  fakes.py                     # FakeBulbClient, recording transports
  test_client_helpers.py
  test_cloud.py
  test_lan.py
  test_daemon.py               # state machine
  test_daemon_socket.py        # AF_UNIX server
  test_daemon_main.py          # main(), singleton lock, last_command replay
  test_send.py
  test_setup.py
  test_integration.py          # opt-in subprocess smoke test
config/
  config.example.json
docs/
  manual-test.md
  superpowers/specs/...        # this file
pyproject.toml                 # uv-managed; deps: httpx, pytest
```

## Runtime files (per-user, outside the repo)

```
~/.claude/govee-claude/
  config.json                  # mode, device IP/id, colors, api key path
  daemon.sock                  # AF_UNIX socket
  daemon.pid                   # PID file with flock
  daemon.log                   # rotated at 1 MB → daemon.log.1
  hook.log                     # send.py errors only (rare)
  last_command                 # latest desired state if daemon was offline when hook fired
```

`GOVEE_CLAUDE_RUNTIME_DIR` overrides this location (used by tests).

## State machine

Daemon holds one piece of state: `mode ∈ {idle, flash, yellow, red, white}`. Each command sets the bulb color; there is no worker thread. (`flash` is retained as the command name for the working state because the hook contract uses it; the daemon alternates blue/aqua for 1 s each while working.)

| Hook fired         | Command sent       | New mode  | Bulb shows                |
|--------------------|--------------------|-----------|---------------------------|
| `SessionStart`     | `ensure-running`   | unchanged | unchanged                 |
| `UserPromptSubmit` | `flash`            | `flash`   | alternating blue ↔ aqua   |
| `Stop`             | `yellow`           | `yellow`  | solid yellow              |
| `Notification`     | `red`              | `red`     | solid red                 |
| `SessionEnd`       | `white`            | `white`   | solid white               |

Notification mid-turn overwrites the flash; red persists until the next `Stop` (yellow) or next `UserPromptSubmit` (flash resumes).

## Setup & mode detection

`scripts/setup.py` is run manually whenever config needs to be (re)written. Required env vars: `GOVEE_API_KEY_PATH`. Optional: `GOVEE_DEVICE_ID`, `GOVEE_SKU` (default `H6004`), `GOVEE_ENABLE_LAN=1` to opt into LAN discovery.

1. If `GOVEE_DEVICE_ID` is unset, list devices via the cloud and pick the first matching SKU.
2. **LAN discovery (only when `GOVEE_ENABLE_LAN=1`)** — multicast scan to `239.255.255.250:4001` plus the directed broadcast for the host's primary `/24` subnet (e.g., `10.0.0.255:4001`) and `255.255.255.255:4001`, with the multicast send interface pinned to the host's primary IP. Listen on `:4002` for 4 s. The host IP and subnet are derived at runtime — not hardcoded. If our SKU/device responds, write `{mode: "lan", device_ip, ...}` and exit.
3. **Cloud mode (default path)** — validate by calling `/router/api/v1/user/devices` once and confirming the device is present, then write `{mode: "cloud", api_key_path, device_id, sku, ...}`.
4. Print one-line result.

### Current default: cloud

H6004 is on Govee's published LAN-supported list, but on this specific bulb the LAN Control toggle never appeared in the Govee Home app even after a power-cycle. LAN discovery is therefore opt-in; cloud is the default. The LAN codepath and `LanClient` stay in the repo so re-running `setup.py` with `GOVEE_ENABLE_LAN=1` can promote LAN without code changes if the toggle ever surfaces.

## Config

```json
{
  "mode": "cloud",
  "device_ip": null,
  "device_id": "39:24:60:74:F4:D7:A3:3E",
  "sku": "H6004",
  "api_key_path": "/Users/jared/github_projects/govee-claude/govee-api-key.txt",
  "colors": {
    "yellow": "#FFFF00",
    "red":    "#FF0000",
    "blue":   "#0000FF",
    "white":  "#FFFFFF"
  }
}
```

Each state corresponds to at most one cloud call per Claude turn (typically two: `flash` on `UserPromptSubmit`, `yellow` on `Stop`), well under the Govee Developer API limit of ~10 requests/device/min.

## Components

### `BulbClient` interface (`scripts/govee/client.py`)

```python
class BulbClient(Protocol):
    def set_rgb(self, rgb: int) -> None: ...
```

`set_rgb` is the only operation the daemon needs — every state in the state machine maps to one RGB value (white is `0xFFFFFF`, not a power command). Plus a tiny helper for `hex_str → rgb_int`.

### `CloudClient` (`scripts/govee/cloud.py`)

POST to `https://openapi.api.govee.com/router/api/v1/device/control` with `Govee-API-Key` header. Body:
```json
{
  "requestId": "<uuid4>",
  "payload": {
    "sku": "<sku>",
    "device": "<device_id>",
    "capability": {
      "type": "devices.capabilities.color_setting",
      "instance": "colorRgb",
      "value": <int 0-16777215>
    }
  }
}
```
- Retry once on 5xx/network with 500 ms backoff.
- 429 → log, sleep until next minute, drop the in-flight transition.
- 401/403 → log loud remediation, daemon exits.
- 15 s `httpx.Client` timeout.

### `LanClient` (`scripts/govee/lan.py`)

UDP unicast to `device_ip:4003` with the documented Govee LAN JSON for `colorwc`/`color`. Stays in the codebase even though it's currently unused, so re-running setup with `GOVEE_ENABLE_LAN=1` can promote LAN without code changes.

### `daemon.py`

- Single thread serves the AF_UNIX socket and dispatches commands synchronously. No flash worker thread.
- Singleton: writes `daemon.pid`, `flock`s it. Second instance fails fast (exit 2). This makes redundant respawns from `send.py` harmless.
- On startup, reads `last_command` if present and applies it immediately, then deletes the file.
- Crash recovery: stale `daemon.sock` is removed before bind.
- `set_rgb` failures are caught and logged; the daemon does not crash on transient bulb errors.
- Logs to `daemon.log`, rotated once at 1 MB.
- Test hook: when `GOVEE_CLAUDE_FAKE_BULB=<path>` is set, the daemon swaps in a `_RecordingClient` that appends JSONL of every `set_rgb` call to that path.

### `send.py`

- Connects to `daemon.sock`, writes the command word, reads ack, exits.
- 2 s hard timeout. Any error → log to `hook.log`, exit 0 (must not break Claude).
- `ensure-running`: if the socket isn't accepting, spawn the daemon and return.
- Any other command when the send fails: write the command to `last_command`, then spawn a fresh daemon. The daemon picks up `last_command` on startup. The singleton flock makes a redundant spawn safe if one is already (re)starting.
- Daemon spawn: `subprocess.Popen` of `uv run --project <plugin_root> python <plugin_root>/scripts/daemon.py`, `start_new_session=True`, stdout/stderr appended to `daemon.log`. Override the command via `GOVEE_CLAUDE_DAEMON_CMD` (used by tests).

### Plugin manifest (`hooks/hooks.json`)

Hooks reference scripts via `${CLAUDE_PLUGIN_ROOT}`. Each hook command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/send.py" <command>` with the appropriate command word.

## Error handling summary

| Failure | Behavior |
|---|---|
| Daemon socket unreachable from hook | Hook writes `last_command`, spawns a fresh daemon, exits 0 |
| Daemon crashed | Next hook (any of them) re-spawns it; `last_command` is replayed on startup |
| Cloud API 5xx / network blip | Daemon retries once with 500 ms backoff |
| Cloud API 429 | Log; sleep until next minute; drop the in-flight transition |
| Cloud API 401/403 | Log remediation hint; daemon exits |
| Bulb offline | Cloud call fails; logged; resumes on next event |
| Two Claude sessions racing | Last-write-wins; no locking |
| API key file changed | Manual daemon restart (documented) |

## Testing

**Unit**
- `test_cloud.py`: `httpx` recording transport. Asserts request shape, retry on 5xx, no retry on most 4xx, 401/403 surface.
- `test_lan.py`: fake socket; asserts outgoing JSON / port `4003` / target IP.
- `test_client_helpers.py`: hex → RGB-int round-trip for the named colors.
- `test_setup.py`: device discovery and config-write paths.
- `test_send.py`: `try_send` success/failure, `ensure-running` spawn behavior, `last_command` write + daemon spawn on send failure.

**Daemon**
- `test_daemon.py`: drives `Daemon.handle` directly with the `FakeBulbClient` from `tests/fakes.py`. Asserts each command maps to the expected single `set_rgb` call and that mode transitions land where expected.
- `test_daemon_socket.py`: end-to-end through `SocketServer` with stale-socket cleanup.
- `test_daemon_main.py`: `main()` — singleton flock, `last_command` replay on startup, log rotation setup.

**Integration (`tests/test_integration.py`, opt-in)**
- Real subprocess daemon with `GOVEE_CLAUDE_FAKE_BULB=<path>` env var → `_RecordingClient` writes a JSONL recording file.
- Real `send.py` issues commands over the real socket.
- Skipped unless `pytest -m integration`.

**Manual (`docs/manual-test.md`)** — checklist: setup, prompt, finish, permission prompt, end session; verify each color transition.

**Coverage target:** ~90% on daemon + clients. `setup.py` not chased.

## Out of scope

- Multiple bulbs / multi-device fan-out
- HSL / HSV color picker UI
- Telemetry, metrics, dashboards
- Auto-firmware update of the bulb to surface the LAN toggle
- Cross-host coordination (daemon binds local socket only)
