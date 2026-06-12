"""Global model provider management.

Stores provider configurations centrally in ~/.config/pluck/providers.yaml,
allowing all environments to share the same provider definitions while maintaining
independent model selections.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)

# Configurable via PLUCK_CONFIG_DIR env var; defaults to XDG_CONFIG_HOME/pluck
def _get_pluck_dir() -> Path:
    """Return the pluck config directory, respecting environment variables."""
    custom = os.environ.get("PLUCK_CONFIG_DIR")
    if custom:
        return Path(custom)
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "pluck"


_PROVIDERS_DIR = _get_pluck_dir()
PROVIDERS_PATH = _PROVIDERS_DIR / "providers.yaml"


def _refresh_paths() -> None:
    """Re-read PLUCK_CONFIG_DIR env var to update paths.

    Call this in test setup after setting the env var.
    """
    global _PROVIDERS_DIR, PROVIDERS_PATH
    _PROVIDERS_DIR = _get_pluck_dir()
    PROVIDERS_PATH = _PROVIDERS_DIR / "providers.yaml"


@dataclass(frozen=True)
class ModelTier:
    """Model tier configuration."""
    id: str


@dataclass(frozen=True)
class ProviderConfig:
    """Model provider configuration."""
    name: str
    display_name: str
    base_url: str
    models: dict[str, ModelTier]
    default_tier: str = "sonnet"
    auth_token: str | None = None


@dataclass
class ProviderRegistry:
    """Global provider registry."""
    version: int = 1
    providers: dict[str, ProviderConfig] = None
    active_provider: str = ""

    def __post_init__(self):
        if self.providers is None:
            self.providers = {}


def _load_registry() -> ProviderRegistry:
    """Load provider registry from disk.

    Returns an empty registry when file doesn't exist — no built-in
    providers are created automatically.
    """
    if not PROVIDERS_PATH.exists():
        logger.debug("Provider registry not found at %s", PROVIDERS_PATH)
        return ProviderRegistry(providers={})

    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")

    try:
        with open(PROVIDERS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Failed to load providers from %s: %s", PROVIDERS_PATH, e)
        return ProviderRegistry(providers={})

    try:
        # Parse providers with ModelTier objects
        providers = {}
        for name, config in data.get("providers", {}).items():
            models = {
                tier: ModelTier(id=tier_data["id"])
                for tier, tier_data in config["models"].items()
            }
            providers[name] = ProviderConfig(
                name=name,
                display_name=config["display_name"],
                base_url=config["base_url"],
                models=models,
                default_tier=config.get("default_tier", "sonnet"),
                auth_token=config.get("auth_token"),
            )

        return ProviderRegistry(
            version=data.get("version", 1),
            providers=providers,
            active_provider=data.get("active_provider", "anthropic"),
        )
    except (KeyError, TypeError) as e:
        logger.error("Invalid provider registry format: %s", e)
        return ProviderRegistry(providers={})


def _save_registry(registry: ProviderRegistry) -> None:
    """Save provider registry to disk."""
    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")

    # Ensure directory exists
    PROVIDERS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Convert to serializable format
    data = {
        "version": registry.version,
        "active_provider": registry.active_provider,
        "providers": {},
    }

    for name, config in registry.providers.items():
        provider_data = {
            "display_name": config.display_name,
            "base_url": config.base_url,
            "default_tier": config.default_tier,
            "models": {
                tier_name: {"id": tier_config.id}
                for tier_name, tier_config in config.models.items()
            },
        }
        if config.auth_token is not None:
            provider_data["auth_token"] = config.auth_token
        data["providers"][name] = provider_data

    content = yaml.dump(data, allow_unicode=True, default_flow_style=False)
    from pluck.io_utils import atomic_write
    atomic_write(PROVIDERS_PATH, content)


def _create_default_registry() -> ProviderRegistry:
    """Create default registry with built-in providers."""
    from pluck.model_config import BUILTIN_PROVIDERS

    return ProviderRegistry(
        version=1,
        providers=BUILTIN_PROVIDERS,
        active_provider="anthropic",
    )


def get_providers() -> dict[str, ProviderConfig]:
    """Get all available providers.

    Returns
    -------
    dict[str, ProviderConfig]
        Dictionary mapping provider names to configurations.
    """
    registry = _load_registry()
    return registry.providers


def get_provider(name: str) -> ProviderConfig | None:
    """Get a specific provider by name.

    Parameters
    ----------
    name:
        Provider name (case-insensitive).

    Returns
    -------
    ProviderConfig | None
        Provider configuration, or None if not found.
    """
    providers = get_providers()
    name_lower = name.lower()

    for provider_name, config in providers.items():
        if provider_name.lower() == name_lower:
            return config

    return None


def add_provider(config: ProviderConfig) -> None:
    """Add a new provider to the global registry.

    Parameters
    ----------
    config:
        Provider configuration to add.

    Raises
    ------
    ValueError
        If provider name is invalid or already exists.
    """
    from pluck.config import validate_plugin_name

    config = ProviderConfig(
        name=validate_plugin_name(config.name),
        display_name=config.display_name,
        base_url=config.base_url,
        models=config.models,
        default_tier=config.default_tier,
        auth_token=config.auth_token,
    )

    registry = _load_registry()

    # Check for duplicate
    name_lower = config.name.lower()
    for name in registry.providers.keys():
        if name.lower() == name_lower:
            raise ValueError(f"Provider '{config.name}' already exists")

    registry.providers[config.name] = config
    _save_registry(registry)

    logger.info("Added provider '%s'", config.name)


def remove_provider(name: str) -> None:
    """Remove a provider from the global registry.

    Parameters
    ----------
    name:
        Provider name to remove.

    Raises
    ------
    ValueError
        If provider not found.
    """
    registry = _load_registry()
    name_lower = name.lower()

    # Find and remove
    for provider_name in list(registry.providers.keys()):
        if provider_name.lower() == name_lower:
            del registry.providers[provider_name]
            _save_registry(registry)
            logger.info("Removed provider '%s'", provider_name)
            return

    raise ValueError(f"Provider not found: {name}")


def list_providers() -> list[ProviderConfig]:
    """List all available providers.

    Returns
    -------
    list[ProviderConfig]
        List of provider configurations sorted by name.
    """
    providers = get_providers()
    return sorted(providers.values(), key=lambda p: p.name)


def get_active_provider() -> str:
    """Get the default active provider name.

    Returns
    -------
    str
        Name of the active provider.
    """
    registry = _load_registry()
    return registry.active_provider


def set_active_provider(name: str) -> None:
    """Set the default active provider.

    Parameters
    ----------
    name:
        Provider name to set as active.

    Raises
    ------
    ValueError
        If provider not found.
    """
    registry = _load_registry()
    name_lower = name.lower()

    # Find provider
    for provider_name in registry.providers.keys():
        if provider_name.lower() == name_lower:
            registry.active_provider = provider_name
            _save_registry(registry)
            logger.info("Set active provider to '%s'", provider_name)
            return

    raise ValueError(f"Provider not found: {name}")
