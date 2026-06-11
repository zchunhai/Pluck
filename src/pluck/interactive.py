"""Interactive component selection and config saving for pluck."""

import logging
import sys
import termios
import tty
from pathlib import Path
from typing import Any

from pluck.config import COMPONENT_TYPES
from pluck.repo import discover_components

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)

# ── Terminal raw-mode helpers ──────────────────────────────────────────


def _read_key() -> str:
    """Read a single keypress including arrow-key escape sequences."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Might be an escape sequence; try to read more
            # Set non-blocking read for the next bytes
            new = list(old)
            new[4] = 0  # VMIN = 0 (poll)
            new[5] = 1  # VTIME = 0.1s
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 in ("A", "B", "C", "D"):
                    return f"\x1b[{ch3}"
                return ch3  # e.g. '3' from '\x1b[3~' (Delete)
            return ch2 or ch  # standalone Escape or timed out
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── ANSI helpers ───────────────────────────────────────────────────────

CLEAR_LINE = "\033[2K"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
CYAN = "\033[36m"


def _move_cursor_up(n: int) -> None:
    sys.stdout.write(f"\033[{n}A")
    sys.stdout.flush()


# ── Interactive selection ──────────────────────────────────────────────


def interactive_select(
    plugin_name: str,
    repo_dir: Path,
    current_components: dict[str, Any],
) -> dict[str, Any]:
    """Run interactive component selection for a single plugin.

    Space to toggle, Enter to confirm. Up/Down or j/k to move cursor.
    / to filter, a for all, n for none.

    Args:
        plugin_name: Plugin name for display.
        repo_dir: Path to the cloned repository.
        current_components: Current component selections from config.

    Returns:
        Updated component selections dict.
    """
    available = discover_components(repo_dir)

    total_counts = " | ".join(
        f"{len(items)} {t}" for t, items in available.items() if items
    )
    print(f"\n{BOLD}📦 {plugin_name}{RESET} ({total_counts})")
    print(
        f"  {DIM}[space] toggle  [enter] confirm  [a] all  [n] none  [/] filter  [q] quit{RESET}"
    )
    print()

    result: dict[str, Any] = {}

    for comp_type in COMPONENT_TYPES:
        items = available.get(comp_type, [])
        if not items:
            result[comp_type] = []
            continue

        current = current_components.get(comp_type, [])
        current_names = _resolve_current_names(current, items)

        selected = _select_category_tui(comp_type, items, current_names)
        result[comp_type] = selected

    return result


def _resolve_current_names(current: list[str] | str, all_items: list[str]) -> set[str]:
    """Resolve current selection to a set of names."""
    if current == "all":
        return set(all_items)
    if isinstance(current, list):
        return set(current)
    return set()


def _select_category_tui(
    comp_type: str,
    items: list[str],
    current_names: set[str],
) -> list[str] | str:
    """Terminal-UI selection for one component category.

    Returns list of selected names, or "all".
    """
    cursor = 0  # index into display_items
    display_items = list(items)
    filter_keyword = ""
    selected = set(current_names)

    # Pre-calculate available terminal height
    term_height = _term_height()

    print(HIDE_CURSOR, end="")
    sys.stdout.flush()

    try:
        while True:
            max_show = max(4, term_height - 4)
            _render_frame(
                comp_type, display_items, selected, cursor, filter_keyword, max_show
            )

            key = _read_key()

            if key in ("\r", "\n"):
                # Enter — confirm
                break

            elif key in ("q", "Q"):
                # Quit without saving changes
                selected = set(current_names)
                break

            elif key in ("\x1b[A", "k"):
                # Up
                if display_items:
                    cursor = (cursor - 1) % len(display_items)

            elif key in ("\x1b[B", "j"):
                # Down
                if display_items:
                    cursor = (cursor + 1) % len(display_items)

            elif key == " ":
                # Space — toggle
                if display_items:
                    name = display_items[cursor]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)

            elif key in ("a", "A"):
                # Select all visible
                for name in display_items:
                    selected.add(name)

            elif key in ("n", "N"):
                # Deselect all visible
                for name in display_items:
                    selected.discard(name)

            elif key == "/":
                # Enter filter mode
                _cleanup_frame(len(display_items) + 3)
                new_kw = _read_filter_input(comp_type, filter_keyword)
                filter_keyword = new_kw
                display_items = _apply_filter(items, filter_keyword)
                cursor = 0

            elif key == "\x1b":
                # Escape — keep current
                break

    finally:
        print(SHOW_CURSOR, end="")
        sys.stdout.flush()

    # Determine return value
    if selected == set(items):
        return "all"
    return sorted(selected)


def _term_height() -> int:
    """Get terminal height, defaulting to 24."""
    try:
        import shutil

        size = shutil.get_terminal_size()
        return size.lines
    except Exception:
        return 24


def _apply_filter(items: list[str], keyword: str) -> list[str]:
    """Filter items by keyword substring (case-insensitive)."""
    if not keyword:
        return list(items)
    return [it for it in items if keyword.lower() in it.lower()]


def _read_filter_input(comp_type: str, current: str) -> str:
    """Read a filter keyword from the user (line-mode, not raw)."""
    prompt = f"  filter {comp_type} [{current or 'none'}]> "
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    try:
        val = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        val = ""
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()
    return val


def _render_frame(
    comp_type: str,
    items: list[str],
    selected: set[str],
    cursor: int,
    filter_keyword: str,
    max_show: int,
) -> None:
    """Render the selection list with cursor and checkboxes."""
    lines: list[str] = []

    # Header
    header = f"── {comp_type} ({len(selected)}/{len(items)} selected)"
    if filter_keyword:
        header += f"  filter: '{filter_keyword}'"
    lines.append(f"{CYAN}{header}{RESET}")

    # Determine scroll window
    total = len(items)
    if total <= max_show:
        start, end = 0, total
    else:
        # Keep cursor in view
        half = max_show // 2
        start = max(0, cursor - half)
        end = min(total, start + max_show)
        if end - start < max_show:
            start = max(0, end - max_show)

    # Items
    for i in range(start, end):
        name = items[i]
        is_sel = name in selected
        is_cur = i == cursor

        checkbox = f"{GREEN}✓{RESET}" if is_sel else " "
        prefix = f"{BOLD}>{RESET}" if is_cur else " "

        if is_cur:
            line = f" {prefix} [{checkbox}] {BOLD}{name}{RESET}"
        elif is_sel:
            line = f" {prefix} [{checkbox}] {GREEN}{name}{RESET}"
        else:
            line = f" {prefix} [{checkbox}] {DIM}{name}{RESET}"
        lines.append(line)

    # Trim indicator
    if start > 0:
        lines.insert(1, f"  {DIM}... {start} more above{RESET}")
    if end < total:
        lines.append(f"  {DIM}... {total - end} more below{RESET}")

    # Help line
    lines.append(
        f"\n{DIM}  [space] toggle  [↑↓/jk] move  [a] all  [n] none  [/] filter  [enter] confirm  [q] quit{RESET}"
    )

    # Write all lines
    sys.stdout.write("\n".join(lines))
    sys.stdout.write("\n")
    sys.stdout.flush()

    # Move cursor back up so next render overwrites this frame
    _move_cursor_up(len(lines))


def _cleanup_frame(lines: int) -> None:
    """Clear the rendered frame lines so input() doesn't overlap."""
    _move_cursor_up(1)
    for _ in range(lines):
        sys.stdout.write(CLEAR_LINE + "\r\n")
    sys.stdout.flush()


# ── Config saving ──────────────────────────────────────────────────────


def save_config(config_path: Path, plugins: list[dict[str, Any]]) -> None:
    """Save updated plugin configs back to YAML file.

    Merges new component selections into the existing file, preserving
    any extra keys or top-level fields the user may have added.
    """
    if yaml is None:
        raise ImportError("PyYAML is required")

    original: dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                original = yaml.safe_load(f) or {}
        except Exception:
            pass

    original_plugins = original.get("plugins", [])

    updated_plugins = []
    for plugin in plugins:
        name = plugin["name"]
        orig_entry: dict[str, Any] = next(
            (p for p in original_plugins if p.get("name") == name), {}
        )
        extra_orig = {
            k: v
            for k, v in orig_entry.items()
            if k not in ("name", "repo", "branch", "components")
        }
        merged = {
            "name": plugin["name"],
            "repo": plugin.get("repo") or orig_entry.get("repo", ""),
            "branch": plugin.get("branch") or orig_entry.get("branch", "main"),
            **extra_orig,
            "components": plugin["components"],
        }
        updated_plugins.append(merged)

    data = {**original, "plugins": updated_plugins}

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    logger.info("Config saved to %s", config_path)
