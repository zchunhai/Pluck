"""Plugin creation, registration, and uninstall for pluck."""

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pluck.config import (
    CONFIG_FILE_NAME,
    MARKETPLACE_NAME,
    get_claude_config_dir,
    get_install_dir,
    validate_plugin_name,
)
from pluck.io_utils import atomic_write_json, safe_load_json
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


def _ensure_path_within_base(target: Path, base: Path) -> Path:
    """Resolve ``target`` and verify it lies within ``base``.

    Returns the resolved path.  Raises ``ValueError`` if the resolved
    target escapes the allowed base directory.
    """
    resolved = target.resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        raise ValueError(
            f"Path escapes allowed directory: {target} -> {resolved}"
        ) from None
    return resolved


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
    name = validate_plugin_name(plugin_config["name"])
    components = plugin_config["components"]

    _check_conflicts(name, claude_config_dir)

    plugins_base = (claude_config_dir / "plugins" / MARKETPLACE_NAME).resolve()
    install_dir = _ensure_path_within_base(
        get_install_dir(name, claude_config_dir), plugins_base
    )

    if install_dir.exists():
        shutil.rmtree(install_dir)

    install_dir.mkdir(parents=True, exist_ok=True)

    _create_plugin_manifest(install_dir, name, repo_dir)
    _create_marketplace_manifest(install_dir, name, claude_config_dir)
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


def _create_marketplace_manifest(
    install_dir: Path, name: str, claude_config_dir: Path
) -> None:
    """Create or update the marketplace manifest for pluck.

    Claude Code discovers local marketplaces via:
      <marketplace-dir>/.claude-plugin/marketplace.json

    Each plugin entry needs a ``source`` field pointing to the plugin
    subdirectory relative to the marketplace root.

    Structure:
      plugins/pluck/                       ← marketplace root
      ├── .claude-plugin/
      │   └── marketplace.json             ← THIS file
      └── <plugin>/                        ← plugin source = "./<plugin>"
    """
    marketplace_dir = claude_config_dir / "plugins" / MARKETPLACE_NAME
    claude_plugin_dir = marketplace_dir / ".claude-plugin"
    marketplace_file = claude_plugin_dir / "marketplace.json"

    marketplace_dir.mkdir(parents=True, exist_ok=True)
    claude_plugin_dir.mkdir(parents=True, exist_ok=True)

    # Clean up old-style marketplace.json at wrong location (pre-2.1.x compat)
    old_marketplace = marketplace_dir / "marketplace.json"
    if old_marketplace.exists():
        old_marketplace.unlink()

    # Read existing marketplace.json if it exists
    if marketplace_file.exists():
        try:
            with open(marketplace_file, encoding="utf-8") as f:
                marketplace = cast(dict[str, Any], json.load(f))
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupted marketplace.json, starting fresh")
            marketplace = _new_marketplace_template()
    else:
        marketplace = _new_marketplace_template()

    # Check if this plugin is already in the marketplace list
    plugin_exists = any(
        p.get("name") == name for p in marketplace.get("plugins", [])
    )

    if not plugin_exists:
        # Build plugin entry with correct source path
        plugin_manifest = install_dir / ".claude-plugin" / "plugin.json"
        if plugin_manifest.exists():
            with open(plugin_manifest, encoding="utf-8") as f:
                plugin_data = cast(dict[str, Any], json.load(f))
        else:
            plugin_data = {"name": name}

        # source is relative to the marketplace root (plugins/pluck/)
        plugin_data["source"] = f"./{name}"

        marketplace.setdefault("plugins", []).append(plugin_data)
        atomic_write_json(marketplace_file, marketplace)

    # Register the marketplace in Claude's global settings so that
    # /plugins can discover it. Claude stores local marketplace
    # registrations in ~/.claude/settings.json under extraKnownMarketplaces.
    _register_marketplace_with_claude(marketplace_dir)


