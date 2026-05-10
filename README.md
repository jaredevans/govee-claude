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

### LAN mode (future)

LAN discovery is **disabled by default** because the H6004 doesn't expose Govee's LAN API. When you swap in a LAN-capable bulb (e.g. H6006), enable LAN at setup time:

```bash
GOVEE_ENABLE_LAN=1 GOVEE_API_KEY_PATH="$PWD/govee-api-key.txt" \
  GOVEE_SKU=H6006 uv run python scripts/setup.py
```

Setup will run UDP discovery on your primary `/24` and fall back to cloud if no response.

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
