# Pluck - Selective Claude Plugin Installer

## Architecture

`src/pluck/` package with these modules:

| Module | Responsibility |
|--------|---------------|
| `cli.py` | argparse CLI entry point, 6 subcommands |
| `config.py` | YAML config parsing, path management, `MARKETPLACE_NAME` / `COMPONENT_TYPES` constants |
| `repo.py` | Git clone/update, component discovery, path resolution |
| `installer.py` | Plugin dir creation, file copy, registry writes (`installed_plugins.json` + `settings.json`) |
| `interactive.py` | Terminal UI for component selection, config saving |

## Key Design Decisions

- **Atomic JSON writes**: `_atomic_write_json()` uses temp file + `os.replace` to prevent corruption
- **JSON backup recovery**: `_safe_load_json()` falls back to `.json.bak` on corruption
- **Partial clone**: `--filter=blob:none --depth=1` for large repos
- **Plugin key format**: `<name>@pluck` in registry, separate from marketplace installs

## Commands

```
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
