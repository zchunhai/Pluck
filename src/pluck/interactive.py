"""Interactive component selection and config saving for pluck."""

import contextlib
import logging
import os
import re
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


def _is_tty() -> bool:
    return os.isatty(sys.stdin.fileno())


def _read_key() -> str:
    """Read a single keypress. Returns \\x03 for Ctrl+C."""
    if not _is_tty():
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            sys.stdin.readline()
        return "\\n"

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\\x03":
            return "\\x03"
        if ch == "\\x1b":
            new = list(old)
            new[4] = 0
            new[5] = 1
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 in ("A", "B", "C", "D"):
                    return f"\\x1b[{ch3}"
                return ch3
            return ch2 or ch
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


CLEAR_LINE = "\\033[2K"
HIDE_CURSOR = "\\033[?25l"
SHOW_CURSOR = "\\033[?25h"
BOLD = "\\033[1m"
DIM = "\\033[2m"
RESET = "\\033[0m"
GREEN = "\\033[32m"
CYAN = "\\033[36m"
YELLOW = "\\033[33m"


def _term_width() -> int:
    import shutil

    return shutil.get_terminal_size().columns


def _term_height() -> int:
    import shutil

    return shutil.get_terminal_size().lines


def _strip_ansi(text: str) -> str:
    return re.sub(r"\\033\\[[0-9;]*[a-zA-Z]", "", text)


def _write_trunc(text: str, max_w: int) -> None:
    visible = _strip_ansi(text)
    if len(visible) <= max_w:
        sys.stdout.write(text)
        return
    out = []
    cnt = 0
    esc = False
    for ch in text:
        if ch == "\\033":
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
    if lines <= 0:
        return
    for _ in range(lines):
        sys.stdout.write(CLEAR_LINE + "\\r\\n")
    sys.stdout.write(f"\\033[{lines}A")
    sys.stdout.flush()


def interactive_select(
    plugin_name: str, repo_dir: Path, current_components: dict[str, Any]
) -> dict[str, Any] | None:
    available = discover_components(repo_dir)
    total_counts = " | ".join(
        f"{len(items)} {t}" for t, items in available.items() if items
    )
    print(f"\\n{BOLD}📦 {plugin_name}{RESET} ({total_counts})")
    print(
        f"  {DIM}[space] toggle  [enter] confirm  [a] all  [n] none  [/] filter  [q] reset  [Q] abort{RESET}"
    )
    print()

    result: dict[str, Any] = {}
    for comp_type in COMPONENT_TYPES:
        items = available.get(comp_type, [])
        if not items:
            result[comp_type] = []
            continue
        current = current_components.get(comp_type, [])
        if current == "all":
            current_names = set(items)
        elif isinstance(current, list):
            current_names = set(current)
        else:
            current_names = set()
        selected = _select_category(comp_type, items, current_names)
        if selected is None:
            return None
        result[comp_type] = selected
    return result


def _select_category(
    comp_type: str, items: list[str], current_names: set[str]
) -> list[str] | str | None:
    cursor = 0
    filter_kw = ""
    filter_mode = False
    selected = set(current_names)
    prev_lines = 0
    term_h = _term_height()
    term_w = _term_width()
    max_show = max(4, term_h - 5)

    if not _is_tty():
        if selected == set(items):
            return "all"
        return sorted(selected)

    print()
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()

    try:
        while True:
            filtered = _apply_filter(items, filter_kw)
            if cursor >= len(filtered) and filtered:
                cursor = len(filtered) - 1
            elif not filtered:
                cursor = 0

            prev_lines = _render_frame(
                comp_type,
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

            if key == "\\x03":
                _clear_frame(prev_lines)
                sys.stdout.write(SHOW_CURSOR)
                sys.stdout.flush()
                print("  Aborted.")
                return None

            if filter_mode:
                if key == "\\x1b":
                    filter_mode = False
                    continue
                if key in ("\\x7f", "\\x08"):
                    filter_kw = filter_kw[:-1]
                    continue
                if len(key) == 1 and key.isprintable():
                    filter_kw += key
                    continue

            if key in ("\\r", "\\n"):
                if filter_mode:
                    filter_mode = False
                else:
                    break
                continue

            if key == "/":
                if not filter_mode:
                    filter_mode = True
                continue

            if key in ("\\x1b[A", "k"):
                if filtered:
                    cursor = (cursor - 1) % len(filtered)
            elif key in ("\\x1b[B", "j"):
                if filtered:
                    cursor = (cursor + 1) % len(filtered)
            elif key == " ":
                if filtered:
                    name = filtered[cursor]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)
            elif key in ("a", "A"):
                for name in filtered:
                    selected.add(name)
            elif key in ("n", "N"):
                for name in filtered:
                    selected.discard(name)
            elif key == "\\x1b":
                break

            if not filter_mode:
                if key == "Q":
                    _clear_frame(prev_lines)
                    sys.stdout.write(SHOW_CURSOR)
                    sys.stdout.flush()
                    print("  Aborted.")
                    return None
                if key == "q":
                    selected = set(current_names)
                    break
    finally:
        _clear_frame(prev_lines)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

    if selected == set(items):
        return "all"
    return sorted(selected)


