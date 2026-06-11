"""Interactive component selection and config saving for pluck."""

import logging
import os
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

# ── TTY detection ──


def _is_tty() -> bool:
    """Check if stdin is a real terminal (not piped)."""
    return os.isatty(sys.stdin.fileno())


# ── Terminal raw-mode ──────────────────────────────────────────────────


def _read_key() -> str:
    """Read a single keypress including arrow-key escape sequences.

    Returns "\\x03" for Ctrl+C so callers can detect abort intent.
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            return "\x03"  # Ctrl+C
        if ch == "\x1b":
            new = list(old)
            new[4] = 0  # VMIN = 0 (poll)
            new[5] = 1  # VTIME = 0.1s
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 in ("A", "B", "C", "D"):
                    return f"\x1b[{ch3}"
                return ch3
            return ch2 or ch
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── ANSI codes ─────────────────────────────────────────────────────────

CLEAR_BELOW = "\033[J"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
SAVE_CURSOR = "\033[s"
RESTORE_CURSOR = "\033[u"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"


# ── Interactive selection ──────────────────────────────────────────────


def interactive_select(
    plugin_name: str,
    repo_dir: Path,
    current_components: dict[str, Any],
) -> dict[str, Any] | None:
    """Run interactive component selection for a single plugin.

    Space to toggle, Enter to confirm. Up/Down or j/k to move cursor.
    / to start inline filter, type to filter in real-time.
    q to reset category, Q or Ctrl+C to abort everything.

    Returns:
        Updated component selections dict, or None if user aborted.
    """
    available = discover_components(repo_dir)

    total_counts = " | ".join(
        f"{len(items)} {t}" for t, items in available.items() if items
    )
    print(f"\n{BOLD}📦 {plugin_name}{RESET} ({total_counts})")
    print(
        f"  {DIM}[space] toggle  [enter] confirm  [a] all  [n] none  "
        f"[/] filter  [q] reset  [Q] abort{RESET}"
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

        selected = _select_category(comp_type, items, current_names)
        if selected is None:
            return None  # user aborted
        result[comp_type] = selected

    return result


def _resolve_current_names(current: list[str] | str, all_items: list[str]) -> set[str]:
    """Resolve current selection to a set of names."""
    if current == "all":
        return set(all_items)
    if isinstance(current, list):
        return set(current)
    return set()


def _select_category(
    comp_type: str,
    items: list[str],
    current_names: set[str],
) -> list[str] | str | None:
    """Terminal-UI selection for one component category.

    Returns list of selected names, "all", or None if aborted.
    """
    cursor = 0
    filter_keyword = ""
    filter_mode = False  # True while user is typing a filter
    selected = set(current_names)

    term_height = _term_height()
    max_show = max(4, term_height - 5)  # 1 header + 1 filter + items + 1 help

    sys.stdout.write(SAVE_CURSOR)
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()

    try:
        while True:
            display_items = _apply_filter(items, filter_keyword)
            # Clamp cursor if filter changed
            if cursor >= len(display_items) and display_items:
                cursor = len(display_items) - 1
            elif not display_items:
                cursor = 0

            _render_frame(
                comp_type,
                display_items,
                selected,
                cursor,
                filter_keyword,
                filter_mode,
                max_show,
            )

            key = _read_key()

            if key == "\x03":
                sys.stdout.write(RESTORE_CURSOR)
                sys.stdout.write(CLEAR_BELOW)
                sys.stdout.write(SHOW_CURSOR)
                sys.stdout.flush()
                print("  ⚠ Aborted.")
                return None

            if filter_mode:
                # In filter mode: keys modify the filter string
                if key in ("\r", "\n", "\x1b"):
                    filter_mode = False
                elif key in ("\x7f", "\x08"):
                    # Backspace / Delete
                    filter_keyword = filter_keyword[:-1]
                elif len(key) == 1 and key.isprintable():
                    filter_keyword += key
                continue

            # Normal mode
            if key in ("\r", "\n"):
                break  # Enter — confirm

            if key == "Q":
                sys.stdout.write(RESTORE_CURSOR)
                sys.stdout.write(CLEAR_BELOW)
                sys.stdout.write(SHOW_CURSOR)
                sys.stdout.flush()
                print("  ⚠ Aborted.")
                return None

            if key == "q":
                selected = set(current_names)
                break

            if key == "/":
                filter_mode = True
                continue

            if key in ("\x1b[A", "k"):
                if display_items:
                    cursor = (cursor - 1) % len(display_items)

            elif key in ("\x1b[B", "j"):
                if display_items:
                    cursor = (cursor + 1) % len(display_items)

            elif key == " ":
                if display_items:
                    name = display_items[cursor]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)

            elif key in ("a", "A"):
                for name in display_items:
                    selected.add(name)

            elif key in ("n", "N"):
                for name in display_items:
                    selected.discard(name)

            elif key == "\x1b":
                break  # Escape — keep current

    finally:
        sys.stdout.write(RESTORE_CURSOR)
        sys.stdout.write(CLEAR_BELOW)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

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
    kw = keyword.lower()
    return [it for it in items if kw in it.lower()]


def _render_frame(
    comp_type: str,
    items: list[str],
    selected: set[str],
    cursor: int,
    filter_keyword: str,
    filter_mode: bool,
    max_show: int,
) -> None:
    """Render the selection list with cursor and checkboxes.

    Uses cursor save/restore to avoid line-wrapping flicker artifacts.
    """
    sys.stdout.write(RESTORE_CURSOR)
    sys.stdout.write(CLEAR_BELOW)

    lines: list[str] = []

    # ── Filter bar (always visible at top) ──
    if filter_mode:
        bar = f"{YELLOW}filter:{RESET} {filter_keyword}{BOLD}█{RESET}"
    elif filter_keyword:
        bar = f"{DIM}filter: {filter_keyword}  [/] to edit{RESET}"
    else:
        bar = f"{DIM}[/] filter{RESET}"
    lines.append(f"  {bar}")

    # ── Category header ──
    header = f"{CYAN}── {comp_type}{RESET}  ({GREEN}{len(selected)}{RESET}/{len(items)} selected)"
    lines.append(header)

    # ── Items ──
    total = len(items)
    if total == 0:
        lines.append(f"  {DIM}(no matches){RESET}")
    elif total <= max_show:
        start, end = 0, total
    else:
        half = max_show // 2
        start = max(0, cursor - half)
        end = min(total, start + max_show)
        if end - start < max_show:
            start = max(0, end - max_show)

    for i in range(start, end) if total > 0 else []:
        name = items[i]
        is_sel = name in selected
        is_cur = i == cursor
        checkbox = f"{GREEN}✓{RESET}" if is_sel else " "
        pointer = f"{BOLD}>{RESET}" if is_cur else " "

        if is_cur:
            line = f" {pointer} [{checkbox}] {BOLD}{name}{RESET}"
        elif is_sel:
            line = f" {pointer} [{checkbox}] {GREEN}{name}{RESET}"
        else:
            line = f" {pointer} [{checkbox}] {DIM}{name}{RESET}"
        lines.append(line)

    if total > 0:
        if start > 0:
            lines.insert(2, f"  {DIM}... {start} more above{RESET}")
        if end < total:
            lines.append(f"  {DIM}... {total - end} more below{RESET}")

    # ── Help ──
    if filter_mode:
        help_line = f"{YELLOW}  [type to filter] [enter/esc] done{RESET}"
    else:
        help_line = (
            f"{DIM}  [space] toggle  [↑↓/jk] move  [a] all  [n] none  "
            f"[/] filter  [enter] confirm  [q] reset  [Q] abort{RESET}"
        )
    lines.append(help_line)

    sys.stdout.write("\n".join(lines))
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
