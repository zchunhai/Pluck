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
import signal
import sys
import types
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
    validate_plugin_name,
)
from pluck.installer import (
    get_installed_plugins,
    install_plugin,
    uninstall_plugin,
)
from pluck.interactive import save_config as save_interactive_config
from pluck.repo import clone_or_update, discover_components
from pluck.tab_ui import interactive_select

logger = logging.getLogger("pluck")


def _sigint_handler(signum: int, frame: types.FrameType | None) -> None:
    """Handle Ctrl+C gracefully."""
    sys.exit(130)


def main() -> None:
    """Main CLI entry point."""
    signal.signal(signal.SIGINT, _sigint_handler)

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

    # --- env ---
    env_p = subparsers.add_parser("env", help="Manage isolated environments")
    env_sub = env_p.add_subparsers(dest="env_command", help="Available env actions")

    env_create = env_sub.add_parser("create", help="Create a new environment")
    env_create.add_argument("name", help="Environment name")
    env_create.add_argument(
        "--path",
        help="Custom directory path (default: ~/.claude-envs/<name>)",
    )

    env_sub.add_parser("list", help="List all environments")

    env_switch = env_sub.add_parser("switch", help="Activate an environment")
    env_switch.add_argument("name", help="Environment to switch to")

    env_init = env_sub.add_parser("init", help="Generate shell wrapper for auto-switching")
    env_init.add_argument(
        "--shell", choices=["zsh", "bash"], default="zsh",
        help="Target shell (default: zsh)",
    )

    env_delete = env_sub.add_parser("delete", help="Delete an environment")
    env_delete.add_argument("name", help="Environment to delete")

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
        "--yes", "-y",
        action="store_true",
        help="Non-interactive mode: skip component selection, use config as-is",
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
    uninstall_p.add_argument("plugin_name", help="Plugin to uninstall")
    uninstall_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )

    # --- list ---
    list_p = subparsers.add_parser("list", help="List available components in repos")
    list_p.add_argument("-p", "--plugin", help="List for a specific plugin")
    list_p.add_argument(
        "-t",
        "--type",
        choices=list(COMPONENT_TYPES),
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
            "env": lambda: cmd_env(args, claude_dir),
            "install": lambda: cmd_install(args, claude_dir),
            "update": lambda: cmd_update(args, claude_dir),
            "uninstall": lambda: cmd_uninstall(args, claude_dir),
            "list": lambda: cmd_list(args, claude_dir),
            "status": lambda: cmd_status(args, claude_dir),
        }
        handlers[args.command]()
    except (ValueError, RuntimeError, ImportError, OSError) as e:
        logger.error("%s: %s", type(e).__name__, e)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


def _validate_repo_url(url: str) -> str:
    """Validate that a URL looks like a valid git repository URL."""
    url = url.strip()
    valid_prefixes = ("https://", "http://", "git://", "git@", "ssh://")
    if not any(url.startswith(p) for p in valid_prefixes):
        raise ValueError(
            f"Invalid repo URL: {url!r}. "
            f"Expected https://, git://, git@host:user/repo, or ssh:// format."
        )
    return url


def _extract_plugin_name(repo_url: str) -> str:
    """Extract plugin name from a git repo URL (always lowercased).

    Examples:
        https://github.com/affaan-m/ECC.git     -> ecc
        https://github.com/obra/superpowers.git -> superpowers
        git@github.com:obra/superpowers.git      -> superpowers
    """
    clean = repo_url.rstrip("/").removesuffix(".git")
    name = clean.rsplit("/", maxsplit=1)[-1]
    if ":" in name:
        name = name.rsplit(":", maxsplit=1)[-1]
    return name.lower()


def _ensure_repo_in_config(args: argparse.Namespace) -> None:
    """Add a plugin from --repo URL to config file if not already present."""
    _validate_repo_url(args.repo)
    name = validate_plugin_name(
        args.plugin or _extract_plugin_name(args.repo)
    )
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

    With no args: interactive selection for each configured plugin, then install.
    With --repo only (no --all): adds to config, runs interactive selection,
    then installs. With --repo --all: adds to config with all components,
    then installs.
    """
    config = load_config()

    # Add from --repo URL
    if args.repo:
        _ensure_repo_in_config(args)

    repos_dir = get_repos_dir(claude_dir)
    changed = False

    # Install phase
    for plugin in _filter_plugins(config, args.plugin):
        logger.info("📦 Processing: %s", plugin["name"])

        repo_dir = repos_dir / plugin["name"]
        try:
            sha = clone_or_update(plugin["repo"], repo_dir, plugin["branch"])
            logger.info("  Repo at commit: %s", sha)
        except RuntimeError as e:
            logger.error("  Failed: %s", e)
            continue

        # Interactive selection: default on, skip with --yes or --repo --all
        is_repo_plugin = args.repo and (
            plugin["name"] == _extract_plugin_name(args.repo)
            or (args.plugin and plugin["name"] == args.plugin)
        )
        skip_interact = args.yes or (is_repo_plugin and args.install_all)

        if not skip_interact:
            logger.info(
                "  Interactive selection:\n"
                "    [Tab] switch type  [Space] toggle  [a] all  [Enter] confirm\n"
                "    (or use: pluck install -y for non-interactive)"
            )
            new_components = interactive_select(
                plugin["name"], repo_dir, plugin["components"]
            )
            if new_components is None:
                logger.info("  ⚠ Aborted, skipping install for '%s'", plugin["name"])
                continue
            if new_components != plugin["components"]:
                plugin["components"] = new_components
                changed = True
                logger.info("  ✅ Selection updated for '%s'", plugin["name"])
            else:
                logger.info("  No changes for '%s'", plugin["name"])

        if args.dry_run:
            _show_dry_run(plugin, repo_dir)
        else:
            install_plugin(plugin, repo_dir, claude_dir)
            logger.info("  ✅ '%s' installed\n", plugin["name"])

    # Save config if any selections changed
    if changed:
        save_interactive_config(get_default_config_path(), config["plugins"])
        logger.info("✅ Selection saved.\n")


def cmd_update(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'update' command — delegates to install in non-interactive mode."""
    args.repo = None
    args.install_all = False
    args.dry_run = False
    args.yes = True
    cmd_install(args, claude_dir)