def _register_marketplace_with_claude(marketplace_dir: Path) -> None:
    """Ensure the pluck marketplace is registered in Claude's settings.

    Adds an entry to ``extraKnownMarketplaces`` in both the user's global
    ``~/.claude/settings.json`` and the project-level config (when
    ``CLAUDE_CONFIG_DIR`` is set) so that ``/plugins`` can discover it.

    Tries the CLI first, falls back to direct JSON editing.
    """
    global_settings = Path.home() / ".claude" / "settings.json"

    # --- Preferred path: use Claude's own CLI (idempotent) ---
    try:
        result = subprocess.run(
            ["claude", "plugin", "marketplace", "add", str(marketplace_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.debug("Marketplace 'pluck' registered via CLI")
            return
        logger.debug("CLI registration failed: %s", result.stderr.strip())
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.debug("Cannot invoke Claude CLI: %s", exc)

    # --- Fallback: edit settings.json directly ---
    # When CLAUDE_CONFIG_DIR is set, also write to project-level settings
    settings_paths = [global_settings]
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        project_settings = Path(config_dir) / "settings.json"
        if project_settings != global_settings:
            settings_paths.append(project_settings)

    entry = {
        "source": {
            "source": "directory",
            "path": str(marketplace_dir),
        }
    }

    for settings_path in settings_paths:
        if not settings_path.exists():
            continue
        data = safe_load_json(settings_path)
        marketplaces = data.setdefault("extraKnownMarketplaces", {})

        if MARKETPLACE_NAME not in marketplaces:
            marketplaces[MARKETPLACE_NAME] = entry
            atomic_write_json(settings_path, data)
            logger.info(
                "Marketplace 'pluck' registered in %s", settings_path
            )


def _new_marketplace_template() -> dict[str, Any]:
    """Return a fresh marketplace manifest template."""
    return {
        "name": MARKETPLACE_NAME,
        "description": "Pluck-managed plugins",
        "owner": {"name": "pluck"},
        "plugins": [],
    }


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
        logger.warning("Could not determine git SHA for %s", repo_dir)
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
        data = safe_load_json(plugins_file)
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
    atomic_write_json(plugins_file, data)


def _update_settings(settings_file: Path, plugin_key: str) -> None:
    """Update settings.json to enable the plugin."""
    if not settings_file.exists():
        # Create minimal settings.json if it doesn't exist
        data = {"enabledPlugins": {plugin_key: True}}
        atomic_write_json(settings_file, data)
        logger.info("Created settings.json with %s enabled", plugin_key)
        return

    data = safe_load_json(settings_file)

    if "enabledPlugins" not in data:
        data["enabledPlugins"] = {}

    data["enabledPlugins"][plugin_key] = True
    atomic_write_json(settings_file, data)


def _find_key_case_insensitive(
    mapping: dict[str, Any], name_lower: str, marketplace: str
) -> str | None:
    """Find a key like ``<name>@<marketplace>`` with case-insensitive name match.

    Returns the actual key (preserving original case), or ``None``.
    """
    suffix = f"@{marketplace}"
    for key in mapping:
        if key.lower() == f"{name_lower}{suffix}":
            return key
    return None


def uninstall_plugin(name: str, claude_config_dir: Path | None = None) -> bool:
    """Uninstall a pluck-managed plugin by name.

    Matching is case-insensitive — ``pluck uninstall ecc`` and
    ``pluck uninstall ECC`` both match a plugin registered as ``ECC@pluck``.
    """
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    name_lower = validate_plugin_name(name)

    plugins_base = (claude_config_dir / "plugins" / MARKETPLACE_NAME).resolve()
    install_dir = _ensure_path_within_base(
        get_install_dir(name_lower, claude_config_dir), plugins_base
    )
    if install_dir.exists():
        shutil.rmtree(install_dir)
        logger.info("Removed plugin directory: %s", install_dir)

    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"
    if plugins_file.exists():
        data = safe_load_json(plugins_file)
        # Case-insensitive key lookup
        actual_key = _find_key_case_insensitive(
            data.get("plugins", {}), name_lower, MARKETPLACE_NAME
        )
        if actual_key:
            del data["plugins"][actual_key]
            atomic_write_json(plugins_file, data)
            logger.info("Removed from registry: %s", actual_key)

    settings_file = claude_config_dir / "settings.json"
    if settings_file.exists():
        data = safe_load_json(settings_file)
        actual_key = _find_key_case_insensitive(
            data.get("enabledPlugins", {}), name_lower, MARKETPLACE_NAME
        )
        if actual_key:
            del data["enabledPlugins"][actual_key]
            atomic_write_json(settings_file, data)
            logger.info("Removed from settings: %s", actual_key)

    # Remove from marketplace manifest
    marketplace_file = (
        claude_config_dir / "plugins" / MARKETPLACE_NAME
        / ".claude-plugin" / "marketplace.json"
    )
    if marketplace_file.exists():
        mkt = safe_load_json(marketplace_file)
        mkt["plugins"] = [
            p for p in mkt.get("plugins", [])
            if p.get("name", "").lower() != name_lower
        ]
        atomic_write_json(marketplace_file, mkt)
        logger.info("Removed from marketplace: %s", name)

    # Remove from pluck.yaml config
    _remove_from_config(name_lower, claude_config_dir)

    return True


def _remove_from_config(name_lower: str, claude_config_dir: Path) -> None:
    """Remove a plugin entry from pluck.yaml (case-insensitive)."""
    from pluck.io_utils import atomic_write

    config_path = claude_config_dir / CONFIG_FILE_NAME
    if not config_path.exists():
        return

    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as exc:
        logger.debug("Could not read config for uninstall cleanup: %s", exc)
        return

    if not config or "plugins" not in config:
        return

    plugins = config["plugins"]
    original_len = len(plugins)
    config["plugins"] = [
        p for p in plugins if p.get("name", "").lower() != name_lower
    ]

    if len(config["plugins"]) < original_len:
        content = yaml.safe_dump(
            config, default_flow_style=False, allow_unicode=True
        )
        atomic_write(config_path, content)
        logger.info("Removed from config: %s", name_lower)


def get_installed_plugins(
    claude_config_dir: Path | None = None,
) -> dict[str, Any]:
    """Get all pluck-managed plugins from the registry."""
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    plugins_file = claude_config_dir / "plugins" / "installed_plugins.json"

    if not plugins_file.exists():
        return {}

    data = safe_load_json(plugins_file)

    pluck_plugins: dict[str, Any] = {}
    for key, entries in data.get("plugins", {}).items():
        if key.endswith(f"@{MARKETPLACE_NAME}"):
            name = key[: -len(f"@{MARKETPLACE_NAME}")]
            pluck_plugins[name] = entries

    return pluck_plugins
