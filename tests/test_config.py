"""Tests for pluck.config — validation, loading, and path management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pluck.config import (
    COMPONENT_TYPES,
    CONFIG_FILE_NAME,
    MARKETPLACE_NAME,
    MAX_PLUGIN_NAME_LENGTH,
    ensure_config_file,
    get_claude_config_dir,
    get_default_config_path,
    get_install_dir,
    get_repos_dir,
    load_config,
    validate_component_name,
    validate_config,
    validate_plugin,
    validate_plugin_name,
    _normalize_selection,
)


# ─── validate_plugin_name ────────────────────────────────────────


class TestValidatePluginName:
    def test_accepts_simple_name(self) -> None:
        assert validate_plugin_name("ecc") == "ecc"

    def test_lowercases(self) -> None:
        assert validate_plugin_name("ECC") == "ecc"

    def test_accepts_hyphens_dots_underscores(self) -> None:
        assert validate_plugin_name("my-plugin.v2_beta") == "my-plugin.v2_beta"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_plugin_name("")

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name(None)  # type: ignore[arg-type]

    def test_rejects_path_separators(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name("../escape")

    def test_rejects_absolute_path(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name("/etc/passwd")

    def test_rejects_too_long(self) -> None:
        long_name = "a" * (MAX_PLUGIN_NAME_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            validate_plugin_name(long_name)

    def test_rejects_starts_with_dot(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name(".hidden")

    def test_rejects_starts_with_dash(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name("-danger")

    def test_rejects_spaces(self) -> None:
        with pytest.raises(ValueError):
            validate_plugin_name("has space")


# ─── validate_component_name ─────────────────────────────────────


class TestValidateComponentName:
    def test_accepts_normal(self) -> None:
        assert validate_component_name("react-patterns") == "react-patterns"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_component_name("")

    def test_rejects_slash(self) -> None:
        with pytest.raises(ValueError, match="path separator"):
            validate_component_name("foo/bar")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="path separator"):
            validate_component_name("foo\\bar")

    def test_rejects_null(self) -> None:
        with pytest.raises(ValueError, match="path separator"):
            validate_component_name("foo\x00bar")

    def test_rejects_dot_dot(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            validate_component_name("..")

    def test_rejects_dot(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            validate_component_name(".")


# ─── _normalize_selection ────────────────────────────────────────


class TestNormalizeSelection:
    def test_none_returns_empty(self) -> None:
        assert _normalize_selection(None) == []

    def test_false_returns_empty(self) -> None:
        assert _normalize_selection(False) == []

    def test_true_returns_all(self) -> None:
        assert _normalize_selection(True) == "all"

    def test_string_all(self) -> None:
        assert _normalize_selection("all") == "all"

    def test_single_string_becomes_list(self) -> None:
        result = _normalize_selection("my-skill")
        assert result == ["my-skill"]

    def test_list_passthrough(self) -> None:
        result = _normalize_selection(["a", "b"])
        assert result == ["a", "b"]

    def test_rejects_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _normalize_selection(42)  # type: ignore[arg-type]


# ─── validate_plugin ─────────────────────────────────────────────


class TestValidatePlugin:
    def test_minimal_valid(self) -> None:
        plugin = {"name": "ecc", "repo": "https://github.com/x/ecc.git"}
        result = validate_plugin(plugin, 0)
        assert result["name"] == "ecc"
        assert result["repo"] == "https://github.com/x/ecc.git"
        assert result["branch"] == "main"
        for ct in COMPONENT_TYPES:
            assert ct in result["components"]

    def test_missing_name(self) -> None:
        with pytest.raises(ValueError, match="missing 'name'"):
            validate_plugin({"repo": "https://x"}, 0)

    def test_missing_repo(self) -> None:
        with pytest.raises(ValueError, match="missing 'repo'"):
            validate_plugin({"name": "x"}, 0)

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            validate_plugin("not a dict", 0)  # type: ignore[arg-type]

    def test_components_not_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            validate_plugin(
                {"name": "x", "repo": "https://x", "components": [1, 2]}, 0
            )

    def test_custom_branch(self) -> None:
        result = validate_plugin(
            {"name": "x", "repo": "https://x", "branch": "dev"}, 0
        )
        assert result["branch"] == "dev"

    def test_normalizes_components(self) -> None:
        result = validate_plugin(
            {
                "name": "x",
                "repo": "https://x",
                "components": {"skills": True, "agents": ["a1"]},
            },
            0,
        )
        assert result["components"]["skills"] == "all"
        assert result["components"]["agents"] == ["a1"]


# ─── validate_config ─────────────────────────────────────────────


class TestValidateConfig:
    def test_valid_config(self) -> None:
        cfg = {"plugins": [{"name": "ecc", "repo": "https://x"}]}
        result = validate_config(cfg)
        assert len(result["plugins"]) == 1

    def test_not_dict(self) -> None:
        with pytest.raises(ValueError, match="YAML mapping"):
            validate_config([])  # type: ignore[arg-type]

    def test_missing_plugins_key(self) -> None:
        with pytest.raises(ValueError, match="'plugins' key"):
            validate_config({})

    def test_plugins_not_list(self) -> None:
        with pytest.raises(ValueError, match="must be a list"):
            validate_config({"plugins": "nope"})

    def test_empty_plugins(self) -> None:
        result = validate_config({"plugins": []})
        assert result["plugins"] == []


# ─── path management ─────────────────────────────────────────────


class TestPathManagement:
    def test_get_claude_config_dir_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = get_claude_config_dir()
            assert result == Path.home() / ".claude"

    def test_get_claude_config_dir_from_env(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/test-claude"}):
            result = get_claude_config_dir()
            assert result == Path("/tmp/test-claude")

    def test_get_repos_dir_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = get_repos_dir()
            assert result == Path.home() / ".cache" / MARKETPLACE_NAME / "repos"

    def test_get_repos_dir_xdg(self) -> None:
        with patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/xdg-cache"}, clear=False):
            result = get_repos_dir()
            assert result == Path("/tmp/xdg-cache") / MARKETPLACE_NAME / "repos"

    def test_get_install_dir(self) -> None:
        result = get_install_dir("ecc", Path("/tmp/claude"))
        assert result == Path("/tmp/claude/plugins") / MARKETPLACE_NAME / "ecc"

    def test_get_default_config_path(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/cdir"}):
            result = get_default_config_path()
            assert result == Path("/tmp/cdir") / CONFIG_FILE_NAME


# ─── ensure_config_file ─────────────────────────────────────────


class TestEnsureConfigFile:
    def test_creates_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "pluck.yaml"
        ensure_config_file(cfg)
        assert cfg.exists()
        assert cfg.read_text() == "plugins: []\n"

    def test_does_not_overwrite(self, tmp_path: Path) -> None:
        cfg = tmp_path / "pluck.yaml"
        cfg.write_text("plugins:\n  - name: existing\n")
        ensure_config_file(cfg)
        assert "existing" in cfg.read_text()


# ─── load_config ─────────────────────────────────────────────────


class TestLoadConfig:
    def test_loads_valid(self, tmp_path: Path) -> None:
        cfg = tmp_path / "pluck.yaml"
        cfg.write_text(
            "plugins:\n"
            "  - name: ecc\n"
            "    repo: https://github.com/x/ecc.git\n"
        )
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp_path)}):
            result = load_config()
        assert len(result["plugins"]) == 1
        assert result["plugins"][0]["name"] == "ecc"

    def test_creates_missing_file(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp_path)}):
            result = load_config()
        assert result["plugins"] == []
        assert (tmp_path / "pluck.yaml").exists()

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pluck.yaml").write_text("")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp_path)}):
            result = load_config()
        assert result["plugins"] == []


# ─── remove_from_selection ──────────────────────────────────────


class TestRemoveFromSelection:
    def test_removes_from_explicit_list(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection(["a", "b", "c"], {"b"})
        assert result == ["a", "c"]

    def test_converts_all_to_explicit_list(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection("all", {"b"}, all_items=["a", "b", "c"])
        assert result == ["a", "c"]

    def test_all_requires_all_items(self) -> None:
        from pluck.config import remove_from_selection
        with pytest.raises(ValueError, match="all_items"):
            remove_from_selection("all", {"b"})

    def test_empty_result(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection(["a"], {"a"})
        assert result == []

    def test_no_match(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection(["a", "b"], {"z"})
        assert result == ["a", "b"]

    def test_empty_remove_set(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection(["a", "b"], set())
        assert result == ["a", "b"]

    def test_all_removes_everything(self) -> None:
        from pluck.config import remove_from_selection
        result = remove_from_selection("all", {"a", "b"}, all_items=["a", "b"])
        assert result == []
