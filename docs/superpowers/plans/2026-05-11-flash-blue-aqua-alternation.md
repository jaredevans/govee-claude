# Flash blue/aqua alternation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "Claude is working" indicator from solid blue to alternating blue/aqua, holding each color for 1.0 second.

**Architecture:** Restore the worker-thread pattern from commit `6984d48` (reverted in `aadff3f`) with a hard-coded module-level constant for the half-period instead of a config field. `Daemon.handle("flash")` spawns a daemon thread that alternates blue↔aqua and uses `threading.Event.wait()` between toggles so any subsequent solid-color or quit command interrupts it within milliseconds.

**Tech Stack:** Python stdlib (`threading`), pytest, existing `FakeBulbClient` (already thread-safe).

**Spec:** `docs/superpowers/specs/2026-05-11-flash-blue-aqua-alternation-design.md`

---

## File Map

- **Modify** `scripts/daemon.py` — add `FLASH_HALF_PERIOD` constant; restore `_stop_event`, `_worker`, `_stop_flash()`, `_flash_loop()`; wire stops into `handle()` for `yellow`/`red`/`white`/`quit`; make `flash` start the worker.
- **Modify** `tests/test_daemon.py` — replace solid-blue assertions with alternation assertions; add idempotency + responsive-stop tests; add aqua to the local `COLORS` dict.
- **Modify** `scripts/setup.py` — re-add `"aqua": "#00FFFF"` to the dict in `write_config()`.
- **Modify** `config/config.example.json` — re-add aqua.
- **Modify** `tests/test_setup.py` — flip the `assert "aqua" not in cfg["colors"]` to `assert cfg["colors"]["aqua"] == "#00FFFF"`.
- **Modify** `tests/test_integration.py` — include aqua in the fake config; flash step asserts at least one blue+one aqua call within a short window.
- **Modify** `README.md` — update the "Working" bullet.
- **Modify** `docs/manual-test.md` — update step 3 expectation.
- **Modify** `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md` — pointer note that flash now alternates, see new spec.

---

## Task 1: Add `FLASH_HALF_PERIOD` constant and update tests for alternation behavior (TDD)

**Files:**
- Modify: `tests/test_daemon.py` (the whole file)
- Modify: `scripts/daemon.py` (add constant; later tasks add the worker)

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_daemon.py` with:

```python
import time

import pytest

import daemon as daemon_mod
from daemon import Daemon
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}


@pytest.fixture(autouse=True)
def _fast_flash(monkeypatch):
    """Shrink the flash half-period so tests don't wait whole seconds."""
    monkeypatch.setattr(daemon_mod, "FLASH_HALF_PERIOD", 0.01)


def make_daemon():
    return Daemon(client=FakeBulbClient(), colors=COLORS)


def _wait_for(predicate, timeout=1.0, interval=0.005):
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


def test_flash_alternates_blue_and_aqua():
    d = make_daemon()
    try:
        d.handle("flash")
        assert d.mode == "flash"
        assert _wait_for(
            lambda: COLORS["blue"] in d.client.calls and COLORS["aqua"] in d.client.calls
        )
    finally:
        d.handle("quit")


def test_flash_then_yellow_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final
    assert d.mode == "yellow"


