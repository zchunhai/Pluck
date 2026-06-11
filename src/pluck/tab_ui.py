"""Tab-based component selection UI for pluck.

Implements a multi-tab interface where each component type (skills, agents, etc.)
is a tab, with shared filtering and independent selection state.
"""

import contextlib
import os
import sys
import termios
import tty
from pathlib import Path
from typing import Any

from pluck.config import COMPONENT_TYPES
from pluck.repo import discover_components

# Reuse ANSI constants and helpers from the parent module
# These are imported at runtime to avoid circular imports
CLEAR_LINE = "\x1b[2K"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"


def _is_tty() -> bool:
    return os.isatty(sys.stdin.fileno())


def _read_key() -> str:
    """Read a single keypress. Returns \x03 for Ctrl+C."""
    if not _is_tty():
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            sys.stdin.readline()
        return "\n"

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            return "\x03"
        if ch == "\x1b":
            new = list(old)
            cc = list(new[6])
            cc[termios.VMIN] = 0
            cc[termios.VTIME] = 1
            new[6] = cc
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


def _term_width() -> int:
    import shutil

    return shutil.get_terminal_size().columns


def _term_height() -> int:
    import shutil

    return shutil.get_terminal_size().lines


def _strip_ansi(text: str) -> str:
    import re

    # \x1b is the ESC byte; concatenate to avoid raw-string issues
    return re.sub("\x1b" + r"\[[0-9;]*[a-zA-Z]", "", text)


def _write_trunc(text: str, max_w: int) -> None:
    visible = _strip_ansi(text)
    if len(visible) <= max_w:
        sys.stdout.write(text)
        return
    out = []
    cnt = 0
    esc = False
    for ch in text:
        if ch == "\x1b":
            esc = True
            out.append(ch)
        elif esc:
            out.append(ch)
            if ch == "m":
                esc = False
        else:
            if cnt >= max_w:
                break
            cnt += 1
            out.append(ch)
    sys.stdout.write("".join(out))


def _clear_frame(lines: int) -> None:
    """Remove the TUI frame entirely, pulling content below up to fill the gap.

    ``lines`` is ``prev_lines`` = ``frame_height - 1``.
    Uses ANSI delete-line (``\\x1b[M``) to remove each frame row
    so no blank lines are left behind.
    """
    if lines <= 0:
        return
    # Move up from last rendered line to frame top
    sys.stdout.write(f"\x1b[{lines}A")
    # Delete each frame row; content below scrolls up to fill
    for _ in range(lines + 1):
        sys.stdout.write("\x1b[M")
    sys.stdout.flush()


def _apply_filter(items: list[str], keyword: str) -> list[str]:
    if not keyword:
        return list(items)
    kw = keyword.lower()
    return [it for it in items if kw in it.lower()]