def _apply_filter(items: list[str], keyword: str) -> list[str]:
    if not keyword:
        return list(items)
    kw = keyword.lower()
    return [it for it in items if kw in it.lower()]


def _render_frame(
    comp_type: str,
    items: list[str],
    selected: set[str],
    cursor: int,
    filter_kw: str,
    filter_mode: bool,
    max_show: int,
    term_w: int,
    prev_lines: int,
) -> int:
    if prev_lines > 0:
        sys.stdout.write(f"\\033[{prev_lines}A")

    lines = []

    if filter_mode:
        lines.append(f"  {YELLOW}filter:{RESET} {filter_kw}{BOLD}_{RESET}")
    elif filter_kw:
        lines.append(f"  {DIM}filter: {filter_kw}  [/] to edit{RESET}")
    else:
        lines.append(f"  {DIM}[/] filter{RESET}")

    lines.append(
        f"{CYAN}-- {comp_type}{RESET}  "
        f"({GREEN}{len(selected)}{RESET}/{len(items)} selected)"
    )

    total = len(items)
    if total == 0:
        lines.append(f"  {DIM}(no matches){RESET}")
        start = end = 0
    elif total <= max_show:
        start, end = 0, total
    else:
        half = max_show // 2
        start = max(0, cursor - half)
        end = min(total, start + max_show)
        if end - start < max_show:
            start = max(0, end - max_show)

    for i in range(start, end):
        name = items[i]
        is_sel = name in selected
        is_cur = i == cursor
        chk = f"{GREEN}v{RESET}" if is_sel else " "
        ptr = f"{BOLD}>{RESET}" if is_cur else " "
        if is_cur:
            lines.append(f" {ptr} [{chk}] {BOLD}{name}{RESET}")
        elif is_sel:
            lines.append(f" {ptr} [{chk}] {GREEN}{name}{RESET}")
        else:
            lines.append(f" {ptr} [{chk}] {DIM}{name}{RESET}")

    if total > 0:
        if start > 0:
            lines.insert(2, f"  {DIM}... {start} more above{RESET}")
        if end < total:
            lines.append(f"  {DIM}... {total - end} more below{RESET}")

    if filter_mode:
        lines.append(
            f"{YELLOW}  [type + arrows to filter and pick]  "
            f"[enter/esc] exit filter{RESET}"
        )
    else:
        lines.append(
            f"{DIM}  [space] toggle  [arrows/jk] move  [a] all  [n] none  "
            f"[/] filter  [enter] confirm  [q] reset  [Q] abort{RESET}"
        )

    for i, line in enumerate(lines):
        sys.stdout.write(CLEAR_LINE + "\\r")
        _write_trunc(line, term_w - 1)
        if i < len(lines) - 1:
            sys.stdout.write("\\n")

    if prev_lines > len(lines):
        for _ in range(prev_lines - len(lines)):
            sys.stdout.write("\\n" + CLEAR_LINE + "\\r")
        sys.stdout.write(f"\\033[{prev_lines - len(lines)}A")

    sys.stdout.flush()
    return len(lines)


def save_config(config_path: Path, plugins: list[dict[str, Any]]) -> None:
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
            data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    logger.info("Config saved to %s", config_path)
