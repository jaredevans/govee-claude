# govee-claude plugin — design

**Date:** 2026-05-09
**Status:** Approved (architecture, state machine, setup, error handling, testing)
**Target device:** Govee H6004 smart bulb ("Claude"), device id `39:24:60:74:F4:D7:A3:3E`

## Goal

A Claude Code plugin that drives a Govee bulb as a status indicator:

- **Claude is working** (between `UserPromptSubmit` and `Stop`): bulb breathes blue ↔ aqua
- **Claude finished a turn** (`Stop`): solid yellow
- **Claude needs attention** (`Notification`): solid red, overrides flash
- **Session ended** (`SessionEnd`): solid white
- **Session start** (`SessionStart`): no bulb change; daemon ensured-running

## Architecture

A single persistent daemon owns all bulb state and the flash loop. Hooks are tiny clients that send a one-word command over an `AF_UNIX` socket and exit. The daemon outlives individual Claude sessions (last-write-wins if multiple sessions are open). It is not a launchd/system service: it starts the first time a `SessionStart` hook fires after boot, and dies on reboot or manual kill. Re-spawn is automatic on the next `SessionStart`.

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
hooks/
  hooks.json                   # SessionStart + UserPromptSubmit + Stop + Notification + SessionEnd
scripts/
  daemon.py                    # long-running; owns the flash loop + socket
  send.py                      # one-shot client used by hooks
  setup.py                     # discovery + config write
  govee/
    __init__.py
    client.py                  # BulbClient interface + RGB helpers
    lan.py                     # UDP LAN client
    cloud.py                   # HTTPS Developer-API client
tests/
  test_clients.py
  test_daemon.py
  test_integration.py          # marked @pytest.mark.integration, opt-in
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
  config.json                  # mode, device IP/id, colors, flash period, api key path
  daemon.sock                  # AF_UNIX socket
  daemon.pid                   # PID file with flock
  daemon.log                   # rotated at 1 MB → daemon.log.1
  hook.log                     # send.py errors only (rare)
  last_command                 # latest desired state if daemon was offline when hook fired
