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
    remove_from_selection,
    validate_plugin_name,
)
from pluck.installer import (
    get_installed_plugins,
    install_plugin,
    remove_components,
    scan_installed_components,
    uninstall_plugin,
)
from pluck.interactive import save_config as save_interactive_config
from pluck.repo import clone_or_update, discover_components
from pluck.tab_ui import interactive_remove, interactive_select, select_from_list

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

    env_switch = env_sub.add_parser("switch", help="Activate an environment (TUI if no name)")
    env_switch.add_argument("name", nargs="?", help="Environment to switch to")

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
        "uninstall", help="Uninstall plugins or remove specific components"
    )
    uninstall_p.add_argument("plugin_name", help="Plugin to uninstall")
    uninstall_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    uninstall_p.add_argument(
        "-t", "--type",
        choices=list(COMPONENT_TYPES),
        help="Component type to remove (requires -n or removes all of that type)",
    )
    uninstall_p.add_argument(
        "-n", "--name",
        help="Specific component name to remove (requires --type)",
    )
    uninstall_p.add_argument(
        "--all",
        action="store_true",
        dest="remove_all",
        help="Remove the entire plugin (default behavior without -t/-n)",
    )
    uninstall_p.add_argument(
        "--dry-run", action="store_true", help="Preview without removing"
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

    # --- model ---
    model_p = subparsers.add_parser("model", help="Manage model providers")
    model_sub = model_p.add_subparsers(dest="model_command", help="Available model actions")

    model_sub.add_parser("list", help="List available providers")

    model_sub.add_parser("current", help="Show current model configuration")

    model_switch = model_sub.add_parser("switch", help="Switch to a provider (TUI if no name)")
    model_switch.add_argument("provider", nargs="?", help="Provider name (e.g., zhipu, deepseek)")
    model_switch.add_argument(
        "--tier",
        choices=["opus", "sonnet", "haiku"],
        help="Model tier (defaults to provider's default)",
    )

    model_sub.add_parser("reset", help="Reset to default provider (anthropic)")

    model_add = model_sub.add_parser("add", help="Add a custom provider (interactive wizard)")
    model_add.add_argument("name", nargs="?", help="Provider identifier")
    model_add.add_argument("--display-name", help="Human-readable name")
    model_add.add_argument("--base-url", help="API base URL")
    model_add.add_argument(
        "--sonnet-model", help="Model ID for sonnet tier",
    )
    model_add.add_argument(
        "--haiku-model", help="Model ID for haiku tier",
    )
    model_add.add_argument(
        "--opus-model", help="Model ID for opus tier",
    )
    model_add.add_argument(
        "--default-tier",
        choices=["opus", "sonnet", "haiku"],
        default="sonnet",
        help="Default tier (default: sonnet)",
    )

    model_remove = model_sub.add_parser("remove", help="Remove a provider")
    model_remove.add_argument("name", help="Provider identifier to remove")

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
            "model": lambda: cmd_model(args),
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

    # Add from --repo URL, then reload so the new entry is visible
    if args.repo:
        _ensure_repo_in_config(args)
        config = load_config()

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
    """Handle 'uninstall' command — full plugin removal or component-level removal.

    Without flags: interactive TUI to select components for removal.
    With --all: full plugin uninstall (original behavior).
    With -t/-n: remove specific components non-interactively.
    """
    plugin_name = args.plugin_name

    # Check if plugin is installed
    installed = get_installed_plugins(claude_dir)
    if plugin_name not in installed:
        logger.error("Plugin '%s' is not installed", plugin_name)
        logger.info("To see installed plugins, run: pluck status")
        sys.exit(1)

    # --- Full uninstall (--all or no components left) ---
    has_component_flags = args.type or args.name
    if args.remove_all or not has_component_flags:
        # If --all is explicit, or no -t/-n flags: check if this is a
        # plain uninstall call (no component flags at all).
        if args.remove_all or not has_component_flags:
            # Plain uninstall or --all
            if has_component_flags and args.remove_all:
                # User said --all AND -t/-n — just do full uninstall
                pass
            elif not args.remove_all and not has_component_flags:
                # No flags at all → interactive TUI mode
                _interactive_uninstall(args, plugin_name, claude_dir)
                return

            # Full uninstall (original behavior)
            if not args.yes:
                logger.info("🗑️  Uninstalling: %s (entire plugin)", plugin_name)
                logger.info("   This will remove plugin files AND its entry from pluck.yaml")
                response = input("Are you sure? [y/N] ")
                if response.lower() != "y":
                    logger.info("Cancelled")
                    return

            logger.info("🗑️  Uninstalling: %s", plugin_name)
            uninstall_plugin(plugin_name, claude_dir)
            logger.info("  ✅ Done")
            return

    # --- Component-level removal ---
    _component_uninstall(args, plugin_name, claude_dir)


def _interactive_uninstall(
    args: argparse.Namespace, plugin_name: str, claude_dir: Path
) -> None:
    """Interactive TUI mode for selecting components to remove."""
    installed_set = scan_installed_components(claude_dir, plugin_name)
    total_installed = sum(len(v) for v in installed_set.values())

    if total_installed == 0:
        logger.warning("Plugin '%s' has no installed components", plugin_name)
        logger.info("Use: pluck uninstall %s --all  to remove the plugin entry", plugin_name)
        return

    to_remove = interactive_remove(plugin_name, installed_set)
    if to_remove is None:
        logger.info("Cancelled")
        return

    total_selected = sum(len(v) for v in to_remove.values())
    if total_selected == 0:
        logger.info("Nothing selected for removal")
        return

    # Check if all components selected → suggest full uninstall
    total_after = total_installed - total_selected
    if total_after == 0:
        logger.warning("All components selected for removal.")
        logger.info(
            "Consider using: pluck uninstall %s --all -y  "
            "(removes plugin entirely including config entry)",
            plugin_name,
        )

    if args.dry_run:
        _show_removal_plan(plugin_name, to_remove)
        return

    _show_removal_plan(plugin_name, to_remove)

    if not args.yes:
        response = input("Proceed with removal? [y/N] ")
        if response.lower() != "y":
            logger.info("Cancelled")
            return

    _execute_removal(args, plugin_name, to_remove, claude_dir)


def _component_uninstall(
    args: argparse.Namespace, plugin_name: str, claude_dir: Path
) -> None:
    """Non-interactive component removal via -t/-n flags."""
    to_remove: dict[str, set[str]] = {}

    if args.name:
        if not args.type:
            logger.error("--name requires --type")
            sys.exit(1)
        to_remove = {args.type: {args.name}}

    elif args.type:
        installed_set = scan_installed_components(claude_dir, plugin_name)
        items_of_type = installed_set.get(args.type, set())
        if not items_of_type:
            logger.info("No %s components installed for '%s'", args.type, plugin_name)
            return
        to_remove = {args.type: items_of_type}

    total = sum(len(v) for v in to_remove.values())
    if total == 0:
        logger.info("Nothing to remove")
        return

    if args.dry_run:
        _show_removal_plan(plugin_name, to_remove)
        return

    _show_removal_plan(plugin_name, to_remove)

    if not args.yes:
        response = input("Proceed with removal? [y/N] ")
        if response.lower() != "y":
            logger.info("Cancelled")
            return

    _execute_removal(args, plugin_name, to_remove, claude_dir)


def _show_removal_plan(
    plugin_name: str, to_remove: dict[str, set[str]]
) -> None:
    """Display what will be removed."""
    logger.info("Removing from '%s':", plugin_name)
    for comp_type in sorted(to_remove):
        for name in sorted(to_remove[comp_type]):
            logger.info("  - %s/%s", comp_type, name)


def _execute_removal(
    args: argparse.Namespace,
    plugin_name: str,
    to_remove: dict[str, set[str]],
    claude_dir: Path,
) -> None:
    """Execute component removal and update config."""
    removed_count = remove_components(plugin_name, to_remove, claude_dir)

    # Update pluck.yaml
    config = load_config()
    plugin_cfg = _find_plugin_config(config, plugin_name)
    if plugin_cfg is not None:
        installed_set = scan_installed_components(claude_dir, plugin_name)
        for comp_type, names in to_remove.items():
            current = plugin_cfg["components"].get(comp_type, [])
            all_items = sorted(installed_set.get(comp_type, set()) | names)
            plugin_cfg["components"][comp_type] = remove_from_selection(
                current, names, all_items=all_items
            )
        save_interactive_config(get_default_config_path(), config["plugins"])
        logger.info("  Config updated")

    logger.info("  ✅ Removed %d component(s) from '%s'", removed_count, plugin_name)

    # Warn if plugin is now empty
    remaining = scan_installed_components(claude_dir, plugin_name)
    if not any(remaining.values()):
        logger.warning(
            "Plugin '%s' has no remaining components. "
            "Consider: pluck uninstall %s --all",
            plugin_name, plugin_name,
        )


def _find_plugin_config(
    config: dict[str, Any], name: str
) -> dict[str, Any] | None:
    """Find a plugin's config entry by name (case-insensitive)."""
    name_lower = name.lower()
    for p in config["plugins"]:
        if p["name"].lower() == name_lower:
            return p
    return None


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

        installed_set = scan_installed_components(claude_dir, plugin["name"])

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
        name = args.name
        if not name:
            # TUI selection
            current = get_current_env()
            current_name = current["name"] if current else DEFAULT_ENV_NAME
            env_names = [DEFAULT_ENV_NAME] + [e["name"] for e in list_envs()]
            name = select_from_list(env_names, current=current_name, title="Select environment")
            if name is None:
                return

        try:
            cmd = switch_env_command(name)
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


def cmd_model(args: argparse.Namespace) -> None:
    """Handle 'model' command — unified model provider management."""
    from pluck.model import (
        get_current_model,
        list_providers,
        remove_provider,
        reset_to_default,
        switch_provider,
    )

    if args.model_command == "list":
        list_providers()

    elif args.model_command == "current":
        current = get_current_model()
        logger.info("Current model configuration:\n")
        logger.info("  Provider: %s", current["provider"])
        logger.info("  Tier:     %s", current["model"])
        logger.info("  Base URL: %s", current["base_url"])
        logger.info("  Model:    %s", current["anthropic_model"])

    elif args.model_command == "switch":
        provider_name = args.provider
        if not provider_name:
            # TUI selection
            from pluck.providers import list_providers as get_all_providers

            providers = get_all_providers()
            if not providers:
                logger.info("No providers configured. Use 'pluck model add' first.")
                return

            current = get_current_model()
            names = [p.name for p in sorted(providers, key=lambda p: p.name)]
            provider_name = select_from_list(
                names, current=current["provider"], title="Select model provider",
            )
            if provider_name is None:
                return

        try:
            switch_provider(provider_name, args.tier)
            logger.info(
                "\n✅ Switched model. Restart Claude Code to apply changes."
            )
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)

    elif args.model_command == "reset":
        try:
            reset_to_default()
            logger.info(
                "\n✅ Reset to Anthropic. Restart Claude Code to apply changes."
            )
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)

    elif args.model_command == "add":
        # Interactive wizard (default) or CLI flags for scripting
        has_cli_flags = args.base_url and (
            args.sonnet_model or args.haiku_model or args.opus_model
        )
        if args.name and has_cli_flags:
            _add_provider_cli(args)
        else:
            _add_provider_wizard(args)

    elif args.model_command == "remove":
        try:
            remove_provider(args.name)
            logger.info("✅ Removed provider '%s'", args.name)
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)

    else:
        logger.error(
            "Unknown model action. Available: list, current, switch, reset, add, remove"
        )
        sys.exit(1)


