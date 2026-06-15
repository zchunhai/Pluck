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
from pluck.repo import discover_components, get_commit_sha, resolve_component_paths, COMPONENT_SEARCH_PATHS, _find_component

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
    target escapes the allowed base directory or if a symlink is
    encountered in the path components (prevents traversal attacks).
    """
    # Resolve without following the final symlink, then walk every
    # component from the target upward to detect symlink-based
    # directory traversal attacks.
    resolved = target.resolve()
    base_resolved = base.resolve()

    check = resolved
    while check != check.parent:
        if check.is_symlink():
            raise ValueError(
                f"Symlink detected in path (potential traversal): {check}"
            )
        if check == base_resolved:
            break
        check = check.parent

    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(
            f"Path escapes allowed directory: {target} -> {resolved}"
        ) from None
    return resolved


def _get_plugin_source_from_marketplace(repo_dir: Path, plugin_name: str) -> Path:
    """Read marketplace.json and extract the source directory for a plugin.

    Args:
        repo_dir: Path to the cloned repository.
        plugin_name: Name of the plugin to find in marketplace.json.

    Returns:
        Path to the plugin source directory (relative to repo_dir).
        Returns repo_dir if no marketplace.json or if source is "./".
    """
    marketplace_file = repo_dir / ".claude-plugin" / "marketplace.json"
    if not marketplace_file.exists():
        logger.debug("No marketplace.json found in %s, using repo root", repo_dir)
        return repo_dir

    try:
        with open(marketplace_file, encoding="utf-8") as f:
            marketplace = cast(dict[str, Any], json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read marketplace.json: %s", e)
        return repo_dir

    # Find the plugin entry in the plugins array
    plugins = marketplace.get("plugins", [])

    # Try exact match first (for when plugin name matches marketplace entry)
    for plugin in plugins:
        if plugin.get("name") == plugin_name:
            source = plugin.get("source", "./")
            # Normalize the source path
            if source == "./" or source == ".":
                logger.debug("Plugin '%s' source is root", plugin_name)
                return repo_dir
            elif source.startswith("./"):
                plugin_dir = repo_dir / source[2:]  # Remove leading "./"
                if plugin_dir.is_dir():
                    logger.debug("Plugin '%s' source: %s", plugin_name, source)
                    return plugin_dir
                else:
                    logger.warning(
                        "Plugin source directory not found: %s, using repo root",
                        plugin_dir
                    )
                    return repo_dir
            else:
                logger.warning("Invalid source path '%s', using repo root", source)
                return repo_dir

    # No exact match: use the first plugin entry (handles renamed plugins via -p)
    if plugins:
        first_plugin = plugins[0]
        actual_name = first_plugin.get("name", "unknown")
        source = first_plugin.get("source", "./")
        logger.debug(
            "Plugin '%s' not found in marketplace, using first entry '%s'",
            plugin_name, actual_name
        )
        # Normalize the source path
        if source == "./" or source == ".":
            logger.debug("First plugin source is root")
            return repo_dir
        elif source.startswith("./"):
            plugin_dir = repo_dir / source[2:]  # Remove leading "./"
            if plugin_dir.is_dir():
                logger.debug("Using first plugin source: %s", source)
                return plugin_dir
            else:
                logger.warning(
                    "First plugin source directory not found: %s, using repo root",
                    plugin_dir
                )
                return repo_dir
        else:
            logger.warning("Invalid source path '%s', using repo root", source)
            return repo_dir

    logger.debug("No plugins in marketplace.json, using repo root")
    return repo_dir


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
    org = plugin_config.get("org", "Unknown")  # Get org for author fallback

    # Adjust repo_dir based on marketplace.json source field
    repo_dir = _get_plugin_source_from_marketplace(repo_dir, name)

    _check_conflicts(name, claude_config_dir)

    plugins_base = (claude_config_dir / "plugins" / MARKETPLACE_NAME).resolve()
    install_dir = _ensure_path_within_base(
        get_install_dir(name, claude_config_dir), plugins_base
    )

    if install_dir.exists():
        shutil.rmtree(install_dir)

    install_dir.mkdir(parents=True, exist_ok=True)

    _create_plugin_manifest(install_dir, name, repo_dir, org)
    _create_marketplace_manifest(install_dir, name, claude_config_dir, org)
    _copy_plugin_metadata(repo_dir, install_dir)

    installed_count = 0
    for comp_type, selection in components.items():
        if not selection:
            continue

        source_paths = resolve_component_paths(repo_dir, comp_type, selection)
        if not source_paths:
            continue

        # Build name->path mapping. For "all", resolve_component_paths
        # discovers names internally; for lists, names are in selection.
        if selection == "all":
            all_components = discover_components(repo_dir)
            names = all_components.get(comp_type, [])
        else:
            names = list(selection)

        # Create a mapping from leaf names to full names for lookup
        path_to_name: dict[str, str] = {}
        for comp_name in names:
            source = _find_component(repo_dir, COMPONENT_SEARCH_PATHS[comp_type], comp_type, comp_name)
            if source is not None:
                path_to_name[str(source.resolve())] = comp_name

        # Rules go to ~/.claude/rules/<plugin>/ (so Claude Code can auto-load them)
        # Everything else goes into the plugin install directory
        if comp_type == "rules":
            target_dir = claude_config_dir / "rules" / name
        else:
            target_subdir = COMPONENT_DIR_MAP.get(comp_type, comp_type)
            target_dir = install_dir / target_subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        for source_path in source_paths:
            # Use the full component name (preserving nested path) as dest name
            comp_name = path_to_name.get(str(source_path.resolve()), source_path.name)
            dest = target_dir / comp_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source_path, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, dest)
            installed_count += 1
            logger.info("  Installed: %s/%s", comp_type, comp_name)

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


def _create_plugin_manifest(install_dir: Path, name: str, repo_dir: Path, org: str) -> None:
    """Create .claude-plugin/plugin.json for the filtered plugin.

    Args:
        install_dir: Target installation directory.
        name: Plugin name.
        repo_dir: Source repository directory.
        org: Repository organization (used as author fallback).
    """
    original = _read_original_manifest(repo_dir)

    # Ensure author field has proper structure with name
    author = original.get("author")
    if not author or not isinstance(author, dict):
        author = {"name": org}
    elif "name" not in author or not author["name"]:
        # Author field exists but missing name or name is empty
        author = dict(author)  # Make a copy to avoid mutating original
        author["name"] = org

    manifest = {
        "name": name,
        "description": original.get(
            "description", f"Pluck-managed selective installation of {name}"
        ),
        "version": original.get("version", "0.1.0"),
        "author": author,
    }

    manifest_dir = install_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_dir / "plugin.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _create_marketplace_manifest(
    install_dir: Path, name: str, claude_config_dir: Path, org: str
) -> None:
    """Create or update the marketplace manifest for pluck.

    Claude Code discovers local marketplaces via:
      <marketplace-dir>/.claude-plugin/marketplace.json

    Each plugin entry needs a ``source`` field pointing to the plugin
    subdirectory relative to the marketplace root.

    Args:
        install_dir: Target installation directory.
        name: Plugin name.
        claude_config_dir: Claude config directory.
        org: Repository organization (used as author fallback).

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
    plugin_exists = False
    plugin_idx = None
    for i, p in enumerate(marketplace.get("plugins", [])):
        if p.get("name") == name:
            plugin_exists = True
            plugin_idx = i
            break

    # Build plugin entry with correct source path
    plugin_manifest = install_dir / ".claude-plugin" / "plugin.json"
    if plugin_manifest.exists():
        with open(plugin_manifest, encoding="utf-8") as f:
            plugin_data = cast(dict[str, Any], json.load(f))
    else:
        plugin_data = {"name": name}

    # Ensure author field has proper structure with name
    author = plugin_data.get("author")
    if not author or not isinstance(author, dict):
        plugin_data["author"] = {"name": org}
    elif "name" not in author or not author["name"]:
        plugin_data["author"] = dict(author)
        plugin_data["author"]["name"] = org

    # source is relative to the marketplace root (plugins/pluck/)
    plugin_data["source"] = f"./{name}"

    if plugin_idx is not None:
        # Update existing entry to fix source format and author
        marketplace["plugins"][plugin_idx] = plugin_data
    else:
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

    # Also remove rules from ~/.claude(-envs/<env>)/rules/<plugin>/
    rules_dir = claude_config_dir / "rules" / name_lower
    if rules_dir.exists():
        shutil.rmtree(rules_dir)
        logger.info("Removed rules directory: %s", rules_dir)

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


