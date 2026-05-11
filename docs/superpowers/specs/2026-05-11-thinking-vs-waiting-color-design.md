# Thinking vs Waiting-on-You — design

**Date:** 2026-05-11
**Status:** Approved, not yet implemented
**Depends on:** `2026-05-09-govee-claude-plugin-design.md`

## Goal

Split the single `Notification` color into two so the bulb tells you *why* Claude paused:

- **Red** — Claude is mid-turn and needs your permission to use a tool. Urgent: "go approve it."
- **Purple** — Claude is post-`Stop` and has been idle waiting for your next message for ~60 s. Gentle nudge: "your turn."

The blue/aqua flash continues to mean "Claude is working." This change only refines the `Notification` state.

## Background

Claude Code's `Notification` hook fires in two distinct situations, distinguished only by the `message` field on its stdin JSON:

1. `"Claude needs your permission to use <tool>"` — permission gate, mid-turn.
2. `"Claude is waiting for your input"` — prompt has been idle ~60 s after `Stop`.

Today both map to a single `red` command and look identical on the bulb. This design routes them to different colors by reading the hook's stdin and classifying.

## State machine

Updated table (delta from `2026-05-09-govee-claude-plugin-design.md` §State machine):

| Hook fired         | Command sent                          | New mode           | Bulb shows                       |
|--------------------|---------------------------------------|--------------------|----------------------------------|
| `SessionStart`     | `ensure-running`                      | unchanged          | unchanged                        |
| `UserPromptSubmit` | `flash`                               | `flash`            | alternating blue ↔ aqua          |
| `PostToolUse`      | `flash`                               | `flash`            | clears red/purple back to flash  |
| `Stop`             | `yellow`                              | `yellow`           | solid yellow                     |
| `Notification`     | `notify` → classifies → `red`/`purple`| `red` or `purple`  | solid red **or** solid purple    |
| `SessionEnd`       | `white`                               | `white`            | solid white                      |

Transitions of note:

- **`PostToolUse` clears both red and purple.** Approving a permission prompt causes the tool to run, which fires `PostToolUse`, which sends `flash` — the bulb visibly resumes the working state.
- **`UserPromptSubmit` clears purple.** Typing your next message replaces the idle-waiting purple with flash.
- Purple does not appear mid-turn; red does not appear post-`Stop`. The classifier guarantees this from the message content.

## Approach

Three options were considered:

- **(A) Parse in `send.py`.** New `notify` subcommand reads the Notification JSON from stdin, picks `red` vs `purple` by keyword match on the `message` field, dispatches via the existing socket. One file owns the routing logic; trivially unit-testable. **Chosen.**
- **(B) Parse in `hooks.json` with a shell shim** (jq or `python -c`). Pushes logic into shell strings; harder to test; adds a `jq` dependency.
- **(C) Two `Notification` entries with Claude Code `matcher` filters.** Cleanest in theory, but `matcher` filters on tool name, not message text — not available for this hook event.

## Classifier

Lives in `scripts/send.py` as a pure function so it can be unit-tested without a socket:

```python
def classify_notification(stdin_bytes: bytes) -> str:
    """Returns 'red' or 'purple'. Falls back to 'red' on any error
    so we never regress the existing behavior."""
    try:
        data = json.loads(stdin_bytes)
    except (ValueError, TypeError):
        return "red"
    msg = (data.get("message") or "").lower()
    if "waiting for your input" in msg:
        return "purple"
    if "permission" in msg:
        return "red"
    return "red"  # unknown wording — assume urgent
```

The `notify` command in `send.py`:

1. Reads stdin with a 1 s timeout, capped at 8 KiB (`select` + `os.read`, or `sys.stdin.buffer.read1` with `signal.alarm`).
2. Calls `classify_notification`.
3. Sends the resulting command (`red` or `purple`) through the existing `try_send` path — same buffering and respawn behavior as today.

If stdin times out or the read fails, send `red` (urgent default). All error paths log to `hook.log` and exit 0, like every other `send.py` failure mode.

## Color choice

Default `colors.purple = "#8000FF"`.

Govee RGB bulbs lean warm; the textbook `#800080` reads pink/magenta on this hardware. Pushing the blue channel to full (`#8000FF`, electric violet) keeps the color readably distinct from red on the actual bulb.

