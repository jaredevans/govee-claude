# Thinking vs Waiting-on-You (Red/Purple Split) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Claude Code's two `Notification` sub-cases — "permission needed" (urgent, mid-turn) and "idle waiting for input" (~60 s after `Stop`) — to two different bulb colors (red and purple) instead of a single red.

**Architecture:** A new `notify` command in `send.py` reads the hook's stdin JSON, classifies the `message` text by keyword (substring match), and dispatches `red` or `purple` to the daemon via the existing UNIX-socket protocol. The daemon gains `purple` as a peer solid-color mode alongside `red`/`yellow`/`white`. Defaults are written by `setup.py`; the daemon also hard-codes `0x8000FF` as a fallback so pre-existing configs keep working.

**Tech stack:** Python 3.11+ stdlib (`json`, `select`, `os`, `socket`), pytest, `uv` for the runner.

**Spec:** `docs/superpowers/specs/2026-05-11-thinking-vs-waiting-color-design.md`.

---

## File map

| Path | Change |
|------|--------|
| `scripts/send.py` | Add `classify_notification`, `_read_stdin_capped`, `notify` command branch, extend `VALID` |
| `scripts/daemon.py` | Add `"purple"` to `VALID_MODES`; extend solid-color tuple; default `colors["purple"] = 0x8000FF` |
| `scripts/setup.py` | Write `"purple": "#8000FF"` into fresh configs |
| `config/config.example.json` | Add `"purple": "#8000FF"` to colors |
| `hooks/hooks.json` | Change `Notification` command from `red` to `notify` |
| `tests/test_daemon.py` | Add purple to test COLORS, add solid-purple and flash→purple tests |
| `tests/test_daemon_main.py` | Add upgrade-path replay test (config without `purple` key) |
| `tests/test_send.py` | Add `classify_notification` unit tests + `notify` integration tests |
| `tests/test_setup.py` | Assert `purple` in written configs |
| `tests/test_integration.py` | Add end-to-end notify→purple/red test; extend `_write_config` |
| `README.md` | Update state bullets + upgrade hint |
| `docs/manual-test.md` | New step: idle 60 s → purple |
| `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md` | Back-reference new spec |

---

## Task 1: Daemon accepts `purple` as a solid-color mode

**Files:**
- Modify: `scripts/daemon.py` (lines 27, 41, and `main()` around 225–228)
- Modify: `tests/test_daemon.py` (lines 10–16 COLORS dict; add two new tests)
- Modify: `tests/test_daemon_main.py` (add one integration test)

- [ ] **Step 1: Add purple to the test COLORS dict**

In `tests/test_daemon.py` at line 10–16, replace:

```python
COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}
```

with:

```python
COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
    "purple": 0x8000FF,
}
```

- [ ] **Step 2: Add failing solid-purple test**

Append to `tests/test_daemon.py` immediately after `test_white_sets_solid_white`:

```python
def test_purple_sets_solid_purple():
    d = make_daemon()
    d.handle("purple")
    assert d.mode == "purple"
    assert d.client.calls == [COLORS["purple"]]
```

- [ ] **Step 3: Add failing flash → purple transition test**

Append to `tests/test_daemon.py` after `test_flash_then_white_stops_alternation_cleanly`:

```python
def test_flash_then_purple_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("purple")
    final = d.client.snapshot()
    assert final[-1] == COLORS["purple"]
    time.sleep(0.05)
    assert d.client.snapshot() == final
    assert d.mode == "purple"
```

- [ ] **Step 4: Run the new tests — expect failures**

Run: `uv run pytest tests/test_daemon.py::test_purple_sets_solid_purple tests/test_daemon.py::test_flash_then_purple_stops_alternation_cleanly -v`

Expected: FAIL. `Daemon.handle("purple")` returns `"err: unknown command 'purple'"` and `set_rgb` is never called, so the assertion on `calls` fails.

- [ ] **Step 5: Add `"purple"` to daemon VALID_MODES**

In `scripts/daemon.py` line 27, replace:

```python
VALID_MODES = {"idle", "flash", "yellow", "red", "white"}
```

with:

```python
VALID_MODES = {"idle", "flash", "yellow", "red", "white", "purple"}
```

- [ ] **Step 6: Extend the solid-color branch in `Daemon.handle`**

In `scripts/daemon.py` line 41, replace:

```python
        if cmd in ("yellow", "red", "white"):
```

with:

```python
        if cmd in ("yellow", "red", "white", "purple"):
```

- [ ] **Step 7: Rerun the daemon tests — expect pass**

