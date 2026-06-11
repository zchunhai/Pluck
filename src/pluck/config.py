"""Config parsing and centralized path management for pluck."""

import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

CONFIG_FILE_NAME = "pluck.yaml"

# Marketplace name used in plugin registry keys: <name>@pluck
MARKETPLACE_NAME = "pluck"

COMPONENT_TYPES = ("skills", "agents", "commands", "rules", "hooks", "contexts")


def get_repos_dir(claude_config_dir: Path | None = None) -> Path:
    """Get the directory where plugin repos are cloned."""
    base = claude_config_dir or get_claude_config_dir()
    return base / MARKETPLACE_NAME / "repos"


def get_install_dir(plugin_name: str, claude_config_dir: Path | None = None) -> Path:
    """Get the install directory for a specific pluck-managed plugin."""
    base = claude_config_dir or get_claude_config_dir()
    return base / "plugins" / "cache" / MARKETPLACE_NAME / plugin_name / "selected"


def get_claude_config_dir() -> Path:
    """Resolve Claude config directory from env var or default."""
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".claude"


def get_default_config_path() -> Path:
    """Default config path: $CLAUDE_CONFIG_DIR/pluck.yaml."""
    return get_claude_config_dir() / CONFIG_FILE_NAME


def ensure_config_file(config_path: Path) -> None:
    """Create a minimal config file if it does not exist."""
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("plugins: []\n")


def load_config() -> dict[str, Any]:
    """Load and validate pluck configuration file from $CLAUDE_CONFIG_DIR/pluck.yaml.

    Creates an empty config file if it does not exist.
    """
    config_path = get_default_config_path()

    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")

    ensure_config_file(config_path)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if config is None:
        config = {"plugins": []}

    return validate_config(config)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate configuration structure and normalize values."""
    if not isinstance(config, dict):
        raise ValueError("Config must be a YAML mapping")

    if "plugins" not in config:
        raise ValueError("Config must have a 'plugins' key")

    plugins = config["plugins"]
    if not isinstance(plugins, list):
        raise ValueError("'plugins' must be a list")

    validated = []
    for i, plugin in enumerate(plugins):
        validated.append(validate_plugin(plugin, i))

    return {"plugins": validated}


def validate_plugin(plugin: dict[str, Any], index: int) -> dict[str, Any]:
    """Validate a single plugin configuration entry."""
    if not isinstance(plugin, dict):
        raise ValueError(f"Plugin #{index} must be a mapping")

    name = plugin.get("name")
    if not name:
        raise ValueError(f"Plugin #{index} missing 'name'")

    repo = plugin.get("repo")
    if not repo:
        raise ValueError(f"Plugin '{name}' missing 'repo' URL")

    components = plugin.get("components", {})
    if not isinstance(components, dict):
        raise ValueError(f"Plugin '{name}' components must be a mapping")

    normalized = {}
    for comp_type in COMPONENT_TYPES:
        normalized[comp_type] = _normalize_selection(components.get(comp_type))

    return {
        "name": name,
        "repo": repo,
        "branch": plugin.get("branch", "main"),
        "components": normalized,
    }


def _normalize_selection(value: Any) -> list[str] | str:
    """Normalize a component selection to a list of names or 'all'."""
    if value is None or value is False:
        return []
    if value is True:
        return "all"
    if isinstance(value, str):
        if value == "all":
            return "all"
        return [value]
    if isinstance(value, list):
        return value
    raise ValueError(f"Invalid component selection: {value!r}")
