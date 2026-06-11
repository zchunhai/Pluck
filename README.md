# Pluck - Selective Claude Plugin Installer

A CLI tool to selectively install components (skills, agents, commands, rules, hooks) from Claude Code plugins hosted on GitHub.

## Features

- **Selective Installation**: Pick exactly which components you want from large plugins
- **Environment Management**: Create isolated Claude Code environments for different projects
- **Shared Caching**: Plugin repos are cloned once and shared across environments
- **Interactive Selection**: TUI-based component picker with tab navigation

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
# 1. Install
pip install .

# 2. (Optional) Set up shell wrapper for seamless env switching
pluck env init >> ~/.zshrc
source ~/.zshrc

# 3. Create your first environment
pluck env create myproject

# 4. Add plugins and install
pluck install --repo https://github.com/affaan-m/ECC.git -p ecc

# 5. Verify
pluck status
```

## Commands

### Environment Management

Isolate your Claude Code configuration by project or use case:

```bash
pluck env create myproject              # Create new environment (auto-activates)
pluck env create work --path ~/work    # Create at custom path
pluck env list                         # List all environments
pluck env current                       # Show active environment
pluck env switch coding                # Switch to environment (manual eval only)
pluck env deactivate                    # Deactivate (manual eval only)
pluck env delete myproject             # Delete environment
pluck env init                          # Generate shell wrapper (one-time setup)
```

**Setup (one-time)**: Add the shell wrapper to your `~/.zshrc` or `~/.bashrc`:

```bash
pluck env init >> ~/.zshrc
source ~/.zshrc
```

⚠️ **Important**: Use `>>` (append) not `>` (overwrite) to preserve your existing config. After adding the wrapper, restart your shell or run `source ~/.zshrc` to activate it.

Once activated, `pluck env create`, `pluck env switch`, and `pluck env deactivate` will automatically modify your shell's environment — no manual `eval` needed.

Each environment is a self-contained Claude config directory with its own:
- `pluck.yaml` — plugin selections
- `plugins/` — installed components
- `settings.json` — Claude settings
- `memory/` — session memory

### Plugin Management

#### `install`
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
| `CLAUDE_CONFIG_DIR` | `~/.claude/` | Claude configuration directory (set automatically when switching environments) |
| `XDG_CONFIG_HOME` | `~/.config/` | Config directory for environment registry (`~/.config/pluck/environments.json`) |
| `XDG_CACHE_HOME` | `~/.cache/` | Cache directory for shared plugin repos (`~/.cache/pluck/repos/`) |

## Project Structure

```
pluck/
├── src/pluck/
│   ├── __init__.py       # Package version
│   ├── __main__.py       # python -m pluck support
│   ├── cli.py            # CLI entry point (argparse)
│   ├── config.py         # Config parsing + path management
│   ├── env.py            # Environment management (NEW)
│   ├── installer.py      # Plugin creation + registration
│   ├── interactive.py    # Config saving
│   ├── repo.py           # Git clone/update + component discovery
│   └── tab_ui.py         # Interactive TUI for component selection
├── tests/
│   ├── test_env.py       # Environment management tests
│   └── ...
├── pyproject.toml        # Package config + tool settings
├── pluck.yaml.example    # Example configuration
└── README.md
```

## Installation Locations

Pluck stores data in the following locations:

```
~/.config/pluck/
└── environments.json               # Environment registry

~/.cache/pluck/
└── repos/                        # Shared plugin repo cache

~/.claude-envs/                     # Default environment home
└── <name>/                        # Per-environment directories
    ├── pluck.yaml                 # Plugin selections for this env
    ├── plugins/
    │   ├── pluck/
    │   │   └── <name>/            # Installed plugins (env-specific)
    │   └── installed_plugins.json # Plugin registry (env-specific)
    ├── settings.json              # Claude settings (env-specific)
    ├── CLAUDE.md                  # Global instructions (env-specific)
    └── memory/                    # Session memory (env-specific)
```

**Key design**: Plugin repos are cached in `~/.cache/pluck/repos/` and shared across all environments, avoiding redundant downloads. Only the filtered installations are environment-specific.

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
