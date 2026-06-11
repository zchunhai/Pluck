"""CLI entry point for pluck - Selective Claude plugin installer.

Usage:
    pluck install              # Install all configured plugins
    pluck install -p ecc       # Install only the 'ecc' plugin
    pluck update               # Update repos and reinstall
    pluck uninstall ecc        # Uninstall a pluck-managed plugin
    pluck list                 # List available components
    pluck status               # Show installation status
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, cast

from pluck.config import (
    COMPONENT_TYPES,
    ensure_config_file,
    get_claude_config_dir,
    get_default_config_path,
    get_install_dir,
    get_repos_dir,
    load_config,
)
from pluck.installer import (
    get_installed_plugins,
    install_plugin,
    uninstall_plugin,
)
from pluck.interactive import interactive_select
from pluck.interactive import save_config as save_interactive_config
from pluck.repo import clone_or_update, discover_components

logger = logging.getLogger("pluck")


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="pluck",
        description="Selective Claude plugin installer",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- install ---
    install_p = subparsers.add_parser("install", help="Install plugins from config")
    install_p.add_argument("-p", "--plugin", help="Install only a specific plugin")
    install_p.add_argument(
        "--repo",
        help="Add a plugin from repo URL (auto-added to config if not present)",
    )
    install_p.add_argument("--branch", default="main", help="Branch for --repo")
    install_p.add_argument(
        "--all",
        action="store_true",
        dest="install_all",
        help="With --repo: install all components",
    )
    install_p.add_argument(
        "--dry-run", action="store_true", help="Preview without installing"
    )

    # --- update ---
    update_p = subparsers.add_parser("update", help="Update repos and reinstall")
    update_p.add_argument("-p", "--plugin", help="Update only a specific plugin")

    # --- uninstall ---
    uninstall_p = subparsers.add_parser(
        "uninstall", help="Uninstall pluck-managed plugins"
    )
    uninstall_p.add_argument("plugin_name", nargs="?", help="Plugin to uninstall")

    # --- select ---
    select_p = subparsers.add_parser("select", help="Interactively select components")
    select_p.add_argument("-p", "--plugin", help="Select for a specific plugin only")
    select_p.add_argument(
        "--install", action="store_true", help="Install after selecting"
    )

    # --- list ---
    list_p = subparsers.add_parser("list", help="List available components in repos")
    list_p.add_argument("-p", "--plugin", help="List for a specific plugin")
    list_p.add_argument(
        "-t",
        "--type",
        choices=["skills", "agents", "commands", "rules", "hooks"],
        help="Filter by component type",
    )

    # --- status ---
    subparsers.add_parser("status", help="Show installation status")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    if not args.command:
        parser.print_help()
        sys.exit(1)

    claude_dir = get_claude_config_dir()
    ensure_config_file(get_default_config_path())
    logger.debug("Claude config dir: %s", claude_dir)

    try:
        handlers = {
            "install": lambda: cmd_install(args, claude_dir),
            "update": lambda: cmd_update(args, claude_dir),
            "uninstall": lambda: cmd_uninstall(args, claude_dir),
            "select": lambda: cmd_select(args, claude_dir),
            "list": lambda: cmd_list(args, claude_dir),
            "status": lambda: cmd_status(args, claude_dir),
        }
        handlers[args.command]()
    except Exception as e:
        logger.error("Error: %s", e)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


def _extract_plugin_name(repo_url: str) -> str:
    """Extract plugin name from a git repo URL.

    Examples:
        https://github.com/obra/superpowers.git -> superpowers
        git@github.com:obra/superpowers.git      -> superpowers
    """
    clean = repo_url.rstrip("/").removesuffix(".git")
    name = clean.rsplit("/", maxsplit=1)[-1]
    if ":" in name:
        name = name.rsplit(":", maxsplit=1)[-1]
    return name


def _ensure_repo_in_config(args: argparse.Namespace) -> None:
    """Add a plugin from --repo URL to config file if not already present."""
    name = args.plugin or _extract_plugin_name(args.repo)
    config_path = get_default_config_path()

    config = load_config()

    for existing in config["plugins"]:
        if existing["name"] == name:
            logger.info("Plugin '%s' already in config, using existing entry", name)
            return

    components: dict[str, list[str] | str] = (
        {t: "all" for t in COMPONENT_TYPES}
        if args.install_all
        else {t: [] for t in COMPONENT_TYPES}
    )

    new_plugin = {
        "name": name,
        "repo": args.repo,
        "branch": args.branch,
        "components": components,
    }

    config["plugins"].append(new_plugin)

    from pluck.interactive import save_config

    save_config(config_path, config["plugins"])

    action = "all components" if args.install_all else "empty selection"
    logger.info("Added '%s' to config (%s)", name, action)


def cmd_install(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'install' command.

    With --repo only (no --all): adds to config, runs interactive selection,
    then installs. With --repo --all: adds to config with all components,
    then installs. Without --repo: installs plugins already in config.
    """
    if args.repo:
        _ensure_repo_in_config(args)

    config = load_config()
    repos_dir = get_repos_dir(claude_dir)

    for plugin in _filter_plugins(config, args.plugin):
        logger.info("📦 Processing: %s", plugin["name"])

        repo_dir = repos_dir / plugin["name"]
        try:
            sha = clone_or_update(plugin["repo"], repo_dir, plugin["branch"])
            logger.info("  Repo at commit: %s", sha)
        except RuntimeError as e:
            logger.error("  Failed: %s", e)
            continue

        # --repo without --all: interactive selection for the new plugin
        is_repo_plugin = args.repo and (
            plugin["name"] == _extract_plugin_name(args.repo)
            or (args.plugin and plugin["name"] == args.plugin)
        )
        if is_repo_plugin and not args.install_all:
            logger.info("  Select components interactively (Enter = skip)...")
            new_components = interactive_select(
                plugin["name"], repo_dir, plugin["components"]
            )
            if new_components != plugin["components"]:
                plugin["components"] = new_components
                save_interactive_config(get_default_config_path(), config["plugins"])
                logger.info("  ✅ Selection saved for '%s'", plugin["name"])

        if args.dry_run:
            _show_dry_run(plugin, repo_dir)
        else:
            install_plugin(plugin, repo_dir, claude_dir)
            logger.info("  ✅ '%s' installed\n", plugin["name"])


