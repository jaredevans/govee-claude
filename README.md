# govee-claude

A Claude Code plugin that drives a Govee H6006 smart bulb as an ambient status indicator. The bulb changes color in response to Claude Code hook events — you can tell what Claude is doing without looking at the terminal.

<a href="https://a.co/d/02L9Aotl"><img src="govee-bulb.jpg" alt="Govee H6006 bulb" width="288"></a>

## States

| Bulb | When | Hook |
|------|------|------|
| Blue 2 s ↔ aqua 0.5 s (asymmetric flash) | Claude is working | `UserPromptSubmit`, `PostToolUse` |
| Solid yellow | Turn finished, idle | `Stop` |
| Solid red | "Claude needs your permission to use X" — go approve | `Notification` (permission subtype) |
| Solid purple | "Claude is waiting for your input" — ~60 s after `Stop` | `Notification` (idle subtype) |
| Solid white | Session ended | `SessionEnd` |
| (no change) | Daemon ensured running | `SessionStart` |

`PostToolUse` clears red and purple back to the flash, so approving a permission prompt visibly resumes the working state. Typing a new prompt while idle (purple) does the same.

## Install

1. **Clone the repo.**

   ```bash
   git clone git@github.com:jaredevans/govee-claude.git
   cd govee-claude
   ```