Run: `uv run pytest tests/test_daemon.py -v`

Expected: PASS for all tests, including the two new ones.

- [ ] **Step 8: Default `colors["purple"]` to `0x8000FF` in `main()`**

This covers the upgrade path: a user with an existing config (no `purple` key) doesn't need to re-run setup before purple works.

In `scripts/daemon.py` around lines 225–228, replace:

```python
    client = _build_client(config)
    daemon = Daemon(
        client=client,
        colors={k: int(v.lstrip("#"), 16) for k, v in config["colors"].items()},
    )
```

with:

```python
    client = _build_client(config)
    colors = {k: int(v.lstrip("#"), 16) for k, v in config["colors"].items()}
    colors.setdefault("purple", 0x8000FF)
    daemon = Daemon(client=client, colors=colors)
```

- [ ] **Step 9: Add a failing upgrade-path integration test**

Append to `tests/test_daemon_main.py` after `test_daemon_picks_up_last_command_on_start`:

```python
@pytest.mark.integration
def test_daemon_replays_purple_with_default_color(tmp_path):
    """Config without colors.purple should still produce a purple set_rgb when
    last_command='purple' (upgrade path: hook ran new code before config refresh)."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    write_config(runtime)  # writes config without "purple" key
    (runtime / "last_command").write_text("purple")
    recording = tmp_path / "recording.jsonl"

    p = start_daemon(runtime, recording)
    try:
        send(runtime / "daemon.sock", "yellow")  # waits until socket is up
        time.sleep(0.05)
        lines = recording.read_text().splitlines()
        purple_seen = any("8388863" in line for line in lines)
        assert purple_seen
        assert not (runtime / "last_command").exists()
    finally:
        send(runtime / "daemon.sock", "quit")
        p.wait(timeout=3)
```

`8388863` is `0x8000FF` in decimal — the form the `_RecordingClient` writes to JSONL.

- [ ] **Step 10: Run the integration test — expect pass**

Run: `uv run pytest -m integration tests/test_daemon_main.py -v`

Expected: PASS (all four integration tests).

- [ ] **Step 11: Commit**

```bash
git add scripts/daemon.py tests/test_daemon.py tests/test_daemon_main.py
git commit -m "$(cat <<'EOF'
feat(daemon): accept 'purple' solid mode with safe default

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `classify_notification` keyword classifier

**Files:**
- Modify: `scripts/send.py` (add `import json`, add `classify_notification` function)
- Modify: `tests/test_send.py` (add pure-function tests at top)

- [ ] **Step 1: Add the failing classifier tests**

Append to `tests/test_send.py` immediately after the existing imports block (i.e. between line 12 `SEND_PATH = ...` and line 15 `def fake_daemon(...)`):

```python
import send  # noqa: E402 — scripts/ is on sys.path via conftest.py


def test_classify_returns_purple_for_waiting_message():
    data = b'{"message": "Claude is waiting for your input"}'
    assert send.classify_notification(data) == "purple"


def test_classify_returns_red_for_permission_message():
    data = b'{"message": "Claude needs your permission to use Bash"}'
    assert send.classify_notification(data) == "red"


def test_classify_returns_red_for_empty_bytes():
    assert send.classify_notification(b"") == "red"


def test_classify_returns_red_for_malformed_json():
    assert send.classify_notification(b"not json") == "red"


def test_classify_returns_red_when_message_field_missing():
    assert send.classify_notification(b'{"other": "field"}') == "red"


def test_classify_returns_red_for_unknown_wording():
    data = b'{"message": "something else entirely"}'
    assert send.classify_notification(data) == "red"


def test_classify_is_case_insensitive_on_waiting():
    data = b'{"message": "Claude IS WAITING FOR YOUR INPUT"}'
    assert send.classify_notification(data) == "purple"


def test_classify_handles_non_dict_json():
    assert send.classify_notification(b'["not", "a", "dict"]') == "red"
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_send.py -k classify -v`

Expected: FAIL — `AttributeError: module 'send' has no attribute 'classify_notification'`.

- [ ] **Step 3: Implement `classify_notification`**

In `scripts/send.py`, add `import json` to the imports block (line 9–15 area), and add this function definition between the imports and `VALID = {...}`:

```python
import json