def cmd_uninstall(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'uninstall' command."""
    plugin_name = args.plugin_name

    # Check if plugin is installed
    installed = get_installed_plugins(claude_dir)
    if plugin_name not in installed:
        logger.error("Plugin '%s' is not installed", plugin_name)
        logger.info("To see installed plugins, run: pluck status")
        sys.exit(1)

    # Confirmation prompt
    if not args.yes:
        logger.info("🗑️  Uninstalling: %s", plugin_name)
        logger.info("   This will remove plugin files AND its entry from pluck.yaml")
        response = input("Are you sure? [y/N] ")
        if response.lower() != "y":
            logger.info("Cancelled")
            return

    logger.info("🗑️  Uninstalling: %s", plugin_name)
    uninstall_plugin(plugin_name, claude_dir)
    logger.info("  ✅ Done")


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
    """Handle 'status' command — show active environment and installed plugins."""
    from pluck.env import get_current_env

    # Show active environment first
    current = get_current_env()
    if current:
        logger.info("Environment: %s", current["name"])
        logger.info("Path:       %s", current["path"])
        logger.info("")
    else:
        default_dir = Path.home() / ".claude"
        if claude_dir != default_dir.resolve():
            # User has CLAUDE_CONFIG_DIR set but it's not a registered env
            logger.info("Environment: custom (not managed by pluck)")
            logger.info("Path:       %s", claude_dir)
            logger.info("")
        else:
            logger.info("Environment: default (~/.claude/)")
            logger.info("")

    # Show installed plugins
    installed = get_installed_plugins(claude_dir)

    if not installed:
        logger.info("No pluck-managed plugins installed")
        return

    logger.info("Plugins:\n")
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


def cmd_env(args: argparse.Namespace, claude_dir: Path) -> None:
    """Handle 'env' command — environment management."""
    from pluck.env import (
        DEFAULT_ENV_DIR,
        DEFAULT_ENV_NAME,
        create_env,
        delete_env,
        get_current_env,
        init_command,
        list_envs,
        switch_env_command,
    )

    if args.env_command == "create":
        env_path = Path(args.path) if args.path else None
        try:
            created = create_env(args.name, path=env_path)
            logger.info("Created environment '%s' at: %s", args.name, created)
            # Output the switch command to stdout so that
            #   eval "$(pluck env create <name>)"
            # creates AND activates in one step.
            sys.stdout.write(switch_env_command(args.name) + "\n")
            sys.stdout.flush()
        except ValueError as e:
            logger.error("Cannot create environment: %s", e)
            sys.exit(1)

    elif args.env_command == "list":
        current = get_current_env()
        current_path = str(current["path"]) if current else None
        # Default env is active when no pluck-managed env is current
        default_active = current is None

        logger.info("Environments:")
        marker = "*" if default_active else " "
        logger.info(
            "  %s %-20s %s  (default)",
            marker, DEFAULT_ENV_NAME, DEFAULT_ENV_DIR,
        )

        for env in list_envs():
            active = "*" if env["path"] == current_path else " "
            logger.info("  %s %-20s %s", active, env["name"], env["path"])

    elif args.env_command == "switch":
        if not args.name:
            logger.error("Environment name required. Use 'default' to switch back.")
            return
        try:
            cmd = switch_env_command(args.name)
            sys.stdout.write(cmd + "\n")
            sys.stdout.flush()
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)

    elif args.env_command == "init":
        cmd = init_command(args.shell)
        sys.stdout.write(cmd + "\n")
        sys.stdout.flush()

    elif args.env_command == "delete":
        name = args.name
        current = get_current_env()
        active_match = current and current["name"].lower() == name.lower()

        if active_match:
            logger.info(
                "⚠️  '%s' is the active environment. It will be "
                "switched to default before deletion.", name
            )
        else:
            logger.info("⚠️  This will delete environment '%s' and its contents.", name)

        response = input("Are you sure? [y/N] ")
        if response.lower() != "y":
            logger.info("Cancelled")
            return

        # If active, switch to default first
        if active_match:
            sys.stdout.write(switch_env_command(DEFAULT_ENV_NAME) + "\n")
            sys.stdout.flush()

        try:
            delete_env(name)
            logger.info("Deleted environment '%s'", name)
        except ValueError as e:
            logger.error("Cannot delete: %s", e)
            sys.exit(1)

    else:
        logger.error(
            "Unknown env action. Available: create, list, switch, init, delete"
        )


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
                # agents and commands use stem (no .md); contexts and rules
                # keep the full filename to match discovery naming.
                if comp_type in ("agents", "commands"):
                    items.add(child.stem)
                else:
                    items.add(child.name)
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