def scan_installed_components(
    claude_dir: Path, plugin_name: str
) -> dict[str, set[str]]:
    """Scan the installed plugin directory and return what's actually on disk.

    For skills, recursively searches to find nested directory structures.
    For rules, scans ~/.claude/rules/<plugin>/ (the auto-load path).
    """
    plugin_name = validate_plugin_name(plugin_name)
    install_dir = get_install_dir(plugin_name, claude_dir)
    result: dict[str, set[str]] = {}

    if not install_dir.exists() and not (claude_dir / "rules" / plugin_name).exists():
        return result

    dir_to_type = {
        "skills": "skills",
        "agents": "agents",
        "commands": "commands",
        "rules": "rules",
        "hooks": "hooks",
        "contexts": "contexts",
    }

    def scan_skills_recursive(directory: Path, prefix: str = "") -> set[str]:
        """Recursively scan for skill directories containing SKILL.md."""
        items: set[str] = set()
        for child in sorted(directory.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                # Check if this is a skill (contains SKILL.md)
                if (child / "SKILL.md").exists():
                    skill_name = f"{prefix}{child.name}" if prefix else child.name
                    items.add(skill_name)
                else:
                    # Recursively search subdirectories
                    new_prefix = f"{prefix}{child.name}/" if prefix else f"{child.name}/"
                    items.update(scan_skills_recursive(child, new_prefix))
        return items

    for dir_name, comp_type in dir_to_type.items():
        if comp_type == "rules":
            # Rules are installed to ~/.claude/rules/<plugin>/
            comp_dir = claude_dir / "rules" / plugin_name
        else:
            comp_dir = install_dir / dir_name

        if not comp_dir.exists():
            continue

        if comp_type == "skills":
            # Use recursive scan for skills
            result[comp_type] = scan_skills_recursive(comp_dir)
        else:
            # Single-level scan for other component types
            items: set[str] = set()
            for child in comp_dir.iterdir():
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    items.add(child.name)
                elif child.is_file() and child.suffix == ".md":
                    if comp_type in ("agents", "commands"):
                        items.add(child.stem)
                    else:
                        items.add(child.name)
                elif child.is_file():
                    items.add(child.name)
            result[comp_type] = items

    return result


def _resolve_installed_component_path(
    install_dir: Path, comp_type: str, name: str, claude_dir: Path | None = None
) -> Path | None:
    """Find the filesystem path of an installed component.

    For rules, looks in ``claude_dir/rules/<plugin>/`` (auto-load path).
    For other types, looks in the plugin install directory.

    Returns ``None`` if the component does not exist on disk.
    """
    if comp_type == "rules" and claude_dir is not None:
        plugin_name = install_dir.name  # install_dir ends with plugin name
        base = claude_dir / "rules" / plugin_name
    else:
        subdir = COMPONENT_DIR_MAP.get(comp_type, comp_type)
        base = install_dir / subdir

    if comp_type == "skills":
        candidate = base / name
        if candidate.is_dir():
            return candidate
    elif comp_type in ("agents", "commands"):
        candidate = base / f"{name}.md"
        if candidate.is_file():
            return candidate
    elif comp_type == "hooks":
        if name == "hooks" and (base / "hooks.json").exists():
            return base
    elif comp_type in ("rules", "contexts"):
        candidate = base / name
        if candidate.exists():
            return candidate

    return None


def remove_components(
    plugin_name: str,
    to_remove: dict[str, set[str]],
    claude_config_dir: Path | None = None,
) -> int:
    """Remove specific components from an installed plugin.

    Parameters
    ----------
    plugin_name:
        Name of the installed plugin.
    to_remove:
        Mapping of component type -> set of component names to remove.
    claude_config_dir:
        Claude config directory.

    Returns
    -------
    int
        Number of components actually removed from disk.
    """
    claude_config_dir = claude_config_dir or get_claude_config_dir()
    name = validate_plugin_name(plugin_name)

    plugins_base = (claude_config_dir / "plugins" / MARKETPLACE_NAME).resolve()
    install_dir = _ensure_path_within_base(
        get_install_dir(name, claude_config_dir), plugins_base
    )

    if not install_dir.exists():
        raise ValueError(f"Plugin '{name}' is not installed")

    removed_count = 0

    for comp_type, names in to_remove.items():
        for comp_name in sorted(names):
            path = _resolve_installed_component_path(install_dir, comp_type, comp_name, claude_config_dir)
            if path is None:
                logger.warning("  Not found on disk, skipping: %s/%s", comp_type, comp_name)
                continue

            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            logger.info("  Removed: %s/%s", comp_type, comp_name)
            removed_count += 1

    # Clean up hook dependencies when all hooks are removed
    if "hooks" in to_remove and to_remove["hooks"]:
        remaining = scan_installed_components(claude_config_dir, name)
        if not remaining.get("hooks"):
            for dep in ("scripts", "lib", "node_modules"):
                dep_path = install_dir / dep
                if dep_path.exists():
                    shutil.rmtree(dep_path)
                    logger.info("  Cleaned up dependency: %s/", dep)
            for fname in ("package.json", "package-lock.json"):
                fp = install_dir / fname
                if fp.exists():
                    fp.unlink()
                    logger.info("  Cleaned up dependency: %s", fname)

    logger.info("Removed %d component(s) from '%s'", removed_count, name)
    return removed_count
