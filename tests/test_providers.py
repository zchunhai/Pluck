"""Tests for global provider management."""

import importlib
import pytest

from pluck.providers import ModelTier, ProviderConfig


@pytest.fixture(autouse=True)
def isolated_pluck_dir(tmp_path, monkeypatch):
    """Isolate all pluck config to a temp directory for every test."""
    monkeypatch.setenv("PLUCK_CONFIG_DIR", str(tmp_path / "pluck"))

    from pluck import providers, env
    importlib.reload(providers)
    importlib.reload(env)


def _add_test_provider(name="test-provider", **kwargs):
    """Helper to add a provider for testing."""
    from pluck.providers import add_provider
    config = ProviderConfig(
        name=name,
        display_name=kwargs.get("display_name", name.title()),
        base_url=kwargs.get("base_url", "https://test.example.com/api"),
        models=kwargs.get("models", {
            "sonnet": ModelTier(id="test-sonnet"),
        }),
        default_tier=kwargs.get("default_tier", "sonnet"),
        auth_token=kwargs.get("auth_token"),
    )
    add_provider(config)
    return config


def test_empty_registry_when_file_missing():
    """Registry returns empty when providers.yaml doesn't exist."""
    from pluck.providers import get_providers

    providers = get_providers()
    assert providers == {}


def test_active_provider_empty_by_default():
    """Active provider is empty string when no providers exist."""
    from pluck.providers import get_active_provider

    assert get_active_provider() == ""


def test_add_and_get_provider():
    """Add a provider then retrieve it."""
    from pluck.providers import get_provider

    _add_test_provider(name="my-api")

    p = get_provider("my-api")
    assert p is not None
    assert p.name == "my-api"
    assert p.base_url == "https://test.example.com/api"

    # Case insensitive
    p2 = get_provider("MY-API")
    assert p2 is not None
    assert p2.name == "my-api"


def test_get_provider_not_found():
    """get_provider returns None for non-existent provider."""
    from pluck.providers import get_provider
    assert get_provider("nonexistent") is None


def test_list_providers():
    """List returns sorted providers."""
    from pluck.providers import list_providers

    _add_test_provider(name="c-provider")
    _add_test_provider(name="a-provider")
    _add_test_provider(name="b-provider")

    providers = list_providers()
    names = [p.name for p in providers]
    assert names == sorted(names)
    assert len(providers) == 3


def test_remove_provider():
    """Remove an existing provider."""
    from pluck.providers import remove_provider, get_provider

    _add_test_provider(name="temp-provider")
    assert get_provider("temp-provider") is not None

    remove_provider("temp-provider")
    assert get_provider("temp-provider") is None


def test_remove_provider_not_found():
    """Error when removing non-existent provider."""
    from pluck.providers import remove_provider

    with pytest.raises(ValueError, match="not found"):
        remove_provider("nonexistent")


def test_add_duplicate_fails():
    """Adding duplicate provider name raises ValueError."""
    _add_test_provider(name="dup")
    with pytest.raises(ValueError, match="already exists"):
        _add_test_provider(name="dup")


def test_set_active_provider():
    """Set and get active provider."""
    from pluck.providers import set_active_provider, get_active_provider

    _add_test_provider(name="active-test")
    set_active_provider("active-test")
    assert get_active_provider() == "active-test"


def test_roundtrip_auth_token():
    """Provider with auth_token survives round-trip."""
    from pluck.providers import get_provider

    _add_test_provider(
        name="token-provider",
        auth_token="sk-secret-123",
    )

    p = get_provider("token-provider")
    assert p.auth_token == "sk-secret-123"