def classify_notification(stdin_bytes: bytes) -> str:
    """Return 'red' or 'purple' based on the Notification hook's message text.

    Falls back to 'red' on any parsing error so we never regress the prior
    single-color Notification behavior.
    """
    try:
        data = json.loads(stdin_bytes)
    except (ValueError, TypeError):
        return "red"
    if not isinstance(data, dict):
        return "red"
    msg = (data.get("message") or "").lower()
    if "waiting for your input" in msg:
        return "purple"
    if "permission" in msg:
        return "red"
    return "red"
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_send.py -k classify -v`

Expected: PASS (all eight classify_* tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/send.py tests/test_send.py
git commit -m "$(cat <<'EOF'
feat(send): add classify_notification keyword classifier

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `notify` command reads stdin and dispatches

**Files:**
- Modify: `scripts/send.py` (extend `VALID`, add `_read_stdin_capped`, add `notify` branch in `main`)
- Modify: `tests/test_send.py` (add three integration tests)

- [ ] **Step 1: Add three failing integration tests**

Append to `tests/test_send.py` after `test_send_yellow_reaches_daemon`:

```python
@pytest.mark.integration
def test_send_notify_dispatches_purple_for_waiting(tmp_path):
    log: list[str] = []
    fake_daemon(tmp_path / "daemon.sock", log)
    time.sleep(0.05)
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "notify"],
        env=env,
        input=b'{"message": "Claude is waiting for your input"}',
        capture_output=True,
        timeout=5,
    )
    assert r.returncode == 0
    assert log == ["purple"]


@pytest.mark.integration
def test_send_notify_dispatches_red_for_permission(tmp_path):
    log: list[str] = []
    fake_daemon(tmp_path / "daemon.sock", log)
    time.sleep(0.05)
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "notify"],
        env=env,
        input=b'{"message": "Claude needs your permission to use Bash"}',
        capture_output=True,
        timeout=5,
    )
    assert r.returncode == 0
    assert log == ["red"]


