# Pluck - Selective Claude Plugin Installer

## Architecture

`src/pluck/` package with these modules:

| Module | Responsibility |
|--------|---------------|
| `cli.py` | argparse CLI entry point, 7 subcommands (env + 6 plugin commands) |
| `config.py` | YAML config parsing, path management, `MARKETPLACE_NAME` / `COMPONENT_TYPES` constants |
| `env.py` | Environment management (create, switch, delete, list), shell wrapper generation |
| `repo.py` | Git clone/update, component discovery, path resolution |
| `installer.py` | Plugin dir creation, file copy, registry writes (`installed_plugins.json` + `settings.json`) |
| `interactive.py` | Config saving (atomic YAML writes) |
| `tab_ui.py` | Terminal UI for component selection, tab navigation |

## Key Design Decisions

- **Atomic JSON writes**: `_atomic_write_json()` uses temp file + `os.replace` to prevent corruption
- **JSON backup recovery**: `_safe_load_json()` falls back to `.json.bak` on corruption
- **Partial clone**: `--filter=blob:none --depth=1` for large repos
- **Plugin key format**: `<name>@pluck` in registry, separate from marketplace installs
- **Shared repo cache**: `get_repos_dir()` uses `~/.cache/pluck/repos/` (XDG cache) to avoid redundant downloads across environments
- **Environment isolation**: Each environment has its own Claude config dir; switching sets `CLAUDE_CONFIG_DIR` env var
- **Shell wrapper**: `pluck env init` generates a shell function that auto-evals create/switch/deactivate output

## Commands

```
pluck env create <name> [--path <dir>]    # Create isolated environment
pluck env list                             # List all environments
pluck env switch <name>                    # Activate environment
pluck env deactivate                       # Deactivate
pluck env current                          # Show active environment
pluck env delete <name>                    # Delete environment
pluck env init [--shell zsh|bash]          # Generate shell wrapper
pluck install [-p NAME] [--repo URL] [--all] [--dry-run]
pluck update [-p NAME]
pluck uninstall [NAME]
pluck select [-p NAME] [--install]
pluck list [-p NAME] [-t TYPE]
pluck status
```

## Testing

```bash
CLAUDE_CONFIG_DIR=$(mktemp -d) pluck <command> -c pluck.yaml
```

## Environment Setup

```bash
# One-time shell wrapper setup (MUST use >> append, not > overwrite)
pluck env init >> ~/.zshrc
source ~/.zshrc

# After setup, environments auto-activate on create/switch
pluck env create myproject
```
