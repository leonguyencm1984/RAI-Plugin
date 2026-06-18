"""GitNexus invocation contract for the plugin.

Single source of truth for how the plugin shells out to the `gitnexus` CLI, so
local indexing stays consistent with the RAI-Platform backend
(`app/services/repo_service.py`). That copy is async (FastAPI worker); this one
is sync because the plugin executor runs skills synchronously via subprocess.

Contract notes (verified against `gitnexus analyze --help`):
- `gitnexus analyze .` is INCREMENTAL by default — it only re-parses changed
  files. `--force` triggers a full rebuild. So re-running analyze after a skill
  writes code is cheap (Fork-4 "incremental only"): just call analyze() without
  force.
- The index lives in `<repo>/.gitnexus/`.
- A missing binary degrades gracefully (returns success=False, never raises) so
  a skill can still read files even when symbol queries are unavailable.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

INDEX_DIRNAME = ".gitnexus"
DEFAULT_TIMEOUT = 300


def gitnexus_bin() -> str:
    """Resolve the gitnexus binary. Override with RAI_GITNEXUS_BIN."""
    return os.environ.get("RAI_GITNEXUS_BIN", "gitnexus")


def index_path_for(repo_path: Path) -> Path:
    """Path to the gitnexus index directory for a repo checkout."""
    return Path(repo_path) / INDEX_DIRNAME


def is_indexed(repo_path: Path) -> bool:
    """True when repo_path already has a gitnexus index to keep fresh."""
    return index_path_for(repo_path).is_dir()


def analyze(
    repo_path: Path,
    *,
    force: bool = False,
    skip_git: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """Run `gitnexus analyze .` in repo_path. Returns (success, log).

    Incremental by default (only changed files re-parsed); pass force=True for a
    full rebuild, skip_git=True for a folder without a .git directory. Never
    raises: a missing binary or timeout returns (False, <reason>) so callers can
    degrade gracefully.
    """
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        return False, f"repo path does not exist: {repo_path}"
    cmd = [gitnexus_bin(), "analyze", "."]
    if force:
        cmd.append("--force")
    if skip_git:
        cmd.append("--skip-git")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, f"gitnexus binary not found ({gitnexus_bin()}); indexing skipped"
    except subprocess.TimeoutExpired:
        return False, f"gitnexus analyze timed out after {timeout}s"
    log = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, log[-2000:]


def has_working_tree_changes(repo_path: Path) -> bool | None:
    """Whether the checkout has uncommitted changes.

    Returns True when `git status --porcelain` reports any change, False when the
    tree is clean, and None when it cannot be determined (not a git checkout, or
    git unavailable). This drives the reindex force decision: `gitnexus analyze`
    is incremental against *committed* state only, so an uncommitted file a skill
    just wrote is invisible to an incremental pass and needs a full re-parse.
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return bool((r.stdout or "").strip())


def reindex(repo_path: Path, *, skip_git: bool = False) -> tuple[bool, str]:
    """Refresh the index after a skill writes code.

    Only runs when an index already exists — building the initial index is
    /rai:pull-repos' job, not a side effect of running a skill. Picks the cheapest
    correct mode (decision: "force only when files changed"):
      - clean working tree  → incremental (cheap; nothing uncommitted to miss).
      - dirty / unknown      → full re-parse (`--force`), the only mode that indexes
                               uncommitted working-tree files a skill just wrote.
    Erring toward force when dirtiness is unknown keeps the symbol graph correct
    (force is always correct, just slower). Never raises.
    """
    repo_path = Path(repo_path)
    if not is_indexed(repo_path):
        return False, f"no gitnexus index at {index_path_for(repo_path)}; skipping reindex"
    changed = has_working_tree_changes(repo_path)
    force = changed is not False  # force unless we positively confirm a clean tree
    # A non-git folder source can't be diffed; parse its working tree directly.
    if not (repo_path / ".git").is_dir():
        skip_git = True
    return analyze(repo_path, force=force, skip_git=skip_git)
