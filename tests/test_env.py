"""Tests for pluck environment management."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pluck.env import (
    EnvironmentEntry,
    _create_skeleton,
    _load_registry,
    _save_registry,
    create_env,
    deactivate_command,
    delete_env,
    get_current_env,
    get_env_path,
    list_envs,
    switch_env_command,
)

# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_registry(tmp_path: Path) -> Path:
    """Point ENV_REGISTRY_PATH to a temp file."""
    registry_file = tmp_path / "environments.json"
    with patch("pluck.env.ENV_REGISTRY_PATH", registry_file):
        yield registry_file


@pytest.fixture
def temp_env_home(tmp_path: Path) -> Path:
    """Point DEFAULT_ENV_HOME to a temp directory."""
    env_home = tmp_path / "envs"
    with patch("pluck.env.DEFAULT_ENV_HOME", env_home):
        yield env_home


# ────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_load_empty_creates_file(self, temp_registry: Path) -> None:
        assert not temp_registry.exists()
        result = _load_registry()
        assert result == []
        assert temp_registry.exists()

    def test_save_and_load_roundtrip(self, temp_registry: Path) -> None:
        entry: EnvironmentEntry = {
            "name": "test",
            "path": "/fake/path",
            "created_at": "2026-01-01T00:00:00+00:00",
            "description": "",
        }
        _save_registry([entry])
        result = _load_registry()
        assert len(result) == 1
        assert result[0]["name"] == "test"
        assert result[0]["path"] == "/fake/path"

    def test_recovers_from_corrupt_json(self, temp_registry: Path) -> None:
        temp_registry.parent.mkdir(parents=True, exist_ok=True)
        temp_registry.write_text("not valid json {{{", encoding="utf-8")
        result = _load_registry()
        assert result == []

    def test_handles_missing_environments_key(self, temp_registry: Path) -> None:
        temp_registry.parent.mkdir(parents=True, exist_ok=True)
        temp_registry.write_text('{"version": 1}', encoding="utf-8")
        result = _load_registry()
        assert result == []

    def test_preserves_order_on_save(self, temp_registry: Path) -> None:
        entries: list[EnvironmentEntry] = [
            {"name": "a", "path": "/a", "created_at": "1", "description": ""},
            {"name": "b", "path": "/b", "created_at": "2", "description": ""},
        ]
        _save_registry(entries)
        result = _load_registry()
        assert [e["name"] for e in result] == ["a", "b"]


# ────────────────────────────────────────────────────────────────
# Skeleton
# ────────────────────────────────────────────────────────────────


class TestSkeleton:
    def test_creates_all_files(self, tmp_path: Path) -> None:
        env_dir = tmp_path / "skeleton-test"
        _create_skeleton(env_dir)

        assert env_dir.is_dir()
        assert (env_dir / "CLAUDE.md").exists()
        assert (env_dir / "memory").is_dir()
        assert (env_dir / "plugins").is_dir()

        pluck_yaml = env_dir / "pluck.yaml"
        assert pluck_yaml.read_text() == "plugins: []\n"

        settings = json.loads((env_dir / "settings.json").read_text())
        assert settings == {"enabledPlugins": {}}

        installed = json.loads(
            (env_dir / "plugins" / "installed_plugins.json").read_text()
        )
        assert installed["version"] == 2
        assert installed["plugins"] == {}


# ────────────────────────────────────────────────────────────────
# Create
# ────────────────────────────────────────────────────────────────


class TestCreateEnv:
    def test_creates_with_default_path(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        env_path = create_env("myenv")
        assert env_path == temp_env_home / "myenv"
        assert env_path.is_dir()
        assert (env_path / "pluck.yaml").exists()

        registry = _load_registry()
        assert len(registry) == 1
        assert registry[0]["name"] == "myenv"

    def test_creates_with_custom_path(
        self, temp_registry: Path, tmp_path: Path
    ) -> None:
        custom = tmp_path / "custom-env"
        env_path = create_env("custom", path=custom)
        assert env_path == custom.resolve()
        assert env_path.is_dir()

    def test_rejects_duplicate_name(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        create_env("dup")
        with pytest.raises(ValueError, match="already exists"):
            create_env("dup")

    def test_rejects_invalid_name(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        with pytest.raises(ValueError):
            create_env("../escape")

    def test_rejects_non_empty_directory(
        self, temp_registry: Path, tmp_path: Path
    ) -> None:
        existing = tmp_path / "existing"
        existing.mkdir()
        (existing / "some-file").write_text("data")
        with pytest.raises(ValueError, match="not empty"):
            create_env("test", path=existing)

    def test_rejects_path_already_registered(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        env_path = create_env("first")
        with pytest.raises(ValueError, match="already registered"):
            create_env("second", path=env_path)


# ────────────────────────────────────────────────────────────────
# Delete
# ────────────────────────────────────────────────────────────────


class TestDeleteEnv:
    def test_deletes_normal(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        create_env("to_delete")
        assert len(_load_registry()) == 1
        delete_env("to_delete")
        assert len(_load_registry()) == 0
        assert not (temp_env_home / "to_delete").exists()

    def test_refuses_active_env(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        env_path = create_env("active")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(env_path)}), \
             pytest.raises(ValueError, match="active environment"):
            delete_env("active")

    def test_rejects_nonexistent(self, temp_registry: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            delete_env("nonexistent")

    def test_case_insensitive_delete(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        create_env("CaseSensitive")
        delete_env("casesensitive")
        assert len(_load_registry()) == 0


# ────────────────────────────────────────────────────────────────
# List
# ────────────────────────────────────────────────────────────────


class TestListEnvs:
    def test_empty(self, temp_registry: Path) -> None:
        assert list_envs() == []

    def test_multiple_sorted_by_time(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        create_env("first")
        create_env("second")
        result = list_envs()
        names = [e["name"] for e in result]
        # Newest first (second was created after first)
        assert names == ["second", "first"]


# ────────────────────────────────────────────────────────────────
# Current / Path
# ────────────────────────────────────────────────────────────────


class TestCurrentEnv:
    def test_returns_none_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert get_current_env() is None

    def test_detects_active(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        env_path = create_env("detect-me")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(env_path)}):
            current = get_current_env()
            assert current is not None
            assert current["name"] == "detect-me"

    def test_returns_none_for_unknown_dir(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        create_env("known")  # populate registry
        unknown = str(temp_env_home / "unknown-dir")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": unknown}):
            assert get_current_env() is None


class TestGetEnvPath:
    def test_found(self, temp_registry: Path, temp_env_home: Path) -> None:
        env_path = create_env("lookup")
        result = get_env_path("lookup")
        assert result == env_path

    def test_not_found(self, temp_registry: Path) -> None:
        assert get_env_path("missing") is None

    def test_case_insensitive(self, temp_registry: Path, temp_env_home: Path) -> None:
        env_path = create_env("CaseName")
        result = get_env_path("casename")
        assert result == env_path


# ────────────────────────────────────────────────────────────────
# Switch / Deactivate commands
# ────────────────────────────────────────────────────────────────


class TestSwitchCommand:
    def test_output_format(self, temp_registry: Path, temp_env_home: Path) -> None:
        create_env("testenv")
        cmd = switch_env_command("testenv")
        assert 'export CLAUDE_CONFIG_DIR=' in cmd
        assert str(temp_env_home / "testenv") in cmd
        assert cmd.startswith("export ")

    def test_nonexistent_raises(self, temp_registry: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            switch_env_command("missing")


class TestDeactivateCommand:
    def test_output_format(self) -> None:
        cmd = deactivate_command()
        assert "unset CLAUDE_CONFIG_DIR" in cmd
        assert "echo" in cmd


# ────────────────────────────────────────────────────────────────
# Integration: install inside a switched environment
# ────────────────────────────────────────────────────────────────


class TestIntegration:
    def test_pluck_commands_respect_env(
        self, temp_registry: Path, temp_env_home: Path
    ) -> None:
        """Verify that when CLAUDE_CONFIG_DIR points to an env,
        get_claude_config_dir() returns the env directory.
        """
        from pluck.config import get_claude_config_dir

        env_path = create_env("integration-env")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(env_path)}):
            resolved = get_claude_config_dir()
            assert resolved == env_path