def test_flash_then_red_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("red")
    final = d.client.snapshot()
    assert final[-1] == COLORS["red"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_flash_then_white_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("white")
    final = d.client.snapshot()
    assert final[-1] == COLORS["white"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_red_then_flash_then_yellow_ends_yellow():
    d = make_daemon()
    d.handle("red")
    d.handle("flash")
    assert _wait_for(
        lambda: COLORS["blue"] in d.client.calls or COLORS["aqua"] in d.client.calls
    )
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_double_flash_is_idempotent():
    d = make_daemon()
    try:
        d.handle("flash")
        first_worker = d._worker  # noqa: SLF001
        d.handle("flash")
        assert d._worker is first_worker  # noqa: SLF001
        assert d._worker.is_alive()  # noqa: SLF001
    finally:
        d.handle("quit")


def test_quit_stops_worker():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(
        lambda: d._worker is not None and d._worker.is_alive()  # noqa: SLF001
    )
    d.handle("quit")
    assert d.mode == "idle"
    assert _wait_for(
        lambda: d._worker is None or not d._worker.is_alive()  # noqa: SLF001
    )


def test_set_rgb_failure_does_not_crash_flash_loop():
    d = make_daemon()
    d.client.fail_next = 2
    try:
        d.handle("flash")
        # Loop should keep going past the failures and eventually emit at least one call.
        assert _wait_for(lambda: len(d.client.calls) >= 1, timeout=2.0)
    finally:
        d.handle("quit")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_daemon.py -x`

Expected: failures referencing `FLASH_HALF_PERIOD` not existing in `daemon` (the `monkeypatch.setattr` raises `AttributeError`) or, after the constant exists, failures about `aqua` never appearing in `d.client.calls`. Either failure mode is fine — both prove the tests can't pass without the worker.

- [ ] **Step 3: Add the constant to `scripts/daemon.py`**

Insert near the top of `scripts/daemon.py`, just below the imports and `log = logging.getLogger(...)` line and above `class _SupportsSetRgb`:

```python
FLASH_HALF_PERIOD = 1.0  # seconds each color is held in the flash alternation
```

- [ ] **Step 4: Re-run the tests to confirm the constant exists but tests still fail**

Run: `uv run pytest tests/test_daemon.py -x`

Expected: tests that touch `flash` still fail (no aqua call ever emitted), but the `monkeypatch.setattr` no longer errors. The solid-color tests (`test_yellow_sets_solid_yellow`, etc.) should still pass.

- [ ] **Step 5: Do NOT commit yet — implementation comes in Task 2.**

---

## Task 2: Implement the flash worker thread in the daemon

**Files:**
- Modify: `scripts/daemon.py`

- [ ] **Step 1: Replace the `Daemon` class body**

In `scripts/daemon.py`, replace the existing `class Daemon:` block (currently lines 27–52 — the class definition through `_safe_set`) with this:

```python
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
```

Note: `threading` is already imported at the top of the file.

- [ ] **Step 2: Run the daemon tests**

Run: `uv run pytest tests/test_daemon.py -v`

Expected: all tests in `tests/test_daemon.py` pass.

- [ ] **Step 3: Run the full unit suite (excluding integration) to make sure nothing else broke**

Run: `uv run pytest -m "not integration" -q`

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): alternate blue/aqua during flash mode

Restores the worker-thread pattern from 6984d48. Each color is held
for FLASH_HALF_PERIOD (1.0 s). Subsequent solid-color or quit commands
stop the worker promptly via threading.Event.wait().
"
```

---

## Task 3: Re-add `aqua` to the config writer and example config (TDD)

**Files:**
- Modify: `tests/test_setup.py`
- Modify: `scripts/setup.py`
- Modify: `config/config.example.json`

- [ ] **Step 1: Update the failing setup test**

In `tests/test_setup.py`, replace this line (currently around line 81):

```python
    assert "aqua" not in cfg["colors"]
```

with:

```python
    assert cfg["colors"]["aqua"] == "#00FFFF"
```

Also add an aqua assertion to `test_write_config_lan` (currently around lines 61–70). After the existing `assert cfg["colors"]["blue"] == "#0000FF"` line, add:

```python
    assert cfg["colors"]["aqua"] == "#00FFFF"
```

- [ ] **Step 2: Run the setup tests and confirm they fail**

Run: `uv run pytest tests/test_setup.py -v`

Expected: `test_write_config_lan` and `test_write_config_cloud` fail with `KeyError: 'aqua'`.

- [ ] **Step 3: Add aqua to `scripts/setup.py`**

In `scripts/setup.py`, in the `write_config()` function (the `cfg = {...}` literal), update the `"colors"` dict from:

```python
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "white":  "#FFFFFF",
        },
```

to:

```python
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "aqua":   "#00FFFF",
            "white":  "#FFFFFF",
        },
```

- [ ] **Step 4: Update `config/config.example.json`**

Replace the entire file contents with:

```json
{
  "mode": "cloud",
  "device_ip": null,
  "device_id": "REPLACE_ME",
  "sku": "H6004",
  "api_key_path": "REPLACE_ME",
  "colors": {
    "yellow": "#FFFF00",
    "red":    "#FF0000",
    "blue":   "#0000FF",
    "aqua":   "#00FFFF",
    "white":  "#FFFFFF"
  }
}
```

- [ ] **Step 5: Run the setup tests and confirm they pass**

Run: `uv run pytest tests/test_setup.py -v`

Expected: all setup tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/setup.py config/config.example.json tests/test_setup.py
git commit -m "feat(setup): re-add aqua to default colors

Restores the aqua entry dropped in aadff3f, required by the new
blue/aqua flash alternation in the daemon.
"
```

---

## Task 4: Update the integration test for alternation

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Update the integration test**

In `tests/test_integration.py`:

1. In `_write_config()`, change the `"colors"` dict from:

```python
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "white": "#FFFFFF",
        },
```

to:

```python
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "aqua": "#00FFFF", "white": "#FFFFFF",
        },
