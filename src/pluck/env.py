"""Environment management for pluck — isolated Claude Code environments.

Each environment is a self-contained Claude config directory with its own
plugins, settings, memory, and rules.  Switching an environment sets the
``CLAUDE_CONFIG_DIR`` environment variable, which all existing pluck
commands already respect via ``get_claude_config_dir()``.

Usage::

    pluck env create coding
    eval "$(pluck env switch coding)"
    pluck env current
    pluck env list
    eval "$(pluck env deactivate)"
    pluck env delete coding
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict, cast

from pluck.config import validate_plugin_name
from pluck.io_utils import atomic_write_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Configurable via PLUCK_CONFIG_DIR env var; defaults to XDG_CONFIG_HOME/pluck
def _get_pluck_dir() -> Path:
    """Return the pluck config directory, respecting environment variables."""
    custom = os.environ.get("PLUCK_CONFIG_DIR")
    if custom:
        return Path(custom)
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "pluck"


_ENV_REGISTRY_DIR = _get_pluck_dir()
ENV_REGISTRY_PATH = _ENV_REGISTRY_DIR / "environments.json"


def _refresh_paths() -> None:
    """Re-read PLUCK_CONFIG_DIR env var to update paths.

    Call this in test setup after setting the env var.
    """
    global _ENV_REGISTRY_DIR, ENV_REGISTRY_PATH
    _ENV_REGISTRY_DIR = _get_pluck_dir()
    ENV_REGISTRY_PATH = _ENV_REGISTRY_DIR / "environments.json"

# Default directory where environments are created (virtualenvwrapper-style).
DEFAULT_ENV_HOME = Path.home() / ".claude-envs"


class EnvironmentEntry(TypedDict):
    """Schema for a single environment in the registry."""

    name: str
    path: str
    created_at: str
    description: str


# ---------------------------------------------------------------------------
# Registry: load / save
# ---------------------------------------------------------------------------


def _load_registry() -> list[EnvironmentEntry]:
    """Load environment registry, creating an empty one if missing.

    Returns an empty list on first run or after recovering from corruption.
    """
    registry_path = ENV_REGISTRY_PATH
    if not registry_path.exists():
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {"version": 1, "environments": []}
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return []

    try:
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.warning("Corrupted registry at %s, starting fresh", registry_path)
        data = {"version": 1, "environments": []}

    environments = data.get("environments", [])
    if not isinstance(environments, list):
        logger.warning("Malformed registry, resetting")
        environments = []

    return cast("list[EnvironmentEntry]", environments)


def _save_registry(environments: list[EnvironmentEntry]) -> None:
    """Atomically write the environment registry to disk."""
    data: dict[str, Any] = {"version": 1, "environments": environments}
    atomic_write_json(ENV_REGISTRY_PATH, data)


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

_SKELETON_FILES: dict[str, str] = {
    "pluck.yaml": "plugins: []\n",
    "settings.json": '{"enabledPlugins": {}}',
    "plugins/installed_plugins.json": '{"version": 2, "plugins": {}}',
}

_SKELETON_DIRS = ["memory", "plugins"]


def _create_skeleton(env_dir: Path) -> None:
    """Populate a new environment directory with the minimal skeleton.

    Creates the files and directories a fresh Claude Code config needs
    so that ``pluck install`` works immediately after switching.
    """
    env_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, content in _SKELETON_FILES.items():
        full = env_dir / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    # Empty CLAUDE.md as a user-editable marker
    (env_dir / "CLAUDE.md").touch()

    for dir_name in _SKELETON_DIRS:
        (env_dir / dir_name).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_env(
    name: str,
    path: Path | None = None,
) -> Path:
    """Create a new isolated Claude Code environment.

    Parameters
    ----------
    name:
        Short name for the environment (validated like a plugin name).
    path:
        Custom directory path.  Defaults to ``~/.claude-envs/<name>``.

    Returns
    -------
    Path
        The created environment directory.

    Raises
    ------
    ValueError
        If the name is invalid, already exists, or the target directory
        already exists and is non-empty.
    """
    name = validate_plugin_name(name)  # reuse: no path separators, safe chars

    env_dir = Path(path) if path else (DEFAULT_ENV_HOME / name)
    env_dir = env_dir.resolve()

    # Check registry for duplicate name
    environments = _load_registry()
    for entry in environments:
        if entry["name"].lower() == name.lower():
            raise ValueError(
                f"Environment '{name}' already exists. "
                f"Use a different name or delete it first."
            )
        if Path(entry["path"]).resolve() == env_dir:
            raise ValueError(
                f"Directory '{env_dir}' is already registered as "
                f"environment '{entry['name']}'."
            )

    # Check for non-empty existing directory
    if env_dir.exists():
        contents = list(env_dir.iterdir())
        if contents:
            raise ValueError(
                f"Directory '{env_dir}' already exists and is not empty."
            )

    _create_skeleton(env_dir)

    entry: EnvironmentEntry = {
        "name": name,
        "path": str(env_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "",
    }
    environments.append(entry)
    _save_registry(environments)

    logger.debug("Registered environment '%s' at %s", name, env_dir)
    return env_dir


def delete_env(name: str) -> None:
    """Delete an environment's directory and remove it from the registry.

    Callers should handle the case where the target environment is currently
    active (e.g., switch to default first).

    Raises
    ------
    ValueError
        If the environment is not found.
    """
    name_lower = name.lower()
    environments = _load_registry()

    idx: int | None = None
    for i, entry in enumerate(environments):
        if entry["name"].lower() == name_lower:
            idx = i
            break

    if idx is None:
        raise ValueError(f"Environment not found: '{name}'")

    entry = environments[idx]
    env_path = Path(entry["path"])

    if env_path.exists():
        shutil.rmtree(env_path)
        logger.info("Removed environment directory: %s", env_path)

    del environments[idx]
    _save_registry(environments)
    logger.info("Removed '%s' from registry", entry["name"])


def list_envs() -> list[EnvironmentEntry]:
    """Return all registered environments sorted by creation time (newest first)."""
    environments = _load_registry()
    environments.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return environments


def get_current_env() -> EnvironmentEntry | None:
    """Detect the currently active environment from ``CLAUDE_CONFIG_DIR``.

    Returns ``None`` when no pluck-managed environment is active.
    """
    active_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not active_dir:
        return None

    resolved_active = Path(active_dir).resolve()
    for entry in _load_registry():
        if Path(entry["path"]).resolve() == resolved_active:
            return entry
    return None


def get_env_path(name: str) -> Path | None:
    """Resolve an environment's directory path by name.

    Matching is case-insensitive.
    """
    name_lower = name.lower()
    for entry in _load_registry():
        if entry["name"].lower() == name_lower:
            return Path(entry["path"])
    return None


DEFAULT_ENV_NAME = "default"
DEFAULT_ENV_DIR = Path.home() / ".claude"


def switch_env_command(name: str) -> str:
    """Generate the shell command to activate an environment.

    Special name ``"default"`` switches back to ``~/.claude/`` (deactivates).

    The returned string is suitable for ``eval "$(pluck env switch <name>)"``::

        export CLAUDE_CONFIG_DIR="/path/to/env"; echo "Activated ..."

    Raises
    ------
    ValueError
        If the named environment is not found.
    """
    if name.lower() == DEFAULT_ENV_NAME:
        default_dir = DEFAULT_ENV_DIR
        return (
            "unset CLAUDE_CONFIG_DIR;"
            f' echo "🔌 Switched to default environment ({default_dir})"'
        )

    env_path = get_env_path(name)
    if env_path is None:
        raise ValueError(
            f"Environment not found: '{name}'. "
            f"Available: default, " +
            ", ".join(e["name"] for e in _load_registry())
        )

    safe_path = shlex.quote(str(env_path))
    return (
        f"export CLAUDE_CONFIG_DIR={safe_path};"
        f' echo "🔌 Activated environment: {name} ({env_path})"'
    )


def init_command(shell: str = "zsh") -> str:
    """Generate a shell wrapper function for seamless env switching.

    When added to ``~/.zshrc`` (or ``~/.bashrc``), the wrapper intercepts
    ``pluck env create``, ``pluck env switch``, and ``pluck env deactivate``
    and automatically ``eval``s their output so the user never needs to
    type ``eval "$(...)"`` manually.

    Usage::

        # One-time setup
        pluck env init >> ~/.zshrc

    After that, just::

        pluck env create myproject   # creates AND activates
        pluck env switch coding      # activates
        pluck env deactivate         # deactivates
    """
    if shell not in ("zsh", "bash"):
        raise ValueError(f"Unsupported shell: {shell!r}. Use 'zsh' or 'bash'.")

    return '''# pluck env shell wrapper — added by "pluck env init"
pluck() {
    case "$1" in
        env)
            case "$2" in
                create)
                    # Skip eval for help flags to avoid shell parsing errors
                    case "$*" in
                        *"-h"*|*"--help"*)
                            command pluck "$@"
                            ;;
                        *)
                            eval "$(command pluck "$@")"
                            ;;
                    esac
                    ;;
                switch)
                    # For switch, run directly when TUI needed (no name provided)
                    # so the interactive selector can access the terminal.
                    # Eval only when switching to a specific environment.
                    if [ -z "$3" ]; then
                        command pluck "$@"
                    else
                        case "$*" in
                            *"-h"*|*"--help"*)
                                command pluck "$@"
                                ;;
                            *)
                                eval "$(command pluck "$@")"
                                ;;
                        esac
                    fi
                    ;;
                *)
                    command pluck "$@"
                    ;;
            esac
            ;;
        *)
            command pluck "$@"
            ;;
    esac
}'''