2. **Get a Govee Developer API key** from [developer.govee.com](https://developer.govee.com) and save it to `govee-api-key.txt` at the repo root. (The file is gitignored.)

3. **Run setup.** This resolves your device, attempts LAN discovery, and writes `~/.claude/govee-claude/config.json`.

   ```bash
   GOVEE_API_KEY_PATH="$PWD/govee-api-key.txt" uv run python scripts/setup.py
   ```

   Setup tries LAN first (UDP discovery on your `/24`); if no device responds within ~4 s, it falls back to cloud mode. Either works; LAN is faster and avoids the Govee API rate limit.

4. **Install the plugin in Claude Code:**

   ```
   /plugin marketplace add /path/to/govee-claude
   /plugin install govee-claude
   ```

### Setup env vars

| Var | Default | Purpose |
|-----|---------|---------|
| `GOVEE_API_KEY_PATH` | *(required)* | Path to a file containing your API key (single line) |
| `GOVEE_SKU` | `H6006` | Device SKU. Set this if you're using a different model |
| `GOVEE_DEVICE_ID` | *(auto-discovered)* | Override the device MAC if your account has multiple bulbs |
| `GOVEE_CLAUDE_RUNTIME_DIR` | `~/.claude/govee-claude` | Override runtime/state dir (used by tests) |

## Architecture

```
Claude Code hook fires
        │
        ▼
   scripts/send.py <command>      ← stateless, ≤2 s, never breaks Claude
        │
        ▼  AF_UNIX socket
   scripts/daemon.py               ← per-user singleton (flock)
        │
        ▼
   BulbClient (abstract)
        ├── LanClient   (UDP unicast → device_ip:4003, fire-and-forget)
        └── CloudClient (HTTPS → openapi.api.govee.com, rate-limited)
```

**Hooks are tiny clients.** Each hook in `hooks/hooks.json` runs `python3 send.py <command>` and exits within 2 seconds. `send.py` writes a one-word command to the daemon's UNIX socket, reads the ack, and exits. Any failure (daemon down, socket missing, exception) is swallowed — hooks never break Claude.

**The daemon owns all bulb state.** It runs as a per-user singleton enforced by `flock` on `~/.claude/govee-claude/daemon.pid`. It accepts commands on `~/.claude/govee-claude/daemon.sock` and serializes them through a small state machine. The flash mode runs in a worker thread that alternates blue (2 s) and aqua (0.5 s) using `threading.Event.wait` so transitions out of flash are responsive.

**Self-healing.** If `send.py` can't reach the daemon, it writes the desired command to `last_command` and spawns a fresh daemon process. The new daemon picks up `last_command` on startup before opening its socket, so the buffered transition is applied even if it never actually saw the original hook fire. The flock makes redundant spawns safe.

**The `notify` command is special.** Unlike the other commands, it reads JSON from stdin (the Notification hook payload) and runs a tiny keyword classifier on the `message` field: `"waiting for your input"` → purple, anything else with `"permission"` → red, error/unknown → red. The classification is in `scripts/send.py:classify_notification`.

### Runtime files (per-user, outside the repo)

```
~/.claude/govee-claude/
  config.json            mode (lan/cloud), device id/ip, api key path, colors
  daemon.sock            AF_UNIX socket
  daemon.pid             pid file, flocked by the running daemon
  daemon.log             rotated at 1 MB → daemon.log.1
  hook.log               send.py errors only (rare)
  last_command           latest desired state if daemon was offline when a hook fired
```

### State machine

The daemon has one piece of state: `mode ∈ {idle, flash, yellow, red, white, purple}`. Solid-color commands stop any running flash worker and set the bulb once. `flash` starts (or no-ops on) the worker. `quit` stops the worker and shuts down via the socket-server stop path.

## Daily management

**Inspect what the daemon is doing:**

```bash
tail -f ~/.claude/govee-claude/daemon.log
```

Look for one `set_rgb` per state transition. Transient bulb errors are logged but don't crash the daemon.

**Restart the daemon** (e.g. after editing `config.json`):

```bash
python3 scripts/send.py quit
```

The next hook will spawn a fresh daemon automatically. The flock makes this safe even if a second `quit` races in.

**Tail hook errors:** these should be rare — `send.py` only writes here when something unusual happens (bad invocation, IO error).

```bash
tail -f ~/.claude/govee-claude/hook.log
```

**Multiple Claude sessions on the same machine** share one daemon and one bulb. The daemon serializes commands but state is **not** scoped per session — last-write-wins. If one session's `SessionEnd` sends `quit`, the next command from any session respawns the daemon and replays its `last_command`.

**Override colors:** edit `~/.claude/govee-claude/config.json` and restart the daemon. Each color is a hex string (e.g. `"purple": "#8000FF"`). If a color is missing, the daemon uses a built-in default for `purple` (`0x8000FF`) and fails the `set_rgb` for others.

## Manual verification

After install, walk through this once to confirm everything works:

1. Run setup. Expect a line like `LAN discovered at 10.0.0.137; mode=lan` (or `no LAN response; falling back to cloud`, then `mode=cloud`). Confirm `~/.claude/govee-claude/config.json` exists.
2. Start a Claude Code session. Bulb should not change yet (`SessionStart` only ensures the daemon is up).
3. Submit any prompt. Bulb shows blue 2 s, then aqua 0.5 s, repeating.
4. Wait for Claude to finish a turn. Bulb goes solid yellow.
5. Trigger a permission prompt (e.g. a Bash command Claude needs to ask about). Bulb goes solid red. Approve it — bulb returns to flash while the tool runs.
6. After Claude finishes, wait ~60 s without typing. Bulb goes solid purple ("Claude is waiting for your input"). Type any message — bulb returns to flash.
7. End the session (`/exit`). Bulb goes solid white.
8. Tail `~/.claude/govee-claude/daemon.log` and confirm one `set_rgb` log line per transition.

## Troubleshooting

**Bulb doesn't respond at all.** Check `~/.claude/govee-claude/daemon.log` for cloud API errors (401 → bad API key; 429 → rate limited; transport errors → bulb offline or network blip). For LAN mode, check that the bulb's IP in `config.json` matches what's actually on your LAN — IPs can change.

**Daemon not running.** Run any hook command manually to see what happens:

```bash
python3 scripts/send.py ensure-running
ls -la ~/.claude/govee-claude/daemon.sock     # socket should exist
cat ~/.claude/govee-claude/daemon.log         # latest startup attempt
```

If the daemon refuses to start (`error: no config at ...`), re-run setup.

**LAN discovery fails on a new install.** The bulb must be powered on, joined to the same `/24` as your machine, and have LAN Control enabled in the Govee Home app. Setup falls back to cloud automatically.

**Hooks misfire silently.** Each hook has a 2 s timeout and exits 0 on any error. If the bulb isn't responding to a particular event, check `hook.log` for `send.py` exceptions and `daemon.log` for the corresponding command.

## Development

```bash
uv sync                            # set up the dev venv
uv run pytest                      # unit tests (integration skipped by default)
uv run pytest -m integration       # integration tests (use real subprocesses + sockets)
```

Project layout:

```
.claude-plugin/      plugin manifest + marketplace entry
hooks/hooks.json     Claude Code hook bindings
scripts/
  send.py            one-shot client used by hooks
  daemon.py          long-running singleton, owns the bulb
  setup.py           one-time config writer (device discovery + LAN/cloud probe)
  govee/             BulbClient interface + LAN/Cloud implementations
config/
  config.example.json   reference shape for ~/.claude/govee-claude/config.json
tests/               pytest + integration tests
```

`tests/conftest.py` puts `scripts/` on `sys.path`, so tests import `send`, `daemon`, `setup` directly.

## License

See `LICENSE`.
