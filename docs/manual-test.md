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