```

2. Replace the assertion section (currently around lines 55–66 — the `send("flash") ... assert rgbs == [...]` block) with:

```python
        send("flash")
        # Give the worker time to emit at least one full cycle: blue + aqua.
        # FLASH_HALF_PERIOD is 1.0 s, so 2.5 s is enough for ≥2 emissions.
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
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest -m integration -v`

Expected: `test_full_session_flow` passes. Total runtime ~4 seconds (one flash cycle + transition delays).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q && uv run pytest -m integration -q`

Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): verify flash alternation emits blue and aqua"
```

---

## Task 5: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/manual-test.md`
- Modify: `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`

- [ ] **Step 1: Update `README.md`**

In `README.md`, change line 5 from:

```
- **Working** (between `UserPromptSubmit` and `Stop`): solid blue
```

to:

```
- **Working** (between `UserPromptSubmit` and `Stop`): alternating blue/aqua (1 s each)
```

- [ ] **Step 2: Update `docs/manual-test.md`**

Change step 3 from:

```
3. **Submit a prompt.** Bulb goes solid blue while Claude is working.
```

to:

```
3. **Submit a prompt.** Bulb alternates blue ↔ aqua (1 s each) while Claude is working.
```

Also, near the top of the file (after the "After installing the plugin in Claude Code:" line and before the "1. **Run setup**" header), add a note so users re-run setup to pick up the new aqua color:

```
> **Note:** If you installed before the blue/aqua flash change, re-run step 1 once — your existing `~/.claude/govee-claude/config.json` is missing the `aqua` color.
```

- [ ] **Step 3: Update the original design spec**

In `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`, change line 11 from:

```
- **Claude is working** (between `UserPromptSubmit` and `Stop`): solid blue
```

to:

```
- **Claude is working** (between `UserPromptSubmit` and `Stop`): alternating blue/aqua (see `2026-05-11-flash-blue-aqua-alternation-design.md`)
```

- [ ] **Step 4: Verify the docs render cleanly**

Run: `uv run python -c "import pathlib; [print(p) for p in pathlib.Path('docs').rglob('*.md')]"`

Spot-check: open `README.md` and `docs/manual-test.md` and confirm the new wording reads correctly and that no leftover "solid blue" strings remain in either file.

Run: `grep -n "solid blue" README.md docs/manual-test.md docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md || echo "no stale references"`

Expected: `no stale references`.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/manual-test.md docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md
git commit -m "docs: describe blue/aqua flash alternation"
```

---

## Task 6: Verify against the live bulb

This is a manual step — not run automatically by an executing agent. The agent should pause here and prompt the user.

- [ ] **Step 1: Stop any running daemon**

Run: `uv run python scripts/send.py quit`

Then verify no daemon remains: `ps aux | grep daemon.py | grep -v grep || echo "stopped"`

- [ ] **Step 2: Re-run setup to write `aqua` into the live config**

Run:

```bash
GOVEE_ENABLE_LAN=1 GOVEE_SKU=H6006 GOVEE_API_KEY_PATH="$PWD/govee-api-key.txt" \
  uv run python scripts/setup.py
```

Expected: `LAN discovered at <ip>; mode=lan`.

Verify aqua is in the live config: `grep aqua ~/.claude/govee-claude/config.json`

Expected: a line showing `"aqua": "#00FFFF"`.

- [ ] **Step 3: Test alternation against the real bulb**

Run:

```bash
uv run python scripts/send.py flash
```

Watch the bulb for ~5 seconds. Expected: it alternates blue↔aqua, each held for ~1 second.

- [ ] **Step 4: Test that a solid color cleanly interrupts the alternation**

Run:

```bash
uv run python scripts/send.py yellow
```

Expected: bulb goes solid yellow within ~1 second and stays there.

- [ ] **Step 5: Cycle through the remaining states for full coverage**

Run:

```bash
uv run python scripts/send.py flash && sleep 4 && \
uv run python scripts/send.py red && sleep 2 && \
uv run python scripts/send.py white
```

Expected: alternate for ~4 s, then solid red for ~2 s, then solid white.

- [ ] **Step 6: Stop and confirm the agent prompts the user**

Before reporting the plan complete, the agent must explicitly ask the user to confirm they saw the bulb alternate. UDP is fire-and-forget — log inspection can't prove the bulb actually changed.

---

## Self-Review Notes

- Spec coverage: all behavior changes (flash worker, aqua color, docs, tests, manual verification) have a task.
- Naming consistency: `FLASH_HALF_PERIOD`, `_stop_event`, `_worker`, `_stop_flash()`, `_flash_loop()` match across the implementation and the tests.
- No placeholders.
- Task 1 deliberately defers the daemon implementation to Task 2 so the failing tests have a clean "see them fail" step on the spec-mandated TDD path.
