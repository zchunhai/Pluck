"""Interactive component selection and config saving for pluck."""

import logging
from pathlib import Path
from typing import Any

from pluck.config import COMPONENT_TYPES
from pluck.repo import discover_components

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)


def interactive_select(
    plugin_name: str,
    repo_dir: Path,
    current_components: dict[str, Any],
) -> dict[str, Any]:
    """Run interactive component selection for a single plugin.

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
    print(f"\n📦 {plugin_name} ({total_counts})")
    print("  Enter: numbers/ranges (1 3 5-10), 'all', 'none', '/keyword' to filter")
    print("  Press Enter to keep current selection and continue\n")

    result: dict[str, Any] = {}

    for comp_type in COMPONENT_TYPES:
        items = available.get(comp_type, [])
        if not items:
            result[comp_type] = []
            continue

        current = current_components.get(comp_type, [])
        current_names = _resolve_current_names(current, items)

        selected = _select_category(comp_type, items, current_names)
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
    comp_type: str, items: list[str], current_names: set[str]
) -> list[str] | str:
    """Interactive selection for one component category."""
    filtered_items = list(items)
    filter_keyword = ""

    while True:
        selected = {name for name in filtered_items if name in current_names}
        _print_items(filtered_items, selected, filter_keyword)

        count_text = (
            f"{len(current_names)} selected"
            if len(current_names) <= len(items)
            else "all selected"
        )
        prompt = f"  {comp_type} ({count_text})> "
        answer = input(prompt).strip()

        if not answer:
            break

        if answer == "all":
            current_names = set(items)
            continue

        if answer == "none":
            current_names = set()
            continue

        if answer.startswith("/"):
            filter_keyword = answer[1:].strip().lower()
            if filter_keyword:
                filtered_items = [it for it in items if filter_keyword in it.lower()]
            else:
                filtered_items = list(items)
                filter_keyword = ""
            continue

        indices = _parse_numbers(answer, len(filtered_items))
        if indices is not None:
            for idx in indices:
                name = filtered_items[idx - 1]
                if name in current_names:
                    current_names.discard(name)
                else:
                    current_names.add(name)
            continue

        print("  ⚠ Invalid input. Use numbers, 'all', 'none', or '/keyword'")

    if current_names == set(items):
        return "all"
    return sorted(current_names)


def _print_items(items: list[str], selected: set[str], filter_keyword: str) -> None:
    """Print items with selection markers."""
    header = f"\n── {len(items)} items"
    if filter_keyword:
        header += f" (filtered: '{filter_keyword}')"
    print(header)

    for i, name in enumerate(items, 1):
        marker = "✓" if name in selected else " "
        print(f"  [{marker}] {i:3d}. {name}")

    print()


def _parse_numbers(answer: str, max_count: int) -> list[int] | None:
    """Parse number selections like '1 3 5-10'.

    Returns list of 1-based indices, or None if invalid.
    """
    indices: list[int] = []
    for part in answer.replace(",", " ").split():
        if "-" in part:
            parts = part.split("-", 1)
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError:
                return None
            if start < 1 or end < start or end > max_count:
                print(f"  ⚠ Range {part} out of bounds (1-{max_count})")
                continue
            indices.extend(range(start, end + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                return None
            if n < 1 or n > max_count:
                print(f"  ⚠ Number {n} out of bounds (1-{max_count})")
                continue
            indices.append(n)

    return indices if indices else None


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
