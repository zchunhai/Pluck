"""Config saving for pluck."""

import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from pluck.io_utils import atomic_write

logger = logging.getLogger(__name__)


def save_config(config_path: Path, plugins: list[dict[str, Any]]) -> None:
    """Save plugin configuration to a YAML file atomically.

    Preserves any extra keys that exist in the original file for each
    plugin entry, merging the new selections on top.
    """
    if yaml is None:
        raise ImportError("PyYAML is required")

    original: dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                original = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.debug("Could not read existing config, starting fresh: %s", exc)

    original_plugins = original.get("plugins", [])
    updated_plugins: list[dict[str, Any]] = []

    for plugin in plugins:
        name = plugin["name"]
        orig_entry: dict[str, Any] = next(
            (p for p in original_plugins if p.get("name") == name), {}
        )
        extra_orig = {
            k: v
            for k, v in orig_entry.items()
            if k not in ("name", "repo", "branch", "components")
        }
        merged = {
            "name": plugin["name"],
            "repo": plugin.get("repo") or orig_entry.get("repo", ""),
            "branch": plugin.get("branch") or orig_entry.get("branch", "main"),
            **extra_orig,
            "components": plugin["components"],
        }
        updated_plugins.append(merged)

    data = {**original, "plugins": updated_plugins}

    # Atomic write for consistency with JSON paths
    content = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    atomic_write(config_path, content)

    logger.info("Config saved to %s", config_path)
