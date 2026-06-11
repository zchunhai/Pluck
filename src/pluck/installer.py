"""Plugin creation, registration, and uninstall for pluck."""

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pluck.config import MARKETPLACE_NAME, get_claude_config_dir, get_install_dir
from pluck.repo import discover_components, get_commit_sha, resolve_component_paths

logger = logging.getLogger(__name__)

# Maps component types to their directory names in the plugin structure
COMPONENT_DIR_MAP = {
    "skills": "skills",
    "agents": "agents",
    "commands": "commands",
    "rules": "rules",
    "hooks": "hooks",
    "contexts": "contexts",
}


def _atomic_write_json(path: Path, data: dict, indent: int = 2) -> None:
    """Write JSON atomically: write to temp file, then os.replace.

    Prevents corruption from concurrent writes and crash mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file
    if path.exists():
        backup = path.with_suffix(".json.bak")
        shutil.copy2(path, backup)

    # Write to temp file in same directory (same filesystem for os.replace)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".pluck_", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

        raise


def _safe_load_json(path: Path) -> dict[str, Any]:
    """Load JSON file with fallback to backup on corruption."""
    try:
        with open(path, encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))
    except json.JSONDecodeError:
        backup = path.with_suffix(".json.bak")
        if backup.exists():
            logger.warning("Corrupted JSON: %s, restoring from backup", path)
            with open(backup, encoding="utf-8") as f:
                return cast(dict[str, Any], json.load(f))
        raise


def install_plugin(
    plugin_config: dict[str, Any],
    repo_dir: Path,
    claude_config_dir: Path | None = None,
) -> Path:
    """Install a plugin with selected components.

    Creates a filtered plugin directory containing only the selected components,
    then registers it in Claude's plugin system.

    Returns:
        Path to the installed plugin directory.
    """
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    name = plugin_config["name"]
    components = plugin_config["components"]

    _check_conflicts(name, claude_config_dir)

    install_dir = get_install_dir(name, claude_config_dir)

    if install_dir.exists():
        shutil.rmtree(install_dir)

    install_dir.mkdir(parents=True, exist_ok=True)

    _create_plugin_manifest(install_dir, name, repo_dir)
    _copy_plugin_metadata(repo_dir, install_dir)

    installed_count = 0
    for comp_type, selection in components.items():
        if not selection:
            continue

        source_paths = resolve_component_paths(repo_dir, comp_type, selection)
        if not source_paths:
            continue

        target_subdir = COMPONENT_DIR_MAP.get(comp_type, comp_type)
        target_dir = install_dir / target_subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        for source_path in source_paths:
            dest = target_dir / source_path.name
            if source_path.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source_path, dest)
            else:
                shutil.copy2(source_path, dest)
            installed_count += 1
            logger.info("  Installed: %s/%s", comp_type, source_path.name)

    _warn_missing_components(repo_dir, components)

    if components.get("hooks"):
        _copy_hook_dependencies(repo_dir, install_dir)

    if components.get("agents") or components.get("skills"):
        _copy_agent_yaml(repo_dir, install_dir)

    logger.info("Installed %d components for plugin '%s'", installed_count, name)

    commit_sha = _get_repo_sha(repo_dir)
    _register_plugin(name, install_dir, commit_sha, claude_config_dir)

    return install_dir


def _warn_missing_components(repo_dir: Path, components: dict[str, Any]) -> None:
    """Warn about component names in config that were not found in the repo."""
    available = discover_components(repo_dir)
    for comp_type, selection in components.items():
        if not selection or selection == "all":
            continue
        available_set = set(available.get(comp_type, []))
        missing = [s for s in selection if s not in available_set]
        if missing:
            logger.warning(
                "  ⚠ %s not found in repo: %s", comp_type, ", ".join(missing)
            )


def _check_conflicts(name: str, claude_config_dir: Path) -> None:
    """Warn if the same plugin is already installed from a different marketplace."""
    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"
    if not plugins_file.exists():
        return

    with open(plugins_file, encoding="utf-8") as f:
        data = json.load(f)

    for key in data.get("plugins", {}):
        if key.startswith(f"{name}@") and not key.endswith(f"@{MARKETPLACE_NAME}"):
            logger.warning(
                "  ⚠️  Plugin '%s' already installed as '%s'. "
                "Consider disabling it to avoid conflicts.",
                name,
                key,
            )


def _create_plugin_manifest(install_dir: Path, name: str, repo_dir: Path) -> None:
    """Create .claude-plugin/plugin.json for the filtered plugin."""
    original = _read_original_manifest(repo_dir)

    manifest = {
        "name": name,
        "description": original.get(
            "description", f"Pluck-managed selective installation of {name}"
        ),
        "version": original.get("version", "0.1.0"),
        "author": original.get("author", {}),
    }

    manifest_dir = install_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_dir / "plugin.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    marketplace = {
        "name": f"{name}-pluck",
        "description": "Pluck-managed selective installation",
        "owner": {"name": "pluck"},
        "plugins": [
            {
                "name": name,
                "description": manifest["description"],
                "version": manifest["version"],
                "source": "./",
                "author": manifest["author"],
            }
        ],
    }

    with open(manifest_dir / "marketplace.json", "w", encoding="utf-8") as f:
        json.dump(marketplace, f, indent=2, ensure_ascii=False)


def _read_original_manifest(repo_dir: Path) -> dict[str, Any]:
    """Try to read the original plugin manifest from the repo."""
    candidates = [
        repo_dir / ".claude-plugin" / "plugin.json",
        repo_dir / "package.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            try:
                with open(candidate, encoding="utf-8") as f:
                    return cast(dict[str, Any], json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
    return {}


def _copy_plugin_metadata(repo_dir: Path, install_dir: Path) -> None:
    """Copy plugin-level metadata files (CLAUDE.md, AGENTS.md, etc.)."""
    metadata_files = ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]
    for filename in metadata_files:
        source = repo_dir / filename
        if source.is_file():
            shutil.copy2(source, install_dir / filename)


def _copy_hook_dependencies(repo_dir: Path, install_dir: Path) -> None:
    """Copy scripts/ and other files needed by hooks.

    Copies essential hook infrastructure (scripts, lib, package.json).
    Runs npm install --production instead of copying node_modules.
    """
    for dep_name in ["scripts", "lib"]:
        source = repo_dir / dep_name
        if source.is_dir():
            dest = install_dir / dep_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)

    pkg_json = repo_dir / "package.json"
    if pkg_json.is_file():
        shutil.copy2(pkg_json, install_dir / "package.json")

    pkg_lock = repo_dir / "package-lock.json"
    if pkg_lock.is_file():
        shutil.copy2(pkg_lock, install_dir / "package-lock.json")

    if (install_dir / "package.json").exists() and not (
        install_dir / "node_modules"
    ).exists():
        try:
            subprocess.run(
                ["npm", "install", "--production", "--no-audit", "--no-fund"],
                cwd=str(install_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("  npm install failed; hooks may not work fully")


def _copy_agent_yaml(repo_dir: Path, install_dir: Path) -> None:
    """Copy agent.yaml if it exists (used by some plugins for discovery)."""
    agent_yaml = repo_dir / "agent.yaml"
    if agent_yaml.is_file():
        shutil.copy2(agent_yaml, install_dir / "agent.yaml")


def _get_repo_sha(repo_dir: Path) -> str:
    """Get short commit SHA from repo."""
    try:
        return get_commit_sha(repo_dir)
    except RuntimeError:
        return "unknown"


def _register_plugin(
    name: str,
    install_dir: Path,
    commit_sha: str,
    claude_config_dir: Path,
) -> None:
    """Register the plugin in installed_plugins.json and settings.json."""
    plugin_key = f"{name}@{MARKETPLACE_NAME}"
    now = datetime.now(timezone.utc).isoformat()

    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"
    _update_installed_plugins(plugins_file, plugin_key, install_dir, commit_sha, now)

    settings_file = claude_config_dir / "settings.json"
    _update_settings(settings_file, plugin_key)

    logger.info("Registered plugin: %s", plugin_key)


def _update_installed_plugins(
    plugins_file: Path,
    plugin_key: str,
    install_dir: Path,
    commit_sha: str,
    timestamp: str,
) -> None:
    """Update the installed_plugins.json registry."""
    if plugins_file.exists():
        data = _safe_load_json(plugins_file)
    else:
        data = {"version": 2, "plugins": {}}

    entry = {
        "scope": "user",
        "installPath": str(install_dir),
        "version": "pluck-selected",
        "installedAt": timestamp,
        "lastUpdated": timestamp,
        "gitCommitSha": commit_sha,
        "managedBy": "pluck",
    }

    data["plugins"][plugin_key] = [entry]
    _atomic_write_json(plugins_file, data)


def _update_settings(settings_file: Path, plugin_key: str) -> None:
    """Update settings.json to enable the plugin."""
    if not settings_file.exists():
        logger.warning("settings.json not found at %s", settings_file)
        return

    data = _safe_load_json(settings_file)

    if "enabledPlugins" not in data:
        data["enabledPlugins"] = {}

    data["enabledPlugins"][plugin_key] = True
    _atomic_write_json(settings_file, data)


def uninstall_plugin(name: str, claude_config_dir: Path | None = None) -> bool:
    """Uninstall a pluck-managed plugin by name."""
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    plugin_key = f"{name}@{MARKETPLACE_NAME}"

    install_dir = get_install_dir(name, claude_config_dir).parent
    if install_dir.exists():
        shutil.rmtree(install_dir)
        logger.info("Removed plugin directory: %s", install_dir)

    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"
    if plugins_file.exists():
        data = _safe_load_json(plugins_file)
        if plugin_key in data.get("plugins", {}):
            del data["plugins"][plugin_key]
            _atomic_write_json(plugins_file, data)
            logger.info("Removed from registry: %s", plugin_key)

    settings_file = claude_config_dir / "settings.json"
    if settings_file.exists():
        data = _safe_load_json(settings_file)
        if plugin_key in data.get("enabledPlugins", {}):
            del data["enabledPlugins"][plugin_key]
            _atomic_write_json(settings_file, data)
            logger.info("Removed from settings: %s", plugin_key)

    return True


def get_installed_plugins(
    claude_config_dir: Path | None = None,
) -> dict[str, Any]:
    """Get all pluck-managed plugins from the registry."""
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"

    if not plugins_file.exists():
        return {}

    data = _safe_load_json(plugins_file)

    pluck_plugins: dict[str, Any] = {}
    for key, entries in data.get("plugins", {}).items():
        if key.endswith(f"@{MARKETPLACE_NAME}"):
            name = key[: -len(f"@{MARKETPLACE_NAME}")]
            pluck_plugins[name] = entries

    return pluck_plugins
