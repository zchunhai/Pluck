# Pluck - Selective Claude Plugin Installer

## Architecture

`src/pluck/` package with these modules:

| Module | Responsibility |
|--------|---------------|
| `cli.py` | argparse CLI entry point, 7 subcommands (env + model + 5 plugin commands) |
| `config.py` | YAML config parsing, path management, name validation, `MARKETPLACE_NAME` / `COMPONENT_TYPES` constants |
| `env.py` | Environment management (create, switch, delete, list), shell wrapper generation |
| `io_utils.py` | Atomic file writes (`atomic_write`, `atomic_write_json`, `safe_load_json`) |
| `model.py` | Model provider management: list, current, switch (TUI or direct), add (wizard or CLI), remove, reset |
| `model_config.py` | Built-in provider reference configurations (not auto-seeded; for tests and seeding) |
| `providers.py` | Global provider registry CRUD in `~/.config/pluck/providers.yaml` (or `$PLUCK_CONFIG_DIR`) |
| `repo.py` | Git clone/update, component discovery, path resolution |
| `installer.py` | Plugin dir creation, file copy, registry writes (`installed_plugins.json` + `settings.json`) |
| `interactive.py` | Config saving (atomic YAML writes) |
| `tab_ui.py` | Terminal UI for component selection (tab navigation), single-list selector (`select_from_list`) |

## Key Design Decisions

- **Atomic file writes**: `io_utils.atomic_write_json()` uses temp file + `os.replace` to prevent corruption
- **JSON backup recovery**: `io_utils.safe_load_json()` falls back to `.json.bak` on corruption
- **Shell injection protection**: `shlex.quote()` on all paths in eval output
- **Partial clone**: `--filter=blob:none --depth=1` for large repos
- **Plugin key format**: `<name>@pluck` in registry, separate from marketplace installs
- **Shared repo cache**: `get_repos_dir()` uses `~/.cache/pluck/repos/` (XDG cache) to avoid redundant downloads across environments
- **Environment isolation**: Each environment has its own Claude config dir; switching sets `CLAUDE_CONFIG_DIR` env var
- **Shell wrapper**: `pluck env init` generates a shell function that auto-evals create/switch output (switch to "default" = deactivate)
- **Configurable pluck dir**: `PLUCK_CONFIG_DIR` env var overrides `~/.config/pluck/` (default: `$XDG_CONFIG_HOME/pluck`)
- **No auto-seeding**: Provider registry starts empty; users add providers explicitly via `pluck model add`

## Commands

```
pluck env create <name> [--path <dir>]       # Create isolated environment
pluck env list                                # List all environments (including default)
pluck env switch [name]                       # Activate environment (TUI if no name; "default" = deactivate)
pluck env delete <name>                       # Delete environment
pluck env init [--shell zsh|bash]             # Generate shell wrapper
pluck model list                              # List configured model providers
pluck model current                           # Show current model configuration
pluck model switch [provider] [--tier]        # Switch provider (TUI if no name)
pluck model reset                             # Reset to anthropic
pluck model add [name] [options]              # Add provider (interactive wizard by default; CLI flags for scripting)
pluck model remove <name>                     # Remove provider
pluck install [-p NAME] [-y] [--repo URL] [--all] [--dry-run]
pluck update [-p NAME]
pluck uninstall <NAME> [-y] [--all] [-t TYPE] [-n NAME] [--dry-run]
pluck list [-p NAME] [-t TYPE]
pluck status
```

## Testing

```bash
# Isolate tests from real config: set PLUCK_CONFIG_DIR to a temp dir
PLUCK_CONFIG_DIR=$(mktemp -d) CLAUDE_CONFIG_DIR=$(mktemp -d) pytest

# Or just run pytest (tests auto-isolate via fixtures)
pytest
```

## Environment Setup

```bash
# One-time shell wrapper setup (MUST use >> append, not > overwrite)
pluck env init >> ~/.zshrc
source ~/.zshrc

# After setup, environments auto-activate on create/switch
pluck env create myproject
```

## Model Provider Management

Providers are stored globally in `$PLUCK_CONFIG_DIR/providers.yaml` (default: `~/.config/pluck/providers.yaml`). Each environment's `settings.json` stores only the active model selection.

The registry starts **empty** — no built-in providers are auto-created. Add providers with `pluck model add`.

**Provider reference (use these IDs with `pluck model seed` or add manually):**

| Name | Display | Base URL |
|------|---------|----------|
| `anthropic` | Anthropic (Official) | `https://api.anthropic.com` |
| `zhipu` | 智谱 AI | `https://open.bigmodel.cn/api/anthropic` |
| `deepseek` | DeepSeek | `https://api.deepseek.com` |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` |

**Usage:**
```bash
# List configured providers (current marked with *)
pluck model list

# Show current model configuration
pluck model current

# Switch to a provider (TUI if no name given)
pluck model switch               # Arrow-key TUI selector
pluck model switch zhipu         # Direct switch
pluck model switch deepseek --tier sonnet

# Reset to Anthropic
pluck model reset

# Add provider (interactive wizard by default)
pluck model add                  # Wizard: name, URL, models, token, etc.
pluck model add my-api \         # CLI flags for scripting
  --display-name "My API" \
  --base-url "https://my-api.com" \
  --sonnet-model "my-sonnet-v1" \
  --haiku-model "my-haiku-v1" \
  --opus-model "my-opus-v1"

# Remove provider
pluck model remove my-api
```

### What `switch` writes to `settings.json`

```json
{
  "model": "sonnet",
  "env": {
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "ANTHROPIC_MODEL": "glm-5-turbo",
    "ANTHROPIC_AUTH_TOKEN": "sk-xxx...",
    "_pluck_provider": "zhipu",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5-turbo",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "glm-5-turbo",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "glm-5.1",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "glm-4.7"
  }
}
```

Restart Claude Code after switching.

