"""Tests for pluck.installer — plugin install, uninstall, and registry."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pluck.config import MARKETPLACE_NAME
from pluck.installer import (
    _ensure_path_within_base,
    _find_key_case_insensitive,
    _new_marketplace_template,
    _read_original_manifest,
    get_installed_plugins,
    install_plugin,
    remove_components,
    scan_installed_components,
    uninstall_plugin,
)


# ─── _ensure_path_within_base ────────────────────────────────────


class TestEnsurePathWithinBase:
    def test_valid_subpath(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        target = base / "sub" / "file"
        result = _ensure_path_within_base(target, base)
        assert str(result).startswith(str(base))

    def test_rejects_escape(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        target = tmp_path / "base" / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="escapes"):
            _ensure_path_within_base(target, base)

    def test_rejects_symlink_traversal(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        link = base / "link"
        outside = tmp_path / "outside"
        outside.mkdir()
        link.symlink_to(outside)
        target = link / "evil"
        with pytest.raises(ValueError, match="[Ss]ymlink"):
            _ensure_path_within_base(target, base)


# ─── _find_key_case_insensitive ──────────────────────────────────


class TestFindKeyCaseInsensitive:
    def test_finds_exact(self) -> None:
        data = {"ecc@pluck": {"x": 1}}
        assert _find_key_case_insensitive(data, "ecc", "pluck") == "ecc@pluck"

    def test_finds_different_case(self) -> None:
        data = {"ECC@pluck": {"x": 1}}
        assert _find_key_case_insensitive(data, "ecc", "pluck") == "ECC@pluck"

    def test_returns_none_missing(self) -> None:
        assert _find_key_case_insensitive({}, "ecc", "pluck") is None

    def test_ignores_wrong_marketplace(self) -> None:
        data = {"ecc@other": {"x": 1}}
        assert _find_key_case_insensitive(data, "ecc", "pluck") is None


# ─── _new_marketplace_template ───────────────────────────────────


class TestNewMarketplaceTemplate:
    def test_structure(self) -> None:
        tmpl = _new_marketplace_template()
        assert tmpl["name"] == MARKETPLACE_NAME
        assert tmpl["plugins"] == []
        assert "description" in tmpl


# ─── _read_original_manifest ─────────────────────────────────────


class TestReadOriginalManifest:
    def test_reads_plugin_json(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".claude-plugin"
        manifest_dir.mkdir()
        manifest = manifest_dir / "plugin.json"
        manifest.write_text(json.dumps({"name": "test", "version": "1.0"}))
        result = _read_original_manifest(tmp_path)
        assert result["name"] == "test"

    def test_reads_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "pkg"}))
        result = _read_original_manifest(tmp_path)
        assert result["name"] == "pkg"

    def test_returns_empty_when_none(self, tmp_path: Path) -> None:
        result = _read_original_manifest(tmp_path)
        assert result == {}

    def test_handles_corrupt_json(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text("not json{{{")
        result = _read_original_manifest(tmp_path)
        assert result == {}


# ─── get_installed_plugins ───────────────────────────────────────


class TestGetInstalledPlugins:
    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        result = get_installed_plugins(tmp_path)
        assert result == {}

    def test_returns_pluck_plugins_only(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        data = {
            "version": 2,
            "plugins": {
                "ecc@pluck": [{"scope": "user"}],
                "other@marketplace": [{"scope": "user"}],
            },
        }
        (plugins_dir / "installed_plugins.json").write_text(json.dumps(data))
        result = get_installed_plugins(tmp_path)
        assert "ecc" in result
        assert "other@marketplace" not in result


# ─── install_plugin (integration-style) ──────────────────────────


class TestInstallPlugin:
    def _make_repo(self, tmp_path: Path) -> Path:
        """Create a minimal fake repo structure."""
        repo = tmp_path / "repo"
        repo.mkdir()
        skills_dir = repo / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\n")
        return repo

    def test_installs_selected_components(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "plugins").mkdir()
        (claude_dir / "settings.json").write_text("{}")

        plugin_config = {
            "name": "testplug",
            "repo": "https://example.com/test.git",
            "branch": "main",
            "components": {"skills": ["test-skill"]},
        }

        with patch("pluck.installer._register_marketplace_with_claude"):
            with patch("pluck.installer._get_repo_sha", return_value="abc123"):
                install_dir = install_plugin(plugin_config, repo, claude_dir)

        assert install_dir.exists()
        assert (install_dir / "skills" / "test-skill" / "SKILL.md").exists()

    def test_registers_in_installed_plugins(
        self, tmp_path: Path
    ) -> None:
        repo = self._make_repo(tmp_path)
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "plugins").mkdir()
        (claude_dir / "settings.json").write_text("{}")

        plugin_config = {
            "name": "testplug",
            "repo": "https://example.com/test.git",
            "branch": "main",
            "components": {"skills": ["test-skill"]},
        }

        with patch("pluck.installer._register_marketplace_with_claude"):
            with patch("pluck.installer._get_repo_sha", return_value="abc123"):
                install_plugin(plugin_config, repo, claude_dir)

        plugins_file = claude_dir / "plugins" / "installed_plugins.json"
        assert plugins_file.exists()
        data = json.loads(plugins_file.read_text())
        assert "testplug@pluck" in data["plugins"]

    def test_replaces_existing_install(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "plugins").mkdir()
        (claude_dir / "settings.json").write_text("{}")

        plugin_config = {
            "name": "testplug",
            "repo": "https://example.com/test.git",
            "branch": "main",
            "components": {"skills": ["test-skill"]},
        }

        with patch("pluck.installer._register_marketplace_with_claude"):
            with patch("pluck.installer._get_repo_sha", return_value="abc"):
                install_plugin(plugin_config, repo, claude_dir)
                # Install again — should replace, not error
                install_plugin(plugin_config, repo, claude_dir)


# ─── uninstall_plugin ────────────────────────────────────────────


class TestUninstallPlugin:
    def test_removes_plugin_dir(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        plugins_dir = claude_dir / "plugins" / MARKETPLACE_NAME / "testplug"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "marker.txt").write_text("here")

        # Register in installed_plugins.json
        ip_dir = claude_dir / "plugins"
        ip_data = {"version": 2, "plugins": {"testplug@pluck": [{"scope": "user"}]}}
        (ip_dir / "installed_plugins.json").write_text(json.dumps(ip_data))
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"testplug@pluck": True}})
        )

        result = uninstall_plugin("testplug", claude_dir)
        assert result is True
        assert not plugins_dir.exists()

    def test_removes_from_registry(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        plugins_dir = claude_dir / "plugins" / MARKETPLACE_NAME / "testplug"
        plugins_dir.mkdir(parents=True)

        ip_dir = claude_dir / "plugins"
        ip_data = {"version": 2, "plugins": {"testplug@pluck": [{"scope": "user"}]}}
        (ip_dir / "installed_plugins.json").write_text(json.dumps(ip_data))
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"testplug@pluck": True}})
        )

        uninstall_plugin("testplug", claude_dir)

        ip_result = json.loads(
            (ip_dir / "installed_plugins.json").read_text()
        )
        assert "testplug@pluck" not in ip_result["plugins"]

    def test_case_insensitive(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        plugins_dir = claude_dir / "plugins" / MARKETPLACE_NAME / "testplug"
        plugins_dir.mkdir(parents=True)

        ip_dir = claude_dir / "plugins"
        ip_data = {"version": 2, "plugins": {"TestPlug@pluck": [{"scope": "user"}]}}
        (ip_dir / "installed_plugins.json").write_text(json.dumps(ip_data))
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"TestPlug@pluck": True}})
        )

        result = uninstall_plugin("testplug", claude_dir)
        assert result is True


# ─── remove_components ──────────────────────────────────────────


class TestRemoveComponents:
    def _make_installed_plugin(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a fake installed plugin with multiple component types."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "plugins").mkdir()
        (claude_dir / "settings.json").write_text("{}")

        install_dir = (
            claude_dir / "plugins" / MARKETPLACE_NAME / "testplug"
        )
        install_dir.mkdir(parents=True)

        # skills
        skills = install_dir / "skills"
        skills.mkdir()
        (skills / "skill-a").mkdir()
        (skills / "skill-a" / "SKILL.md").write_text("# A")
        (skills / "skill-b").mkdir()
        (skills / "skill-b" / "SKILL.md").write_text("# B")

        # agents
        agents = install_dir / "agents"
        agents.mkdir()
        (agents / "reviewer.md").write_text("# Reviewer")
        (agents / "architect.md").write_text("# Architect")

        # hooks
        hooks = install_dir / "hooks"
        hooks.mkdir()
        (hooks / "hooks.json").write_text("{}")

        # scripts (hook dependency)
        (install_dir / "scripts").mkdir()
        (install_dir / "scripts" / "run.sh").write_text("#!/bin/sh")
        (install_dir / "package.json").write_text("{}")

        return claude_dir, install_dir

    def test_removes_skill_directory(self, tmp_path: Path) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        count = remove_components(
            "testplug", {"skills": {"skill-a"}}, claude_dir
        )
        assert count == 1
        assert not (install_dir / "skills" / "skill-a").exists()
        assert (install_dir / "skills" / "skill-b").exists()

    def test_removes_agent_md_file(self, tmp_path: Path) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        count = remove_components(
            "testplug", {"agents": {"reviewer"}}, claude_dir
        )
        assert count == 1
        assert not (install_dir / "agents" / "reviewer.md").exists()
        assert (install_dir / "agents" / "architect.md").exists()

    def test_removes_multiple_types(self, tmp_path: Path) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        count = remove_components(
            "testplug",
            {"skills": {"skill-a"}, "agents": {"architect"}},
            claude_dir,
        )
        assert count == 2

    def test_cleans_up_hook_deps(self, tmp_path: Path) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        remove_components("testplug", {"hooks": {"hooks"}}, claude_dir)
        assert not (install_dir / "scripts").exists()
        assert not (install_dir / "package.json").exists()

    def test_preserves_hook_deps_if_hooks_remain(
        self, tmp_path: Path
    ) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        remove_components("testplug", {"skills": {"skill-a"}}, claude_dir)
        assert (install_dir / "scripts").exists()
        assert (install_dir / "package.json").exists()

    def test_raises_if_not_installed(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "plugins").mkdir()
        with pytest.raises(ValueError, match="not installed"):
            remove_components("ghost", {"skills": {"x"}}, claude_dir)

    def test_noop_for_nonexistent_component(self, tmp_path: Path) -> None:
        claude_dir, install_dir = self._make_installed_plugin(tmp_path)
        count = remove_components(
            "testplug", {"skills": {"nonexistent"}}, claude_dir
        )
        assert count == 0


# ─── scan_installed_components ───────────────────────────────────


class TestScanInstalledComponents:
    def test_returns_empty_for_missing(self, tmp_path: Path) -> None:
        result = scan_installed_components(tmp_path, "ghost")
        assert result == {}

    def test_detects_skills_and_agents(self, tmp_path: Path) -> None:
        install_dir = (
            tmp_path / "plugins" / MARKETPLACE_NAME / "testplug"
        )
        install_dir.mkdir(parents=True)

        (install_dir / "skills" / "myskill").mkdir(parents=True)
        (install_dir / "skills" / "myskill" / "SKILL.md").write_text("#")

        (install_dir / "agents").mkdir()
        (install_dir / "agents" / "rev.md").write_text("#")

        result = scan_installed_components(tmp_path, "testplug")
        assert "myskill" in result["skills"]
        assert "rev" in result["agents"]
