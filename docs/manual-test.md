# Manual end-to-end test

After installing the plugin in Claude Code:

> **Note:** If you installed before the purple split (or before the blue/aqua flash change), re-run step 1 once — your existing `~/.claude/govee-claude/config.json` may be missing `purple` or `aqua`. The daemon falls back to `#8000FF` for purple in the meantime.

1. **Run setup**

   ```bash
   GOVEE_API_KEY_PATH=/Users/jared/github_projects/govee-claude/govee-api-key.txt \
     uv run python scripts/setup.py
   ```

   Expected: prints `LAN discovery skipped (set GOVEE_ENABLE_LAN=1 to opt in)` followed by `mode=cloud`. Writes `~/.claude/govee-claude/config.json`. LAN is disabled by default since the H6004 doesn't support it; prepend `GOVEE_ENABLE_LAN=1` once you've moved to a LAN-capable bulb.

2. **Start a Claude Code session.** Bulb should not change yet (`SessionStart` only ensures the daemon is running).

3. **Submit a prompt.** Bulb shows blue for 2 s, then aqua for 0.5 s, repeating while Claude is working.

4. **Wait for Claude to finish.** Bulb goes solid yellow.

5. **Trigger a permission prompt** (e.g., a Bash command Claude needs to ask about). Bulb goes solid red. Approve it — bulb returns to flash (blue/aqua) while the tool runs.

6. **After Claude finishes, wait ~60 s without typing.** Bulb goes solid purple ("Claude is waiting for your input"). Type any message — bulb returns to flash.

7. **End the session** (`/exit`). Bulb goes solid white.

8. **Inspect logs:** `tail -f ~/.claude/govee-claude/daemon.log` should show one `set_rgb` log per transition.

If anything misbehaves, check `~/.claude/govee-claude/daemon.log` and `~/.claude/govee-claude/hook.log`.
