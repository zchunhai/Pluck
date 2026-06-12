# Model Management — Current Design

## Architecture

```
$PLUCK_CONFIG_DIR/                    # default: ~/.config/pluck/
├── providers.yaml                    # Global provider registry
└── environments.json                 # Environment registry (separate feature)

$CLAUDE_CONFIG_DIR/                   # default: ~/.claude/
└── settings.json                     # Per-environment model selection (env block)
```

- `providers.py` — data layer: CRUD for providers.yaml
- `model.py` — application layer: bridges providers → settings.json, list/display
- `model_config.py` — built-in provider reference data (for tests + seeding)
- `cli.py` — single `pluck model` subcommand, interactive wizard + CLI flags

## Data Model

### ModelTier

```python
@dataclass(frozen=True)
class ModelTier:
    id: str
```

### ProviderConfig

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str                          # Identifier (validated: alphanumeric + -._)
    display_name: str                  # Human-readable
    base_url: str
    models: dict[str, ModelTier]       # opus, sonnet, haiku → id
    default_tier: str = "sonnet"
    auth_token: str | None = None
```

### providers.yaml

```yaml
version: 1
active_provider: ''
providers:
  zhipu:
    display_name: Zhipu GLM
    base_url: https://open.bigmodel.cn/api/anthropic
    default_tier: sonnet
    auth_token: sk-xxx...
    models:
      opus:
        id: glm-5.1
      sonnet:
        id: glm-4.7
      haiku:
        id: glm-4.6
```

## Design Decisions

1. **Registry starts empty** — no auto-creation of built-in providers. First `pluck model list` shows nothing.
2. **Provider names restricted** — `[a-zA-Z0-9][-._a-zA-Z0-9]*`, no spaces (validated by `config.validate_plugin_name`).
3. **All 3 tiers required** — the interactive wizard requires opus, sonnet, and haiku model IDs.
4. **auth_token in registry** — stored per-provider in providers.yaml; written to settings.json on switch.
5. **`_MODEL_NAME` = model ID** — same value written to both `ANTHROPIC_DEFAULT_*_MODEL` and `ANTHROPIC_DEFAULT_*_MODEL_NAME`.
6. **`_pluck_provider` marker** — provider name stored in settings.json for fast detection, with URL fallback for legacy configs.
7. **Atomic writes** — providers.yaml saved via `io_utils.atomic_write`.
8. **PLUCK_CONFIG_DIR** — env var overrides `~/.config/pluck/` for test isolation.

## Commands

```
pluck model list       → list providers from registry (* = current)
pluck model current    → read settings.json env block
pluck model switch     → TUI list selector (no args) or direct switch
pluck model reset      → switch to anthropic
pluck model add        → interactive wizard (default) or CLI flags
pluck model remove     → delete from registry
```

## settings.json Output (after switch)

```json
{
  "model": "sonnet",
  "env": {
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "ANTHROPIC_MODEL": "glm-4.7",
    "ANTHROPIC_AUTH_TOKEN": "sk-xxx...",
    "_pluck_provider": "zhipu",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "glm-4.7",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "glm-5.1",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "glm-4.6"
  }
}
```