@pytest.mark.integration
def test_send_notify_with_no_stdin_falls_back_to_red(tmp_path):
    log: list[str] = []
    fake_daemon(tmp_path / "daemon.sock", log)
    time.sleep(0.05)
    env = os.environ.copy()
    env["GOVEE_CLAUDE_RUNTIME_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(SEND_PATH), "notify"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
    )
    assert r.returncode == 0
    assert log == ["red"]
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest -m integration tests/test_send.py -k notify -v`

Expected: FAIL. `send.py` currently logs `bad invocation` for the `notify` argument and exits 0 without contacting the socket, so `log` stays `[]`.

- [ ] **Step 3: Add `notify` to `VALID` and add the stdin reader**

In `scripts/send.py` line 17, replace:

```python
VALID = {"ensure-running", "flash", "yellow", "red", "white", "quit"}
```

with:

```python
VALID = {"ensure-running", "flash", "yellow", "red", "purple", "white", "quit", "notify"}
```

(`purple` is added here too so manual `send.py purple` works for testing.)

Add `import select` to the imports block.

Add this helper function near `classify_notification` (it depends on `os` and `sys`, both already imported):

```python
def _read_stdin_capped(timeout: float = 1.0, max_bytes: int = 8192) -> bytes:
    """Read up to max_bytes from stdin, waiting at most timeout seconds.

    Returns b'' when stdin is not readable within the timeout, or when the
    read raises OSError. Never raises."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return b""
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return b""
    try:
        return os.read(fd, max_bytes)
    except OSError:
        return b""
```

- [ ] **Step 4: Branch on `notify` in `main()`**

In `scripts/send.py` `main()`, immediately after the line `cmd = argv[1]` (currently line 77) and before `sock = rt / "daemon.sock"`, insert:

```python
    if cmd == "notify":
        cmd = classify_notification(_read_stdin_capped())
```

After this branch, `cmd` is guaranteed to be `"red"` or `"purple"`, and the existing `try_send` / `last_command`-buffer path applies unchanged.

- [ ] **Step 5: Run notify integration tests — expect pass**

Run: `uv run pytest -m integration tests/test_send.py -k notify -v`

Expected: PASS (all three).

- [ ] **Step 6: Confirm no regressions in the rest of `test_send.py`**

Run: `uv run pytest tests/test_send.py -v` and `uv run pytest -m integration tests/test_send.py -v`

Expected: PASS for both runs (unit + integration).

- [ ] **Step 7: Commit**

```bash
git add scripts/send.py tests/test_send.py
git commit -m "$(cat <<'EOF'
feat(send): 'notify' command reads stdin and dispatches red/purple

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Setup writes `colors.purple` on fresh configs

**Files:**
- Modify: `scripts/setup.py:120-135` (`write_config`)
- Modify: `tests/test_setup.py` (extend two existing tests)
- Modify: `config/config.example.json`

- [ ] **Step 1: Add failing assertions to existing tests**

In `tests/test_setup.py`, append one line to `test_write_config_lan` (after line 71):

```python
    assert cfg["colors"]["purple"] == "#8000FF"
```

And the same line to `test_write_config_cloud` (after line 82):

```python
    assert cfg["colors"]["purple"] == "#8000FF"
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_setup.py -v`

Expected: FAIL — `KeyError: 'purple'` on both `test_write_config_*` tests.

- [ ] **Step 3: Add purple to the setup colors dict**

In `scripts/setup.py` lines 128–134, replace:

```python
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "aqua":   "#00FFFF",
            "white":  "#FFFFFF",
        },
```

with:

```python
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "aqua":   "#00FFFF",
            "white":  "#FFFFFF",
            "purple": "#8000FF",
        },
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_setup.py -v`

Expected: PASS (all six tests).

- [ ] **Step 5: Update `config/config.example.json` to match**

Replace the entire contents of `config/config.example.json` with:

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
    "white":  "#FFFFFF",
    "purple": "#8000FF"
  }
}
```

- [ ] **Step 6: Commit**

```bash
git add scripts/setup.py tests/test_setup.py config/config.example.json
git commit -m "$(cat <<'EOF'
feat(setup): write colors.purple on fresh configs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire the Notification hook to `notify`

**Files:**
- Modify: `hooks/hooks.json` (the Notification entry's command)

- [ ] **Step 1: Change the Notification command from `red` to `notify`**

In `hooks/hooks.json` around lines 47–57, replace the Notification entry:

```json
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
```

with:

```json
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/send.py\" notify",
            "timeout": 2
          }
        ]
      }
    ],
```

- [ ] **Step 2: Confirm the full suite is green**

Run: `uv run pytest`

Expected: PASS (all non-integration).

Run: `uv run pytest -m integration`

Expected: PASS (all integration).

- [ ] **Step 3: Commit**

```bash
git add hooks/hooks.json
git commit -m "$(cat <<'EOF'
feat(hooks): Notification → 'notify' (red for permission, purple for idle)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: End-to-end integration smoke

**Files:**
- Modify: `tests/test_integration.py` (extend `_write_config`, add new test)

- [ ] **Step 1: Extend `_write_config` to include purple**

This exercises `colors.purple` directly (rather than only the daemon-side default).

In `tests/test_integration.py` lines 23–26, replace:

```python
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "aqua": "#00FFFF", "white": "#FFFFFF",
        },
```

with:

```python
        "colors": {
            "yellow": "#FFFF00", "red": "#FF0000",
            "blue": "#0000FF", "aqua": "#00FFFF", "white": "#FFFFFF",
            "purple": "#8000FF",
        },
```

- [ ] **Step 2: Add an end-to-end notify test**

Append to `tests/test_integration.py`:

```python
@pytest.mark.integration
def test_notify_drives_purple_for_waiting_and_red_for_permission(tmp_path):
    """Full path: real daemon subprocess + real send.py 'notify' + stdin JSON,
    asserting both classifier branches land at the recording bulb."""
    rt = tmp_path / "rt"
    rt.mkdir()
    _write_config(rt)
    rec = tmp_path / "rec.jsonl"

    env = {**os.environ, "GOVEE_CLAUDE_RUNTIME_DIR": str(rt),
           "GOVEE_CLAUDE_FAKE_BULB": str(rec)}

    p = subprocess.Popen([sys.executable, str(DAEMON_PATH)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not (rt / "daemon.sock").exists():
            time.sleep(0.02)

        def notify(payload: bytes):
            r = subprocess.run(
                [sys.executable, str(SEND_PATH), "notify"],
                env=env,
                input=payload,
                capture_output=True,
                timeout=3,
            )
            assert r.returncode == 0

        notify(b'{"message": "Claude is waiting for your input"}')
        time.sleep(0.1)
        notify(b'{"message": "Claude needs your permission to use Bash"}')
        time.sleep(0.1)

        lines = [json.loads(l) for l in rec.read_text().splitlines()]
        rgbs = [e["rgb"] for e in lines]
        assert 0x8000FF in rgbs, f"expected purple (8388863) in {rgbs!r}"
        assert 0xFF0000 in rgbs, f"expected red (16711680) in {rgbs!r}"
        # Order: purple (waiting) before red (permission).
        purple_idx = rgbs.index(0x8000FF)
        red_idx = rgbs.index(0xFF0000, purple_idx + 1)
        assert purple_idx < red_idx
    finally:
        subprocess.run([sys.executable, str(SEND_PATH), "quit"], env=env, timeout=3)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=2)
```

- [ ] **Step 3: Run integration tests**

Run: `uv run pytest -m integration tests/test_integration.py -v`

Expected: PASS for both `test_full_session_flow` and the new `test_notify_drives_purple_for_waiting_and_red_for_permission`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "$(cat <<'EOF'
test(integration): notify dispatches purple for waiting, red for permission

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `docs/manual-test.md`
- Modify: `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`

- [ ] **Step 1: Update README state bullets**

In `README.md` lines 5–8, replace:

```markdown
- **Working** (between `UserPromptSubmit`/`PostToolUse` and `Stop`): blue for 2 s, then aqua for 0.5 s, repeating
- **Idle / done** (`Stop`): solid yellow
- **Needs attention** (`Notification`): solid red — clears back to flash on the next `PostToolUse`, so approving a permission prompt visibly resumes the working state
- **Session ended** (`SessionEnd`): solid white
```

with:

```markdown
- **Working** (between `UserPromptSubmit`/`PostToolUse` and `Stop`): blue for 2 s, then aqua for 0.5 s, repeating
- **Done** (`Stop`): solid yellow
- **Permission prompt** (`Notification`, "Claude needs your permission to use X"): solid red — clears back to flash on the next `PostToolUse`, so approving visibly resumes the working state
- **Idle waiting on you** (`Notification`, "Claude is waiting for your input", fires ~60 s after `Stop`): solid purple — clears to flash on the next `UserPromptSubmit`
- **Session ended** (`SessionEnd`): solid white
```

- [ ] **Step 2: Add an upgrade hint to README**

In `README.md`, insert this section between the install block (ending on the `/plugin install govee-claude` line) and `### LAN mode (future)`:

```markdown
### Upgrading from a pre-purple install

If `colors.purple` is missing from your `~/.claude/govee-claude/config.json` (you installed before the red/purple split), the daemon falls back to `#8000FF`. Re-run setup once to make the value explicit and override-able:

```bash
GOVEE_API_KEY_PATH="$PWD/govee-api-key.txt" uv run python scripts/setup.py
```
```

- [ ] **Step 3: Update manual-test.md**

In `docs/manual-test.md`, replace the note at the top (lines 5):

```markdown
> **Note:** If you installed before the blue/aqua flash change, re-run step 1 once — your existing `~/.claude/govee-claude/config.json` is missing the `aqua` color.
```

with:

```markdown
> **Note:** If you installed before the purple split (or before the blue/aqua flash change), re-run step 1 once — your existing `~/.claude/govee-claude/config.json` may be missing `purple` or `aqua`. The daemon falls back to `#8000FF` for purple in the meantime.
```

Then replace steps 5–7 (lines 22–26):

```markdown
5. **Trigger a permission prompt** (e.g., a Bash command Claude needs to ask about). Bulb goes solid red.

6. **End the session** (`/exit`). Bulb goes solid white.

7. **Inspect logs:** `tail -f ~/.claude/govee-claude/daemon.log` should show one `set_rgb` log per transition.
```

with:

```markdown
5. **Trigger a permission prompt** (e.g., a Bash command Claude needs to ask about). Bulb goes solid red. Approve it — bulb returns to flash (blue/aqua) while the tool runs.

6. **After Claude finishes, wait ~60 s without typing.** Bulb goes solid purple ("Claude is waiting for your input"). Type any message — bulb returns to flash.

7. **End the session** (`/exit`). Bulb goes solid white.

8. **Inspect logs:** `tail -f ~/.claude/govee-claude/daemon.log` should show one `set_rgb` log per transition.
```

- [ ] **Step 4: Add a back-reference to the original spec**

In `docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md`, find the `## State machine` heading (around line 86) and immediately under the section's first paragraph and table, append this note (before the `Notification mid-turn overwrites the flash; red persists ...` line):

```markdown
> **Update (2026-05-11):** The single `Notification` → red mapping has been split into red (permission needed) and purple (idle waiting). See `2026-05-11-thinking-vs-waiting-color-design.md` for the classifier and `notify` command.
```

- [ ] **Step 5: Run the full suite one last time**

Run: `uv run pytest && uv run pytest -m integration`

Expected: PASS for both invocations.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/manual-test.md docs/superpowers/specs/2026-05-09-govee-claude-plugin-design.md
git commit -m "$(cat <<'EOF'
docs: red/purple split for permission vs idle waiting

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done

Seven commits, all green. The bulb now distinguishes "Claude needs permission" (red) from "Claude is idle waiting for you" (purple), with the working flash and end-of-turn yellow unchanged.
