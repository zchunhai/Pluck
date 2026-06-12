"""Tests for model provider management and switching."""

import importlib
import json
import pytest
from pathlib import Path

from pluck.model import (
    get_current_model,
    list_providers,
    reset_to_default,
    switch_provider,
)

# Built-in providers we seed for testing
_SEED_PROVIDERS = [
    ("anthropic", "Anthropic (Official)", "https://api.anthropic.com",
     {"opus": "claude-opus-4-8", "sonnet": "claude-sonnet-4-6", "haiku": "claude-haiku-4-5"}),
    ("zhipu", "智谱 AI", "https://open.bigmodel.cn/api/anthropic",
     {"opus": "glm-5.1", "sonnet": "glm-5-turbo", "haiku": "glm-4.7"}),
    ("deepseek", "DeepSeek", "https://api.deepseek.com",
     {"sonnet": "deepseek-chat", "haiku": "deepseek-chat"}),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
     {"opus": "anthropic/claude-opus-4", "sonnet": "anthropic/claude-sonnet-4", "haiku": "anthropic/claude-haiku-4"}),
]


@pytest.fixture(autouse=True)
def seed_providers(tmp_path, monkeypatch):
    """Isolate providers to temp dir and seed built-in providers for tests."""
    monkeypatch.setenv("PLUCK_CONFIG_DIR", str(tmp_path / "pluck"))

    from pluck import providers as pmod, env
    importlib.reload(pmod)
    importlib.reload(env)

    from pluck.providers import ModelTier, ProviderConfig, add_provider

    for name, display, url, models in _SEED_PROVIDERS:
        add_provider(ProviderConfig(
            name=name,
            display_name=display,
            base_url=url,
            models={t: ModelTier(id=mid) for t, mid in models.items()},
            default_tier="sonnet",
        ))


@pytest.fixture
def temp_settings(tmp_path):
    """Create temporary settings file."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"enabledPlugins": {}}', encoding="utf-8")
    return settings_file


def _patch_settings(monkeypatch, temp_settings):
    """Monkeypatch get_settings_path to use temp file."""
    monkeypatch.setattr("pluck.model.get_settings_path", lambda: temp_settings)


def test_switch_to_zhipu(temp_settings, monkeypatch):
    """Test switching to Zhipu provider."""
    _patch_settings(monkeypatch, temp_settings)

    switch_provider("zhipu", "sonnet")

    current = get_current_model()
    assert current["provider"] == "zhipu"
    assert current["model"] == "sonnet"
    assert "bigmodel.cn" in current["base_url"]


def test_switch_to_deepseek(temp_settings, monkeypatch):
    """Test switching to DeepSeek provider."""
    _patch_settings(monkeypatch, temp_settings)

    switch_provider("deepseek")

    current = get_current_model()
    assert current["provider"] == "deepseek"
    assert "deepseek.com" in current["base_url"]


def test_switch_nonexistent_provider(temp_settings, monkeypatch):
    """Test error handling when switching to non-existent provider."""
    _patch_settings(monkeypatch, temp_settings)

    with pytest.raises(ValueError, match="not found"):
        switch_provider("nonexistent-xyz")


def test_switch_invalid_tier(temp_settings, monkeypatch):
    """Test error handling for invalid model tier."""
    _patch_settings(monkeypatch, temp_settings)

    # DeepSeek only has sonnet and haiku, no opus
    with pytest.raises(ValueError, match="Invalid model tier"):
        switch_provider("deepseek", "opus")


def test_detect_provider_from_settings(temp_settings, monkeypatch):
    """Test provider detection from settings.json with explicit name."""
    _patch_settings(monkeypatch, temp_settings)

    # Write Zhipu settings with explicit provider name
    temp_settings.write_text(
        json.dumps({
            "enabledPlugins": {},
            "env": {
                "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_MODEL": "glm-5-turbo",
                "_pluck_provider": "zhipu",
            },
        }),
        encoding="utf-8",
    )

    current = get_current_model()
    assert current["provider"] == "zhipu"


def test_detect_provider_legacy_url(temp_settings, monkeypatch):
    """Test legacy provider detection from URL without _pluck_provider key."""
    _patch_settings(monkeypatch, temp_settings)

    # Write Zhipu settings WITHOUT explicit provider name (legacy)
    temp_settings.write_text(
        json.dumps({
            "enabledPlugins": {},
            "env": {
                "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_MODEL": "glm-5-turbo",
            },
        }),
        encoding="utf-8",
    )

    current = get_current_model()
    assert current["provider"] == "zhipu"


def test_list_providers_command(temp_settings, monkeypatch):
    """Test list_providers doesn't crash."""
    _patch_settings(monkeypatch, temp_settings)

    # Should not crash
    list_providers(show_current=False)


