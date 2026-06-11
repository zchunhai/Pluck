# Pluck - Selective Claude Plugin Installer

A CLI tool to selectively install components (skills, agents, commands, rules, hooks) from Claude Code plugins hosted on GitHub.

## Why?

Claude plugins like [ECC](https://github.com/affaan-m/ECC) contain 100+ components. You may only need a few. Pluck lets you pick exactly what you want.

## How It Works

1. **Clone** plugin repos to your Claude config directory
2. **Filter** components based on your `pluck.yaml` config
3. **Install** only the selected components as a registered plugin
4. **Use** components with the standard `plugin:component` syntax (e.g., `/ecc:react-patterns`)

## Requirements

- Python 3.10+
- Git

## Installation

```bash
pip install .

# Or in editable mode for development
pip install -e ".[dev]"
```

## Quick Start

```bash
# 1. Create your config
cp pluck.yaml.example pluck.yaml
# Edit pluck.yaml to select your components

# 2. Install
pluck install

# 3. Verify
pluck status
```

## Commands

### `install`
Install all or specific plugins from config:
```bash
pluck install                    # Install all
pluck install -p ecc             # Install only ecc
pluck install --dry-run          # Preview without installing
pluck install --repo <url>       # Add plugin from URL and install
pluck install --repo <url> --all # Add + install all components
```

### `update`
Update repos and reinstall:
```bash
pluck update                    # Update all
pluck update -p ecc             # Update only ecc
```

### `uninstall`
Remove pluck-managed plugins:
```bash
pluck uninstall ecc             # Uninstall specific plugin
pluck uninstall                 # Uninstall all pluck plugins
```

### `select`
Interactively select components (saves to config):
```bash
pluck select                    # Select for all plugins
pluck select -p ecc             # Select for ecc only
pluck select --install          # Install after selecting
```

### `list`
List available components with three-state display:
```bash
pluck list                      # Show all (✓ installed, ⚠ configured, · available)
pluck list -p ecc               # Show only ecc components
pluck list -t skills            # Show only skills
```

### `status`
Show currently installed pluck plugins:
```bash
pluck status
```

## Configuration

Config file: `pluck.yaml` (or specify with `-c path/to/config.yaml`)

```yaml
plugins:
  - name: superpowers
    repo: https://github.com/obra/superpowers.git
    branch: main
    components:
      skills:
        - brainstorming
        - writing-plans

  - name: ecc
    repo: https://github.com/affaan-m/ECC.git
    branch: main
    components:
      skills:
        - react-patterns
        - python-patterns
      agents:
        - code-reviewer
        - architect
      commands:
        - code-review
        - build-fix
      rules:
        - common
      hooks: true
```

### Component Selection

| Value | Meaning |
|-------|---------|
| Omitted or `false` | Don't install any |
| `true` or `"all"` | Install all available |
| `["name1", "name2"]` | Install only listed items |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CONFIG_DIR` | `~/.claude/` | Claude configuration directory |

## Project Structure

```
pluck/
├── src/pluck/
│   ├── __init__.py       # Package version
│   ├── __main__.py       # python -m pluck support
│   ├── cli.py            # CLI entry point (argparse)
│   ├── config.py         # Config parsing + path management
│   ├── repo.py           # Git clone/update + component discovery
│   ├── installer.py      # Plugin creation + registration
│   └── interactive.py    # Interactive selection + config saving
├── tests/                # Unit tests
├── pyproject.toml        # Package config + tool settings
├── pluck.yaml.example    # Example configuration
└── README.md
```

## Installation Locations

Pluck stores data within your Claude config directory:

```
~/.claude/
├── plugins/
│   ├── pluck/
│   │   └── repos/           # Cloned plugin repos
│   └── cache/
│       └── pluck/           # Filtered plugin installations
│           └── <name>/selected/
├── plugins/installed_plugins.json  # Plugin registry
└── settings.json                   # Plugin enablement
```

## Development

```bash
pip install -e ".[dev]"

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/

# Test
pytest
```

## License

MIT
