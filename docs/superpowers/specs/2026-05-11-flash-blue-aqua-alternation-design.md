# Flash: blue/aqua alternation — design

**Date:** 2026-05-11
**Status:** Approved, not yet implemented
**Supersedes (partially):** the `flash` behavior described in `2026-05-09-govee-claude-plugin-design.md` (solid blue)
**Target device:** Govee H6006, LAN mode, `10.0.0.137` (current setup)

## Goal

Replace the **Claude is working** signal — currently solid blue — with an alternating blue/aqua pattern. Each color is held for **1.0 second**, giving a 2-second cycle and a 1 Hz toggle rate.

All other states (yellow / red / white) and the hook contract are unchanged.

## Background

A worker-thread flash implementation existed in commit `6984d48` ("state machine with flash worker, TDD") and was removed in `aadff3f` ("replace flash-breathe with solid blue, harden recovery") at the same time the bulb was an H6004 driven over the cloud API. With the H6006 now reachable over LAN (UDP fire-and-forget), the per-second cadence is cheap and trivial. This spec restores the worker-thread approach with a tighter, fixed cadence.

## Behavior

| Hook command | Daemon behavior |
|---|---|
| `flash` | If already flashing, no-op. Otherwise: start a background worker that alternates blue ↔ aqua, holding each for `FLASH_HALF_PERIOD` seconds. |
| `yellow` / `red` / `white` | Stop the flash worker (if running), then set the solid color once. |
| `quit` | Stop the flash worker; daemon exits via the existing socket-server shutdown path. |

The worker uses `threading.Event.wait(timeout)` rather than `time.sleep` so that a stop request unblocks within milliseconds.

## Code changes

### `scripts/daemon.py`

Restore the worker thread machinery from `6984d48`, with two adjustments:

1. The half-period is a module-level constant, not a config field:
   ```python
   FLASH_HALF_PERIOD = 1.0  # seconds per color in the flash cycle
   ```
2. The `Daemon.__init__` signature loses any `period_seconds` parameter — the worker reads `FLASH_HALF_PERIOD` directly. Tests that need a faster cycle monkeypatch the module constant.

New / restored members on `Daemon`:
- `self._stop_event: threading.Event`
- `self._worker: threading.Thread | None`
- `_stop_flash()`: sets the event, joins with a 0.2 s timeout, nulls `_worker`
- `_flash_loop()`: toggles between `colors["blue"]` and `colors["aqua"]` via `_safe_set`, sleeping via `self._stop_event.wait(FLASH_HALF_PERIOD)` between toggles, returning when the event fires

`handle()` updates:
- `cmd == "flash"`: if `self.mode == "flash"` and `self._worker.is_alive()`, return `"ok"` without restarting. Otherwise stop any prior worker, clear the event, start a new daemon thread.
- `cmd in ("yellow", "red", "white")`: call `_stop_flash()` before `_safe_set`.
- `cmd == "quit"`: call `_stop_flash()` before setting `self.mode = "idle"`.

The shutdown signal handler at the bottom of `main()` already calls `daemon.handle("quit")`, so it inherits the stop behavior for free.

### `scripts/setup.py`

Re-add the `aqua` entry to the colors dict written into `config.json`:
```python
"colors": {
    "yellow": "#FFFF00",
    "red":    "#FF0000",
    "blue":   "#0000FF",
    "aqua":   "#00FFFF",
    "white":  "#FFFFFF",
},
```

### `config/config.example.json`

Mirror the same change.

### Live config migration

The user re-runs `setup.py` after this change ships. The existing `~/.claude/govee-claude/config.json` lacks `aqua`; until it's rewritten, a `flash` command would raise `KeyError` inside `_safe_set` and be swallowed by the existing `try/except`. Acceptable for the brief window between deploy and re-run, but the manual-test doc reminds the user to re-run setup.

## Tests

Restore the flash-worker tests removed in `aadff3f` (see `git show aadff3f -- tests/test_daemon.py`), adapted for:

- The constant-based half-period — tests monkeypatch `daemon.FLASH_HALF_PERIOD` to something small (e.g. 0.01) for speed. (Tests import the module as `daemon`, not `scripts.daemon` — see `tests/test_daemon.py`.)
- A recording fake bulb that captures every `set_rgb(rgb)` call with a timestamp.

Required test cases:

1. **`flash` starts a worker that alternates blue then aqua.** Assert the first two `set_rgb` calls are blue and aqua (in that order), spaced by ~`FLASH_HALF_PERIOD`.
2. **`flash` is idempotent.** Calling `flash` twice without an intervening color command does not spawn a second worker; the first one keeps running uninterrupted.
3. **`yellow` / `red` / `white` stop the worker.** After a flash + solid-color sequence, no further `set_rgb` calls happen beyond the final solid color, even after waiting longer than the half-period.
4. **`quit` stops the worker.** Same as above but ends in idle mode with no further calls.
5. **Stop is responsive.** The worker exits well inside the half-period when `_stop_event` is set (the test uses a small monkeypatched period to keep wall-clock time short, but the assertion is about behavior, not absolute timing).
6. **`_safe_set` swallows exceptions from the bulb client.** Already covered by existing tests; ensure flash-loop behavior is unchanged when `set_rgb` raises.

Integration test (`tests/test_integration.py`): update the section that exercises `flash` to assert the recording fake sees at least one blue + one aqua call across a brief wait window. Keep the monkeypatch trick to avoid real-time sleeps.

## Docs

- `README.md` — change the "Working" bullet from "solid blue" to "alternating blue/aqua (1 s each)".
- `docs/manual-test.md` — update step 3's expectation to describe the alternation.
- `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md` — add a short note at the top of the **working** description pointing at this spec for the live behavior; leave the rest of the doc as the architectural reference.

## Non-goals / what does NOT change

- Hook contract: `send.py` still emits `flash` for `UserPromptSubmit`.
- Socket protocol, AF_UNIX path, singleton flock, `last_command` buffering.
- LAN / cloud client APIs.
- Plugin manifest / marketplace entry (no version bump needed — covered by the next release bump if any).
- Any handling of `aqua` color outside of the flash worker.

## Risks

- **Thread leak on daemon shutdown:** mitigated because the worker is `daemon=True` (Python interpreter shutdown kills it), and the SIGTERM/SIGINT path calls `handle("quit")` which joins it.
- **Set_rgb backpressure under LAN UDP:** none expected — UDP send is non-blocking and the bulb has no rate limit at 1 Hz.
- **Set_rgb backpressure under cloud:** if a future user runs in cloud mode again, 1 Hz API calls might trip rate limits. Out of scope for this change since the current target is LAN; document the risk only in this spec, not in user-facing docs.