def test_reset_to_default(temp_settings, monkeypatch):
    """Test resetting to default provider."""
    _patch_settings(monkeypatch, temp_settings)

    # First switch away from default
    switch_provider("zhipu")
    assert get_current_model()["provider"] == "zhipu"

    # Reset
    reset_to_default()

    current = get_current_model()
    assert current["provider"] == "anthropic"
    assert "api.anthropic.com" in current["base_url"]


def test_switch_stores_provider_name(temp_settings, monkeypatch):
    """Test that switch_provider stores the provider name in settings."""
    _patch_settings(monkeypatch, temp_settings)

    switch_provider("zhipu", "sonnet")

    # Read raw settings to verify _pluck_provider is stored
    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    assert raw["env"]["_pluck_provider"] == "zhipu"


def test_switch_sets_fallback_models(temp_settings, monkeypatch):
    """Test that switch_provider sets model IDs with correct fallbacks."""
    _patch_settings(monkeypatch, temp_settings)

    # DeepSeek has no opus tier — should fall back to the selected tier's model
    switch_provider("deepseek", "sonnet")

    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    # sonnet model should be set correctly
    assert raw["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "deepseek-chat"
    # opus should fall back to the selected model_id since deepseek has no opus
    assert raw["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "deepseek-chat"


def test_switch_writes_model_names(temp_settings, monkeypatch):
    """Test that switch_provider writes _MODEL_NAME env vars."""
    _patch_settings(monkeypatch, temp_settings)

    switch_provider("zhipu", "sonnet")

    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    assert raw["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"] == "glm-5-turbo"
    assert raw["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME"] == "glm-4.7"
    assert raw["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL_NAME"] == "glm-5.1"


def test_switch_writes_auth_token(temp_settings, monkeypatch):
    """Test that switch_provider writes ANTHROPIC_AUTH_TOKEN when provider has one."""
    from pluck.providers import ProviderConfig, ModelTier

    _patch_settings(monkeypatch, temp_settings)

    # Register a provider with an auth token
    from pluck.providers import add_provider
    add_provider(ProviderConfig(
        name="test-token-provider",
        display_name="Test Token Provider",
        base_url="https://test.example.com/api",
        models={"sonnet": ModelTier(id="test-model")},
        default_tier="sonnet",
        auth_token="sk-test-token-123456",
    ))

    switch_provider("test-token-provider", "sonnet")

    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    assert raw["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-test-token-123456"

    # Clean up
    from pluck.providers import remove_provider
    remove_provider("test-token-provider")


def test_switch_skips_auth_token_when_none(temp_settings, monkeypatch):
    """Test that ANTHROPIC_AUTH_TOKEN is not written when provider has no auth_token."""
    _patch_settings(monkeypatch, temp_settings)

    # Zhipu has no auth_token set
    switch_provider("zhipu", "sonnet")

    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    assert "ANTHROPIC_AUTH_TOKEN" not in raw["env"]


def test_switch_model_name_equals_model_id(temp_settings, monkeypatch):
    """Test that _MODEL_NAME is always the same as _MODEL."""
    from pluck.providers import ProviderConfig, ModelTier, add_provider, remove_provider

    _patch_settings(monkeypatch, temp_settings)

    add_provider(ProviderConfig(
        name="cmp-provider",
        display_name="CMP",
        base_url="https://test.example.com/api",
        models={"sonnet": ModelTier(id="cmp-sonnet-v1")},
        default_tier="sonnet",
    ))

    switch_provider("cmp-provider", "sonnet")

    raw = json.loads(temp_settings.read_text(encoding="utf-8"))
    assert raw["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"] == "cmp-sonnet-v1"
    assert raw["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "cmp-sonnet-v1"

    remove_provider("cmp-provider")