def _select_with_tabs(
    plugin_name: str,
    available: dict[str, list[str]],
    current_components: dict[str, Any],
) -> dict[str, Any] | None:
    """Tab-based multi-component selection UI.

    Each component type is a tab, with independent selection state.
    Shared filter applies across all tabs.
    """
    if not _is_tty():
        # Non-TTY fallback: return current selections
        result: dict[str, Any] = {}
        for comp_type, _items in available.items():
            current = current_components.get(comp_type, [])
            if current == "all":
                result[comp_type] = "all"
            elif isinstance(current, list):
                result[comp_type] = current
            else:
                result[comp_type] = []
        return result

    # Initialize state for each tab
    tab_state: dict[str, dict[str, Any]] = {}
    tab_list: list[str] = []
    for comp_type in COMPONENT_TYPES:
        items = available.get(comp_type, [])
        if not items:
            continue
        tab_list.append(comp_type)
        current = current_components.get(comp_type, [])
        if current == "all":
            selected = set(items)
        elif isinstance(current, list):
            selected = set(current)
        else:
            selected = set()
        tab_state[comp_type] = {
            "items": items,
            "selected": selected,
            "cursor": 0,
        }

    if not tab_list:
        print("  No components found.")
        return {}

    # Shared state
    filter_kw = ""
    filter_mode = False
    current_tab_idx = 0
    prev_lines = 0
    term_h = _term_height()
    term_w = _term_width()
    # Header (2 lines) + filter + tabs + up to 2 scroll indicators + menu + spare
    frame_overhead = 9
    max_show = max(4, term_h - frame_overhead)

    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()

    # Suppress terminal echo for the entire TUI session so that keys
    # like ESC are not echoed as "^[" between _read_key() calls.
    fd = sys.stdin.fileno()
    old_mode = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    try:
        while True:
            current_tab = tab_list[current_tab_idx]
            state = tab_state[current_tab]
            items = state["items"]
            selected = state["selected"]

            # Apply filter to current tab
            filtered = _apply_filter(items, filter_kw)
            if not filtered:
                cursor = 0
            else:
                cursor = state["cursor"]
                if cursor >= len(filtered):
                    cursor = len(filtered) - 1
                state["cursor"] = cursor

            prev_lines = _render_tab_frame(
                plugin_name,
                tab_list,
                current_tab_idx,
                filtered,
                selected,
                cursor,
                filter_kw,
                filter_mode,
                max_show,
                term_w,
                prev_lines,
            )

            key = _read_key()

            # --- Ctrl+C: abort ---
            if key == "\x03":
                print("  Aborted.")
                return None

            # --- Filter mode handling ---
            if filter_mode:
                if key == "\x1b":  # ESC: exit filter and clear
                    filter_mode = False
                    filter_kw = ""
                    continue
                if key in ("\x7f", "\x08"):  # Backspace
                    filter_kw = filter_kw[:-1]
                    continue
                if len(key) == 1 and key.isprintable() and key != " ":
                    filter_kw += key
                    continue

            # --- Enter: confirm or exit filter ---
            if key in ("\r", "\n"):
                if filter_mode:
                    filter_mode = False
                    continue
                # Normal mode: confirm and exit
                break

            # --- Slash: enter filter mode ---
            if key == "/":
                if not filter_mode:
                    filter_mode = True
                continue

            # --- Tab navigation: switch between component type tabs ---
            if key in ("\x1b[C", "\x09"):  # Right arrow or Tab
                current_tab_idx = (current_tab_idx + 1) % len(tab_list)
                continue
            if key in ("\x1b[D", "\x1b[Z"):  # Left arrow or Shift+Tab
                current_tab_idx = (current_tab_idx - 1) % len(tab_list)
                continue

            # --- Vertical navigation: move within current tab ---
            if key in ("\x1b[A", "k"):  # Up arrow
                if filtered:
                    state["cursor"] = (cursor - 1) % len(filtered)
            elif key in ("\x1b[B", "j"):  # Down arrow
                if filtered:
                    state["cursor"] = (cursor + 1) % len(filtered)

            # --- Space: toggle current item ---
            elif key == " ":
                if filtered:
                    name = filtered[cursor]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)

            # --- A/N: select/deselect all in current tab ---
            elif key in ("a", "A"):
                for name in filtered:
                    selected.add(name)
            elif key in ("n", "N"):
                for name in filtered:
                    selected.discard(name)

            # --- Q: abort ---
            elif key == "Q":
                print("  Aborted.")
                return None

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_mode)
        _clear_frame(prev_lines)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

    # Build result dict
    final_result: dict[str, Any] = {}
    for comp_type in COMPONENT_TYPES:
        if comp_type not in tab_state:
            final_result[comp_type] = []
            continue
        items = tab_state[comp_type]["items"]
        selected = tab_state[comp_type]["selected"]
        if selected == set(items):
            final_result[comp_type] = "all"
        else:
            final_result[comp_type] = sorted(selected)

    return final_result