The value lives in `config.json` under `colors.purple`, so a user can re-tune it without code changes. `setup.py` writes it on fresh installs. For existing installs that don't re-run setup, `daemon.py` falls back to a hard-coded `0x8000FF` when `colors.purple` is missing, so the upgrade Just Works.

## Components changed

### `scripts/send.py`

- Add `"notify"` to `VALID`.
- Add `classify_notification(stdin_bytes) -> str` (pure function).
- In `main`, when `cmd == "notify"`: read stdin (1 s timeout, 8 KiB cap), classify, dispatch the resulting `red` or `purple` through the existing `try_send` → buffered-respawn flow.
- `"red"` and `"purple"` remain valid direct commands for manual testing and as the dispatch targets from `notify`.

### `scripts/daemon.py`

- Add `"purple"` to `VALID_MODES`.
- Extend the solid-color branch in `Daemon.handle` from `("yellow", "red", "white")` to `("yellow", "red", "white", "purple")`. No flash-worker changes needed; `_stop_flash()` already runs in that branch.
- When constructing the color map, default `colors.get("purple", "#8000FF")` so old configs still work.

### `scripts/setup.py`

- Write `"purple": "#8000FF"` into the `colors` block on fresh configs.
- Existing configs are not migrated automatically (consistent with how the project handles config drift today); the daemon-side default covers them.

### `hooks/hooks.json`

- Change `Notification` from `python3 send.py red` to `python3 send.py notify`.
- All other hooks unchanged.

### Docs

- `README.md` — update the state bullets and the architecture table to spell out red vs purple, and add a one-line note about the `colors.purple` config key.
- `docs/manual-test.md` — add a row: after `Stop`, idle for 60 s, confirm bulb goes purple; type a message, confirm flash returns.
- `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md` — back-reference this design from the state-machine section so future readers don't miss it.

## Error handling

| Failure                                | Behavior                                                                 |
|----------------------------------------|--------------------------------------------------------------------------|
| stdin read times out (1 s)             | `classify_notification` returns `red`; logged                            |
| stdin JSON unparseable                 | Returns `red`; logged                                                    |
| `message` field missing/empty          | Returns `red` (urgent default)                                           |
| Daemon unreachable                     | Existing `last_command` buffer + respawn — works for `purple` as for `red` |
| Old daemon receives `"purple"`         | Replies `err: unknown command 'purple'`; hook ignores reply; bulb stays in prior state. Restart daemon to fix. |
| `colors.purple` missing from config    | Daemon falls back to hard-coded `0x8000FF`                                |

## Testing

### Unit

- **`tests/test_send.py`** — new cases:
  - `classify_notification` returns `red` for `{"message": "Claude needs your permission to use Bash"}`.
  - Returns `purple` for `{"message": "Claude is waiting for your input"}`.
  - Returns `red` for `b""`, malformed JSON, missing `message` field, unknown wording.
  - Routing test: invoking `main(["send.py", "notify"])` with a piped-in waiting-message JSON calls `try_send` with `"purple"` (mock `try_send` and `sys.stdin`).

- **`tests/test_daemon.py`** — extend the parameterized solid-color test to cover `"purple"`: one `set_rgb(0x8000FF)` call, mode == `"purple"`, flash worker stopped if previously running.

- **`tests/test_daemon_main.py`** — `last_command == "purple"` is replayed on startup the same way `last_command == "red"` is today.

- **`tests/test_setup.py`** — fresh-config write includes `"purple": "#8000FF"` in `colors`.

### Integration (`tests/test_integration.py`)

- Pipe a fake `{"message": "Claude is waiting for your input"}` JSON into a subprocess `send.py notify`. Assert the daemon's `RecordingClient` JSONL ends with `{"call": "set_rgb", "rgb": 8388863}` (0x8000FF).
- One symmetric case for the permission message → 0xFF0000.

### Manual

`docs/manual-test.md` gains one row: "after a `Stop`, walk away for 60 s, confirm purple; type something, confirm flash returns."

## Out of scope

- Distinguishing *which* tool needs permission (still flat red regardless of tool).
- Per-session bulb routing (last-write-wins as today).
- Configurable classifier rules — if Claude Code's wording drifts, update the substrings; revisit only if drift becomes frequent.
- Backfilling `colors.purple` into existing user configs via a migration; daemon-side default covers this without a one-shot migration script.
