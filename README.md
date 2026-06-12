# Pluck - Selective Claude Plugin Installer

A CLI tool to selectively install components from Claude Code plugins hosted on GitHub.

Claude plugins like [ECC](https://github.com/affaan-m/ECC) contain 100+ components — skills, agents, commands, rules, hooks, contexts. You may only need a few. Pluck lets you pick exactly what you want.

## Requirements

- Python 3.10+
- Git

## Installation

```bash
pip install .
```

## Quick Start

```bash
# 1. (Optional) Set up shell wrapper for seamless env switching
pluck env init >> ~/.zshrc
source ~/.zshrc

# 2. Add a plugin and install
pluck install --repo https://github.com/affaan-m/ECC.git -p ecc

# 3. Verify
pluck status
```

## Commands

### Environments

Isolate your Claude Code configuration by project. Each environment has its own plugins, settings, and memory.

```bash
pluck env create myproject              # Create (auto-activates with shell wrapper)
pluck env create work --path ~/work     # Create at custom path
pluck env list                          # List all environments
pluck env switch                        # TUI selector (arrow keys)
pluck env switch coding                 # Switch by name
pluck env switch default                # Switch back to default (~/.claude)
pluck env delete myproject              # Delete environment
pluck env init                          # Generate shell wrapper (one-time setup)
```

**One-time setup** — add the shell wrapper to your shell config:

```bash
pluck env init >> ~/.zshrc   # or ~/.bashrc
source ~/.zshrc
```

> ⚠️ Use `>>` (append), not `>` (overwrite).

With the wrapper active, `pluck env create` and `pluck env switch` automatically set the environment — no manual `eval` needed.

### Model Providers

Switch between AI providers (Anthropic, Zhipu, DeepSeek, etc.) globally or per-environment. Provider configuration is stored in `~/.config/pluck/providers.yaml`.

```bash
pluck model list                       # List configured providers (* = current)
pluck model current                    # Show current model configuration
pluck model switch                     # TUI selector (arrow keys)
pluck model switch zhipu               # Switch by name
pluck model switch deepseek --tier sonnet
pluck model reset                      # Reset to Anthropic

pluck model add                        # Interactive wizard
pluck model add my-api \               # Or CLI flags for scripting
  --base-url "https://my-api.com" \
  --sonnet-model "my-sonnet-v1"

pluck model remove my-api              # Remove provider
```

### Plugins

```bash
pluck install                    # Interactive selection + install all
pluck install -y                 # Non-interactive: use config as-is
pluck install -p ecc             # Interactive selection for one plugin
pluck install --dry-run          # Preview without installing
pluck install --repo <url>       # Add plugin from URL, then install
pluck install --repo <url> --all # Add + install all components

pluck update                     # Update repos and reinstall all
pluck update -p ecc              # Update one plugin

pluck uninstall ecc              # Interactive: select components to remove
pluck uninstall ecc --all        # Remove entire plugin
pluck uninstall ecc --all -y     # Remove entire plugin (no prompt)
pluck uninstall ecc -t skills -n react-patterns  # Remove specific component
pluck uninstall ecc -t hooks     # Remove all hooks

pluck list                       # Show all (✓ installed, ⚠ configured, · available)
pluck list -p ecc                # Show one plugin
pluck list -t skills             # Filter by component type

pluck status                     # Show active environment and installed plugins
```

## Configuration

Config file: `$CLAUDE_CONFIG_DIR/pluck.yaml` — see [`pluck.yaml.example`](pluck.yaml.example) for a full example.

```yaml
plugins:
  - name: ecc
    repo: https://github.com/affaan-m/ECC.git
    branch: main
    components:
      skills:
        - react-patterns
        - python-patterns
      agents:
        - code-reviewer
      hooks: true
```

### Component Types

| Type | Description |
|------|-------------|
| `skills` | Skill directories containing `SKILL.md` |
| `agents` | Agent definitions (`.md` files) |
| `commands` | Slash commands (`.md` files) |
| `rules` | Rule files or directories |
| `hooks` | Hook configurations (`hooks.json`) |
| `contexts` | Context files or directories |

### Selection Syntax

| Value | Meaning |
|-------|---------|
| Omitted or `false` | Don't install any |
| `true` or `"all"` | Install all available |
| `["name1", "name2"]` | Install only listed items |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_CONFIG_DIR` | `~/.claude/` | Claude config dir (auto-set by env switching) |
| `PLUCK_CONFIG_DIR` | `$XDG_CONFIG_HOME/pluck` | Provider registry + environment registry location |
| `XDG_CONFIG_HOME` | `~/.config/` | Fallback for `PLUCK_CONFIG_DIR` |
| `XDG_CACHE_HOME` | `~/.cache/` | Shared plugin repo cache location |

## Development

```bash
pip install -e ".[dev]"

ruff check src/      # Lint
mypy src/            # Type check
pytest               # Run tests
```

## License

MIT