def cmd_update(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'update' command."""
    config = load_config()
    repos_dir = get_repos_dir(claude_dir)

    for plugin in _filter_plugins(config, args.plugin):
        logger.info("🔄 Updating: %s", plugin["name"])

        repo_dir = repos_dir / plugin["name"]
        try:
            sha = clone_or_update(plugin["repo"], repo_dir, plugin["branch"])
            logger.info("  Updated to: %s", sha)
        except RuntimeError as e:
            logger.error("  Failed: %s", e)
            continue

        install_plugin(plugin, repo_dir, claude_dir)
        logger.info("  ✅ '%s' reinstalled\n", plugin["name"])


def cmd_uninstall(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'uninstall' command."""
    if args.plugin_name:
        logger.info("🗑️  Uninstalling: %s", args.plugin_name)
        uninstall_plugin(args.plugin_name, claude_dir)
        logger.info("  ✅ Done")
    else:
        installed = get_installed_plugins(claude_dir)
        if not installed:
            logger.info("No pluck-managed plugins to uninstall")
            return
        for name in installed:
            logger.info("🗑️  Uninstalling: %s", name)
            uninstall_plugin(name, claude_dir)
        logger.info("  ✅ All pluck plugins uninstalled")


def cmd_select(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'select' command - interactive component selection."""
    config = load_config()
    repos_dir = get_repos_dir(claude_dir)
    changed = False

    for plugin in _filter_plugins(config, args.plugin):
        name = plugin["name"]
        logger.info("📦 %s: discovering components...", name)

        repo_dir = repos_dir / name
        try:
            sha = clone_or_update(plugin["repo"], repo_dir, plugin["branch"])
            logger.info("  Repo at commit: %s", sha)
        except RuntimeError as e:
            logger.error("  Failed: %s", e)
            continue

        old_components = plugin["components"]
        new_components = interactive_select(name, repo_dir, old_components)

        if new_components != old_components:
            plugin["components"] = new_components
            changed = True
            logger.info("  ✅ Selection updated for '%s'", name)
        else:
            logger.info("  No changes for '%s'", name)

    if changed:
        save_interactive_config(get_default_config_path(), config["plugins"])
        logger.info("")

        if args.install:
            logger.info("Installing updated selections...")
            for plugin in _filter_plugins(config, args.plugin):
                repo_dir = repos_dir / plugin["name"]
                install_plugin(plugin, repo_dir, claude_dir)
                logger.info("  ✅ '%s' installed\n", plugin["name"])
    else:
        logger.info("No changes to save.")


def cmd_list(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'list' command with three-state display."""
    config = load_config()
    repos_dir = get_repos_dir(claude_dir)

    for plugin in _filter_plugins(config, args.plugin):
        repo_dir = repos_dir / plugin["name"]
        if not repo_dir.exists():
            logger.info(
                "📦 %s: not cloned yet. Run 'pluck install' first.",
                plugin["name"],
            )
            continue

        installed_set = _scan_installed_components(claude_dir, plugin["name"])

        logger.info("📦 %s", plugin["name"])
        components = discover_components(repo_dir)

        for comp_type, items in components.items():
            if args.type and comp_type != args.type:
                continue
            if not items:
                continue

            selection = plugin["components"].get(comp_type, [])
            configured_items = (
                items if selection == "all" else [s for s in selection if s in items]
            )

            installed_count = sum(
                1
                for item in configured_items
                if _is_installed(installed_set, comp_type, item)
            )

            if installed_count == len(configured_items) and configured_items:
                status = f"✅ {installed_count}/{len(configured_items)} installed"
            elif installed_count > 0:
                status = f"⚠️  {installed_count}/{len(configured_items)} installed"
            elif configured_items:
                status = f"⬜ {len(configured_items)} configured (not installed)"
            else:
                status = "⬜ none"

            logger.info("  %s %s [%d available]", status, comp_type, len(items))
            for item in items:
                in_config = selection == "all" or item in selection
                on_disk = _is_installed(installed_set, comp_type, item)

                if in_config and on_disk:
                    marker = "  ✓"
                elif in_config and not on_disk:
                    marker = "  ⚠"
                else:
                    marker = "  ·"
                logger.info("    %s %s", marker, item)
        logger.info("")


def cmd_status(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'status' command."""
    installed = get_installed_plugins(claude_dir)

    if not installed:
        logger.info("No pluck-managed plugins installed")
        return

    logger.info("Pluck-managed plugins:\n")
    for name, entries in installed.items():
        for entry in entries:
            logger.info("  📦 %s@pluck", name)
            install_path = entry.get("installPath", "?")
            logger.info("     Path:    %s", install_path)
            logger.info("     Updated: %s", entry.get("lastUpdated", "?"))
            logger.info("     Commit:  %s", entry.get("gitCommitSha", "?"))

            path = Path(install_path) if install_path != "?" else None
            if path and path.exists():
                for comp_dir_name in ("skills", "agents", "commands", "rules", "hooks"):
                    comp_dir = path / comp_dir_name
                    if comp_dir.exists():
                        count = len(list(comp_dir.iterdir()))
                        if count:
                            logger.info("     %s: %d items", comp_dir_name, count)
        logger.info("")


def _scan_installed_components(
    claude_dir: Path, plugin_name: str
) -> dict[str, set[str]]:
    """Scan the installed plugin directory and return what's actually on disk."""
    install_dir = get_install_dir(plugin_name, claude_dir)
    result: dict[str, set[str]] = {}

    if not install_dir.exists():
        return result

    dir_to_type = {
        "skills": "skills",
        "agents": "agents",
        "commands": "commands",
        "rules": "rules",
        "hooks": "hooks",
        "contexts": "contexts",
    }

    for dir_name, comp_type in dir_to_type.items():
        comp_dir = install_dir / dir_name
        if not comp_dir.exists():
            continue
        items: set[str] = set()
        for child in comp_dir.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                items.add(child.name)
            elif child.is_file() and child.suffix == ".md":
                items.add(child.stem)
            elif child.is_file():
                items.add(child.name)
        result[comp_type] = items

    return result


def _is_installed(
    installed_set: dict[str, set[str]], comp_type: str, item_name: str
) -> bool:
    """Check if a specific component is present in the installed set."""
    return item_name in installed_set.get(comp_type, set())


def _filter_plugins(
    config: dict[str, Any], name_filter: str | None
) -> list[dict[str, Any]]:
    """Filter plugins by name if a filter is specified."""
    if not name_filter:
        return cast(list[dict[str, Any]], config["plugins"])
    return [p for p in config["plugins"] if p["name"] == name_filter]


def _show_dry_run(plugin_config: dict[str, Any], repo_dir: Path) -> None:
    """Preview what would be installed."""
    components = discover_components(repo_dir)

    logger.info("  Would install:")
    for comp_type, selection in plugin_config["components"].items():
        if not selection:
            continue

        available = components.get(comp_type, [])
        if selection == "all":
            items = available
        else:
            items = [s for s in selection if s in available]

        if items:
            logger.info("    %s (%d): %s", comp_type, len(items), ", ".join(items))
        else:
            logger.info("    %s: no matching components found", comp_type)
