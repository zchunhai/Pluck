"""Git repository management and component discovery for pluck."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Possible locations for each component type within a plugin repo
COMPONENT_SEARCH_PATHS = {
    "skills": ["skills", ".claude/skills"],
    "agents": ["agents", ".claude/agents"],
    "commands": ["commands", ".claude/commands"],
    "rules": ["rules", ".claude/rules"],
    "hooks": ["hooks", ".claude/hooks"],
    "contexts": ["contexts", ".claude/contexts"],
}


def clone_or_update(repo_url: str, target_dir: Path, branch: str = "main") -> str:
    """Clone or update a git repository. Returns the current commit SHA."""
    target_dir = Path(target_dir)

    if target_dir.exists() and (target_dir / ".git").exists():
        return _update_repo(target_dir, branch)

    if target_dir.exists():
        raise RuntimeError(
            f"Directory exists but is not a git repo: {target_dir}. "
            f"Remove it manually or run 'pluck uninstall {target_dir.name}' first."
        )

    return _clone_repo(repo_url, target_dir, branch)


def _clone_repo(repo_url: str, target_dir: Path, branch: str) -> str:
    """Clone a repository with blob-less partial clone for speed."""
    logger.info(
        "Cloning %s -> %s (this may take a moment for large repos)",
        repo_url,
        target_dir,
    )
    _run_git(
        [
            "git",
            "clone",
            "--branch",
            branch,
            "--depth",
            "1",
            "--filter=blob:none",
            repo_url,
            str(target_dir),
        ],
        timeout=600,
    )
    return get_commit_sha(target_dir)


def _update_repo(repo_dir: Path, branch: str) -> str:
    """Update an existing repository."""
    logger.info("Updating %s", repo_dir)
    _run_git(["git", "fetch", "origin", branch], cwd=repo_dir, timeout=300)
    _run_git(["git", "checkout", branch], cwd=repo_dir)
    _run_git(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
    return get_commit_sha(repo_dir)


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


def get_commit_sha(repo_dir: Path) -> str:
    """Get the current HEAD commit short SHA."""
    return _run_git(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir)


def discover_components(repo_dir: Path) -> dict[str, list[str]]:
    """Scan a repository and discover all available components by type."""
    repo_dir = Path(repo_dir)
    discovered: dict[str, list[str]] = {}

    for comp_type, search_paths in COMPONENT_SEARCH_PATHS.items():
        items: list[str] = []
        for search_path in search_paths:
            comp_dir = repo_dir / search_path
            if comp_dir.is_dir():
                items.extend(_scan_component_dir(comp_type, comp_dir))
        discovered[comp_type] = sorted(set(items))

    return discovered


def _scan_component_dir(comp_type: str, comp_dir: Path) -> list[str]:
    """Scan a component directory and return item names."""
    items: list[str] = []

    for child in sorted(comp_dir.iterdir()):
        name = child.name
        if name.startswith(".") or name == "README.md":
            continue

        if comp_type == "skills":
            if child.is_dir() and (child / "SKILL.md").exists():
                items.append(name)
        elif comp_type == "hooks":
            if name == "hooks.json":
                items.append("hooks")
        elif comp_type in ("agents", "commands"):
            if child.is_file() and child.suffix == ".md":
                items.append(child.stem)
        elif comp_type == "rules":
            if child.is_dir() or (child.is_file() and child.suffix == ".md"):
                items.append(name)
        elif comp_type == "contexts" and (child.is_file() or child.is_dir()):
            items.append(name)

    return items


def resolve_component_paths(
    repo_dir: Path, comp_type: str, selection: list[str] | str
) -> list[Path]:
    """Resolve source paths for selected components.

    Args:
        repo_dir: Path to the cloned plugin repository.
        comp_type: Component type (skills, agents, commands, etc.).
        selection: List of component names, or "all" to select everything.

    Returns:
        List of resolved Path objects for the selected components.
    """
    repo_dir = Path(repo_dir)
    search_paths = COMPONENT_SEARCH_PATHS.get(comp_type, [])

    if selection == "all":
        all_components = discover_components(repo_dir)
        names = all_components.get(comp_type, [])
    else:
        names = list(selection)

    paths: list[Path] = []
    for name in names:
        source = _find_component(repo_dir, search_paths, comp_type, name)
        if source is not None:
            paths.append(source)
        else:
            logger.warning("Component not found: %s/%s", comp_type, name)

    return paths


def _find_component(
    repo_dir: Path, search_paths: list[str], comp_type: str, name: str
) -> Path | None:
    """Find a specific component in the repo by searching known paths."""
    for search_path in search_paths:
        base = repo_dir / search_path
        if not base.is_dir():
            continue

        if comp_type == "skills":
            candidate = base / name
            if candidate.is_dir() and (candidate / "SKILL.md").exists():
                return candidate
        elif comp_type == "hooks":
            if name == "hooks" and (base / "hooks.json").exists():
                return base
        elif comp_type in ("agents", "commands"):
            candidate = base / f"{name}.md"
            if candidate.is_file():
                return candidate
        elif comp_type == "rules" or comp_type == "contexts":
            candidate = base / name
            if candidate.exists():
                return candidate

    return None