def _render_tab_frame(
    plugin_name: str,
    tab_list: list[str],
    current_tab_idx: int,
    items: list[str],
    selected: set[str],
    cursor: int,
    filter_kw: str,
    filter_mode: bool,
    max_show: int,
    term_w: int,
    prev_lines: int,
) -> int:
    """Render the tab-based selection UI.

    Overwrites the old frame in-place by always rendering enough lines
    to cover the taller of the previous and current frame.  Returns the
    line count the *caller* must move up next time to reach frame top
    (i.e. ``total_rendered - 1``).
    """
    # --- Move cursor to frame top ---
    if prev_lines > 0:
        sys.stdout.write(f"\x1b[{prev_lines}A")

    # --- Build frame content ---
    lines: list[str] = []

    # Filter bar
    if filter_mode:
        lines.append(f"  {YELLOW}filter:{RESET} {filter_kw}{BOLD}_{RESET}")
    elif filter_kw:
        lines.append(f"  {DIM}filter: {filter_kw}  [/] to edit{RESET}")
    else:
        lines.append(f"  {DIM}[/] filter{RESET}")

    # Tab labels
    tab_labels: list[str] = []
    for i, tab_name in enumerate(tab_list):
        if i == current_tab_idx:
            tab_labels.append(f"{BOLD}[{tab_name}]{RESET}")
        else:
            tab_labels.append(f"{DIM}[{tab_name}]{RESET}")
    lines.append("  " + " ".join(tab_labels))

    # Content area
    item_total = len(items)
    start: int
    end: int

    if item_total == 0:
        lines.append(f"  {DIM}(no items in this tab){RESET}")
        start = end = 0
    elif item_total <= max_show:
        start, end = 0, item_total
    else:
        half = max_show // 2
        start = max(0, cursor - half)
        end = min(item_total, start + max_show)
        if end - start < max_show:
            start = max(0, end - max_show)

    for idx in range(start, end):
        name = _strip_ansi(items[idx])
        is_sel = name in selected
        is_cur = idx == cursor
        chk = f"{GREEN}v{RESET}" if is_sel else " "
        ptr = f"{BOLD}>{RESET}" if is_cur else " "
        if is_cur:
            lines.append(f" {ptr} [{chk}] {BOLD}{name}{RESET}")
        elif is_sel:
            lines.append(f" {ptr} [{chk}] {GREEN}{name}{RESET}")
        else:
            lines.append(f" {ptr} [{chk}] {DIM}{name}{RESET}")

    if item_total > 0:
        if start > 0:
            lines.insert(2, f"  {DIM}... {start} more above{RESET}")
        if end < item_total:
            lines.append(f"  {DIM}... {item_total - end} more below{RESET}")

    # Bottom menu
    if filter_mode:
        lines.append(
            f"{YELLOW}  [type to filter] [arrows] move [space] toggle "
            f"[enter/esc] exit filter{RESET}"
        )
    else:
        lines.append(
            f"{DIM}  [space] toggle  [arrows/jk] move  [a] all  [n] none  "
            f"[tab/←→] switch tab  [/] filter  [enter] done  [Q] abort{RESET}"
        )

    # --- Render, overwriting old frame ---
    # Always write enough lines to cover the *taller* of old and new frame.
    old_height = prev_lines + 1
    new_height = len(lines)
    total = max(old_height, new_height)

    for i in range(total):
        sys.stdout.write(CLEAR_LINE + "\r")
        if i < new_height:
            _write_trunc(lines[i], term_w - 1)
        if i < total - 1:
            sys.stdout.write("\n")

    # Cursor is now on the last rendered line (row ``total`` of the frame).
    sys.stdout.flush()
    return total - 1


# --- Public API ---

def interactive_select(
    plugin_name: str, repo_dir: Path, current_components: dict[str, Any]
) -> dict[str, Any] | None:
    """Interactively select components using a tab-based UI.

    Each component type (skills, agents, etc.) is shown as a tab.
    Filter applies across all tabs. Selection state is per-tab.
    """
    available = discover_components(repo_dir)

    total_counts = " | ".join(
        f"{len(items)} {t}" for t, items in available.items() if items
    )
    print(f"\n{BOLD}📦 {plugin_name}{RESET} ({total_counts})")

    return _select_with_tabs(plugin_name, available, current_components)
