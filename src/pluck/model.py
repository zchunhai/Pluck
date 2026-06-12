"""Model provider management and switching for pluck.

Bridges the global provider registry (~/.config/pluck/providers.yaml) with
per-environment Claude settings ($CLAUDE_CONFIG_DIR/settings.json).

CLI unified as ``pluck model`` with subcommands:
    list / current / switch / reset / add / remove
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pluck.config import get_claude_config_dir
from pluck.io_utils import atomic_write_json, safe_load_json
from pluck.providers import (
    ModelTier,
    ProviderConfig,
    add_provider as _add_provider,
    get_provider,
    list_providers as get_all_providers,
    remove_provider as _remove_provider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def get_settings_path() -> Path:
    """Get Claude settings.json path."""
    return get_claude_config_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    """Load current Claude settings."""
    settings_path = get_settings_path()
    return safe_load_json(settings_path) or {"enabledPlugins": {}}


def save_settings(settings: dict[str, Any]) -> None:
    """Atomically save Claude settings."""
    atomic_write_json(get_settings_path(), settings)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def get_current_model() -> dict[str, Any]:
    """Get current model configuration from settings.json."""
    settings = load_settings()

    return {
        "provider": _detect_provider(settings),
        "model": settings.get("model", "sonnet"),
        "base_url": settings.get("env", {}).get("ANTHROPIC_BASE_URL", "unknown"),
        "anthropic_model": settings.get("env", {}).get("ANTHROPIC_MODEL", "unknown"),
    }


def _detect_provider(settings: dict[str, Any]) -> str:
    """Detect provider from stored provider name or base URL.

    Prefers the explicit ``_pluck_provider`` key written by ``switch_provider``.
    Falls back to URL substring matching for legacy configs.
    """
    # Fast path: explicit provider name stored by switch_provider
    explicit = settings.get("env", {}).get("_pluck_provider")
    if explicit:
        return explicit

    # Legacy: infer from base URL
    base_url = settings.get("env", {}).get("ANTHROPIC_BASE_URL", "")

    if "api.anthropic.com" in base_url:
        return "anthropic"
    elif "bigmodel.cn" in base_url:
        return "zhipu"
    elif "deepseek.com" in base_url:
        return "deepseek"
    else:
        return "custom"


# ---------------------------------------------------------------------------
# Model tier helper
# ---------------------------------------------------------------------------


def _get_model_id(provider: ProviderConfig, tier: str, fallback_id: str) -> str:
    """Resolve a model ID for a tier, with fallback."""
    if tier in provider.models:
        return provider.models[tier].id
    return fallback_id


# ---------------------------------------------------------------------------
# Switch / Reset
# ---------------------------------------------------------------------------


def switch_provider(provider_name: str, model_tier: str | None = None) -> None:
    """Switch to a different model provider.

    Reads provider configuration from global registry and updates current
    environment's ``settings.json``.

    Parameters
    ----------
    provider_name:
        Target provider name (case-insensitive).
    model_tier:
        Model tier to use (opus, sonnet, haiku). Defaults to provider's default.

    Raises
    ------
    ValueError
        If provider not found or model tier is invalid.
    """
    provider = get_provider(provider_name)
    if provider is None:
        available = ", ".join(p.name for p in get_all_providers())
        raise ValueError(
            f"Provider '{provider_name}' not found. Available: {available}"
        )

    tier = model_tier or provider.default_tier

    if tier not in provider.models:
        available = ", ".join(provider.models.keys())
        raise ValueError(
            f"Invalid model tier '{tier}' for {provider.name}. "
            f"Available: {available}"
        )

    model_id = provider.models[tier].id

    settings = load_settings()

    settings["model"] = tier
    settings.setdefault("env", {})
    settings["env"]["ANTHROPIC_BASE_URL"] = provider.base_url
    settings["env"]["ANTHROPIC_MODEL"] = model_id
    settings["env"]["_pluck_provider"] = provider.name

    for tier_key in ("sonnet", "opus", "haiku"):
        mid = _get_model_id(provider, tier_key, model_id)
        settings["env"][f"ANTHROPIC_DEFAULT_{tier_key.upper()}_MODEL"] = mid
        settings["env"][f"ANTHROPIC_DEFAULT_{tier_key.upper()}_MODEL_NAME"] = mid

    if provider.auth_token:
        settings["env"]["ANTHROPIC_AUTH_TOKEN"] = provider.auth_token

    save_settings(settings)

    logger.info(
        "Switched to %s (%s tier, model: %s)",
        provider.display_name,
        tier,
        model_id,
    )


def reset_to_default() -> None:
    """Reset current environment to default provider (anthropic)."""
    anthropic = get_provider("anthropic")
    if anthropic is None:
        raise ValueError("Default provider 'anthropic' not found in registry")

    switch_provider("anthropic", anthropic.default_tier)
    logger.info("Reset to Anthropic (official)")


# ---------------------------------------------------------------------------
# List / Display
# ---------------------------------------------------------------------------


def list_providers(show_current: bool = True) -> None:
    """Display available providers from global registry.

    Parameters
    ----------
    show_current:
        Whether to highlight the currently active provider.
    """
    current = get_current_model()
    current_provider = current["provider"]

    all_providers = get_all_providers()

    logger.info("Available model providers:\n")

    for provider in sorted(all_providers, key=lambda p: p.name):
        is_current = provider.name.lower() == current_provider.lower()
        marker = "*" if is_current and show_current else " "

        logger.info(
            "  %s %-12s %s",
            marker,
            provider.name,
            provider.display_name,
        )
        logger.info("     Base URL: %s", provider.base_url)
        logger.info(
            "     Models: %s",
            ", ".join(f"{k}: {v.id}" for k, v in provider.models.items()),
        )
        logger.info("")


# ---------------------------------------------------------------------------
# Provider registry management (delegates to providers.py)
# ---------------------------------------------------------------------------


def add_provider(config: ProviderConfig) -> None:
    """Add a new provider to the global registry.

    Delegates to :func:`pluck.providers.add_provider`.
    """
    _add_provider(config)


def remove_provider(name: str) -> None:
    """Remove a provider from the global registry.

    Delegates to :func:`pluck.providers.remove_provider`.
    """
    _remove_provider(name)
