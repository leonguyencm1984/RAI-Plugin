"""Repo resolution (T1, approach C): map a platform repo to ONE local checkout.

Link-first precedence:
  1. configured  — explicit repo_paths[<name>] in project .rkm/config.json
  2. linked      — a single existing local checkout whose origin matches git_url
  3. clone       — no local checkout found; caller clones into the vault
  4. ambiguous   — >1 local checkout matches; caller FAILS LOUD and prompts
                   (never auto-pick — Fork-2)
  5. none        — configured path was set but does not exist; caller fails loud

The *pure* resolution logic lives here so it is unit-testable (eng-review E3).
Side effects (clone, archive, gitnexus index) stay in the caller, driven by the
ResolveOutcome this returns. The git-remote scan is bounded to immediate children
of the given search roots — it never walks the whole filesystem (eng-review E4).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Resolution sources (also written to .rkm-meta.json as resolution_source).
CONFIGURED = "configured"
LINKED = "linked"
CLONE = "clone"
AMBIGUOUS = "ambiguous"
NONE = "none"


def normalize_git_url(url: str) -> str:
    """Normalize a git URL for comparison. Mirrors RAI-Platform
    repo_service.normalize_git_url (case-insensitive, SSH→HTTPS, strip .git /
    trailing slash) so plugin and server agree on what "the same repo" means.
    """
    if not url:
        return ""
    url = url.strip().lower()
    ssh = re.match(r"^git@([^:]+):(.+)$", url)
    if ssh:
        url = f"https://{ssh.group(1)}/{ssh.group(2)}"
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/")


def git_remote_url(checkout: Path) -> str | None:
    """`git -C <checkout> remote get-url origin`, or None on any failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(checkout), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def candidate_checkouts(search_roots: list) -> list[Path]:
    """Immediate child directories of each search root that look like git
    checkouts. Depth 1 only — bounded so we never walk $HOME recursively
    (eng-review E4). Deduplicated by resolved path.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for root in search_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir() and (child / ".git").exists():
                    rp = child.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        out.append(child)
            except OSError:
                continue
    return out


@dataclass
class ResolveOutcome:
    source: str                       # one of CONFIGURED/LINKED/CLONE/AMBIGUOUS/NONE
    path: Path | None = None          # resolved (or clone-target) path
    matches: list[Path] = field(default_factory=list)  # populated when AMBIGUOUS
    reason: str = ""                  # human-readable explanation for fail-loud cases


def resolve_repo(
    repo: dict,
    *,
    repo_paths: dict | None,
    search_roots: list,
    vault_repo_dir: Path,
    remote_url_fn: Callable[[Path], "str | None"] = git_remote_url,
) -> ResolveOutcome:
    """Resolve one platform repo dict to a local path via the link-first
    precedence. Pure except for the injected remote_url_fn (defaults to running
    git; tests inject a fake). Never clones — returns a CLONE outcome whose path
    is the vault target for the caller to clone into.
    """
    name = repo.get("name") or ""
    git_url = repo.get("git_url") or repo.get("url")

    # 1. explicit mapping wins
    configured = (repo_paths or {}).get(name)
    if configured:
        p = Path(configured)
        if p.is_dir():
            return ResolveOutcome(CONFIGURED, path=p)
        return ResolveOutcome(
            NONE,
            reason=f"repo_paths['{name}'] = {configured!r} does not exist; fix the mapping",
        )

    # 2. git-remote match against existing local checkouts
    if git_url:
        target = normalize_git_url(git_url)
        matches = [
            c for c in candidate_checkouts(search_roots)
            if (ru := remote_url_fn(c)) and normalize_git_url(ru) == target
        ]
        if len(matches) == 1:
            return ResolveOutcome(LINKED, path=matches[0])
        if len(matches) > 1:
            return ResolveOutcome(
                AMBIGUOUS,
                matches=matches,
                reason=(
                    f"{len(matches)} local checkouts match '{name}' "
                    f"({', '.join(str(m) for m in matches)}); "
                    f"set repo_paths['{name}'] in .rkm/config.json to disambiguate"
                ),
            )

    # 3. nothing local — caller clones (or archives a zip/folder source) here
    return ResolveOutcome(CLONE, path=vault_repo_dir / name)