```

## State machine

Daemon holds one piece of state: `mode ∈ {idle, flash, yellow, red, white}`. A worker thread, while `mode == flash`, alternates blue and aqua at `flash_period_seconds`; otherwise the worker sleeps and the bulb sits on the last solid color.

| Hook fired         | Command sent       | New mode  | Bulb shows                |
|--------------------|--------------------|-----------|---------------------------|
| `SessionStart`     | `ensure-running`   | unchanged | unchanged                 |
| `UserPromptSubmit` | `flash`            | `flash`   | blue ↔ aqua               |
| `Stop`             | `yellow`           | `yellow`  | solid yellow              |
| `Notification`     | `red`              | `red`     | solid red                 |
| `SessionEnd`       | `white`            | `white`   | solid white               |

Notification mid-turn wins over flash; red persists until the next `Stop` (yellow) or next `UserPromptSubmit` (flash resumes).

## Setup & mode detection

`scripts/setup.py` runs on first daemon start (when `config.json` is missing) and can be re-run manually any time.

1. **LAN discovery probe** — multicast scan to `239.255.255.250:4001` plus the directed broadcast for the host's primary `/24` subnet (e.g., `10.0.0.255:4001` for a host on `10.0.0.0/24`), with the multicast send interface pinned to the host's primary IP. Listen on `:4002` for 4 s, retry once. Filter responses for our SKU (`H6004`) / device id. The host IP and subnet are derived at runtime — not hardcoded.
2. **If LAN responds** → write `{mode: "lan", device_ip, device_id, sku, ...}` to config. Default `flash_period_seconds = 1.0`.
3. **If LAN silent** → write `{mode: "cloud", api_key_path, device_id, sku, ...}`. Default `flash_period_seconds = 6.0` (cloud-safe; clamped to ≥6.0 by daemon when `mode == cloud`). Validate by calling `/router/api/v1/user/devices` once and confirming the device is present.
4. Print one-line result.

### Current default: cloud

H6004 is on Govee's published LAN-supported list, but on this specific bulb the LAN Control toggle never appeared in the Govee Home app even after a power-cycle. LAN discovery returns no responses. We treat LAN as opportunistic — the codepath stays in place; if the toggle ever appears, re-running `setup.py` flips the daemon to LAN mode.

## Config

```json
{
  "mode": "cloud",
  "device_ip": null,
  "device_id": "39:24:60:74:F4:D7:A3:3E",
  "sku": "H6004",
  "api_key_path": "/Users/jared/github_projects/govee-claude/govee-api-key.txt",
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

The daemon clamps `flash_period_seconds` to a mode-aware floor: `1.0` for LAN, `6.0` for cloud (Govee Developer API allows ~10 requests/device/min; 6 s period = 10 calls/min).

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

UDP unicast to `device_ip:4003` with the documented Govee LAN JSON for `colorwc`/`color`. Stays in the codebase even though it's currently unused, so re-running setup later can promote LAN without code changes.

### `daemon.py`

- Single thread for socket accept; flash worker runs in a `threading.Thread` controlled by a `threading.Event` (stop signal).
- Singleton: writes `daemon.pid`, `flock`s it. Second instance fails fast.
- Mode-switch protocol: when transitioning out of `flash`, set the stop event and wait up to 200 ms for the worker to exit cleanly before sending the solid-color command. Avoids racing with an in-flight blue/aqua write.
- On startup, reads `last_command` if present and applies it immediately, then deletes the file.
- Crash recovery: stale `daemon.sock` is removed before bind; stale PID (process dead) is detected and ignored.
- Logs to `daemon.log`, rotated once at 1 MB.

### `send.py`

- Connects to `daemon.sock`, writes the command word, reads single-byte ack, exits.
- 2 s hard timeout. Any error → log to `hook.log`, exit 0 (must not break Claude).
- Special command `ensure-running`: if connect fails, `subprocess.Popen([python, daemon.py], start_new_session=True, stdout/err to daemon.log)` and return immediately.
- Other commands when daemon is unreachable: write the command to `last_command` and return. The daemon will pick it up on next start.

### Plugin manifest (`hooks/hooks.json`)

Hooks reference scripts via `${CLAUDE_PLUGIN_ROOT}`. Each hook command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/send.py" <command>` with the appropriate command word.

## Error handling summary

| Failure | Behavior |
|---|---|
| Daemon socket unreachable from hook | Hook writes `last_command`, exits 0 |
| Daemon crashed | Next `SessionStart`'s `ensure-running` re-spawns it |
| Cloud API 5xx / network blip | Daemon retries once with 500 ms backoff |
| Cloud API 429 | Log; sleep until next minute; drop the in-flight transition |
| Cloud API 401/403 | Log remediation hint; daemon exits |
| Bulb offline | Cloud call fails; logged; resumes on next event |
| Two Claude sessions racing | Last-write-wins; no locking |
| API key file changed | Manual daemon restart (documented) |

## Testing

**Unit (`tests/test_clients.py`)**
- `CloudClient`: `httpx` recording transport. Assert request shape, retry on 5xx, no retry on most 4xx, 401 surface.
- `LanClient`: fake socket; assert outgoing JSON / port `4003` / target IP.
- Hex → RGB-int round-trip for the five named colors.

**Daemon (`tests/test_daemon.py`)**
- Inject `FakeBulbClient` recording every call.
- Inject a fast `sleep` so the flash period collapses to ~10 ms.
- Drive the daemon's command handler directly (no socket).
- Assert sequences: `flash` emits ≥1 blue + ≥1 aqua; `flash→yellow`/`flash→red` end with the solid color and no stragglers; `red→flash→yellow` ends solid yellow; `quit` cleanly shuts down.

**Integration (`tests/test_integration.py`, opt-in)**
- Real subprocess daemon with `GOVEE_CLAUDE_FAKE_BULB=<path>` env var → `FakeBulbClient` writes a recording file.
- Real `send.py` issues commands over the real socket.
- Skipped unless `pytest -m integration`.

**Manual (`docs/manual-test.md`)** — checklist: setup, prompt, finish, permission prompt, end session; verify each color transition.

**Coverage target:** ~90% on daemon + clients. `send.py` and `setup.py` not chased.

## Out of scope

- Multiple bulbs / multi-device fan-out
- HSL / HSV color picker UI
- Telemetry, metrics, dashboards
- Auto-firmware update of the bulb to surface the LAN toggle
- Cross-host coordination (daemon binds local socket only)