def _add_provider_cli(args: argparse.Namespace) -> None:
    """Non-interactive provider add via CLI flags (for scripting)."""
    from pluck.model import ModelTier, ProviderConfig, add_provider

    models: dict[str, ModelTier] = {}
    if args.sonnet_model:
        models["sonnet"] = ModelTier(id=args.sonnet_model)
    if args.haiku_model:
        models["haiku"] = ModelTier(id=args.haiku_model)
    if args.opus_model:
        models["opus"] = ModelTier(id=args.opus_model)

    if not models:
        logger.error("At least one model tier is required (--sonnet-model, --haiku-model, or --opus-model)")
        sys.exit(1)

    display_name = args.display_name or args.name
    config = ProviderConfig(
        name=args.name,
        display_name=display_name,
        base_url=args.base_url,
        models=models,
        default_tier=args.default_tier,
    )

    try:
        add_provider(config)
        logger.info("✅ Added provider '%s'", args.name)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)


def _add_provider_wizard(args: argparse.Namespace) -> None:
    """Interactive wizard for adding a model provider."""
    import getpass

    from pluck.model import ModelTier, ProviderConfig, add_provider

    is_tty = sys.stdin.isatty()

    print("\n📦 Add a new model provider\n")

    # --- Name ---
    if args.name:
        name = args.name
    else:
        while True:
            name = input("  Provider name: ").strip()
            if not name:
                print("  Provider name is required.")
                continue
            if " " in name or not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
                print("  ❌ Invalid name. Use only letters, digits, '-', '_', '.' (no spaces).")
                continue
            break

    # --- Display name ---
    default_display = name.capitalize()
    display_prompt = f"  Display name [{default_display}]: "
    display_input = input(display_prompt).strip() if is_tty else ""
    display_name = display_input or default_display

    # --- Base URL ---
    base_url = args.base_url or input("  API Base URL: ").strip()
    if not base_url:
        logger.error("API Base URL is required")
        sys.exit(1)

    # --- Auth token ---
    token = None
    if is_tty:
        while True:
            token_input = getpass.getpass(
                "  API Token (hidden, Enter to skip): "
            ).strip()
            if not token_input:
                break
            if token_input.startswith(("http://", "https://")):
                print(
                    f"  ⚠️  That looks like a URL, not an API token. "
                    f"API tokens are usually short strings or JWT tokens. "
                    f"Use 'pluck model add --base-url {token_input} ...' to set the base URL."
                )
                retry = input("  Enter token anyway? [y/N] ").strip().lower()
                if retry != "y":
                    continue
            token = token_input
            break

    # --- Model tiers (all 3 required) ---
    print("\n  Model tiers (all required):\n")
    models: dict[str, ModelTier] = {}
    tier_order = [
        ("opus", "Opus model ID"),
        ("sonnet", "Sonnet model ID"),
        ("haiku", "Haiku model ID"),
    ]

    if is_tty:
        for tier_key, label in tier_order:
            while True:
                model_id = input(f"    {label}: ").strip()
                if model_id:
                    break
                print(f"      {tier_key} tier is required. Please enter a model ID.")
            models[tier_key] = ModelTier(id=model_id)
        print()
    elif not models:
        logger.error("At least one model tier is required")
        sys.exit(1)

    # --- Default tier ---
    if is_tty and len(models) > 1:
        tier_list = "/".join(models.keys())
        default_tier_choice = next(
            (t for t in ("sonnet", "haiku", "opus") if t in models), list(models.keys())[0],
        )
        df_prompt = f"  Default tier ({tier_list}) [{default_tier_choice}]: "
        choice = input(df_prompt).strip().lower()
        default_tier = choice or default_tier_choice
    else:
        default_tier = list(models.keys())[0]

    # --- Confirm ---
    print(f"\n  Provider name:     {name}")
    print(f"  Display name:      {display_name}")
    print(f"  API Base URL:      {base_url}")
    print(f"  API Token:         {'****' if token else '(none)'}")
    print(f"  Models:            {', '.join(f'{t}={m.id}' for t, m in models.items())}")
    print(f"  Default tier:      {default_tier}\n")

    if is_tty:
        confirm = input("  Confirm? [Y/n] ").strip().lower()
        if confirm and confirm != "y":
            print("  Cancelled.")
            return

    config = ProviderConfig(
        name=name,
        display_name=display_name,
        base_url=base_url,
        models=models,
        default_tier=default_tier,
        auth_token=token,
    )

    try:
        add_provider(config)
        logger.info(
            "✅ Added provider '%s' to ~/.config/pluck/providers.yaml", name,
        )
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)



