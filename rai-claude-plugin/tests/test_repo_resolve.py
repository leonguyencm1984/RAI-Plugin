"""Self-contained tests for repo resolution (T1, approach C).

Run with plain python (no pytest dependency required):
    python3 tests/test_repo_resolve.py

Covers all five resolution branches of resolve_repo() via an injected fake
remote_url_fn (no real git calls needed):
  - configured : repo_paths[<name>] points at an existing dir          → CONFIGURED
  - none       : repo_paths[<name>] points at a missing dir            → NONE (fail loud)
  - linked     : exactly one local checkout's origin matches git_url   → LINKED
  - ambiguous  : >1 local checkout matches git_url                     → AMBIGUOUS (fail loud)
  - clone      : no configured path and no remote match                → CLONE (vault target)

Plus the pure helpers: normalize_git_url and candidate_checkouts (depth-1 bound).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# mcp/ is a flat module dir (no __init__.py) — mirror how the MCP server loads it.
MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(MCP_DIR))

import repo_resolve as rr  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL") + f" — {label}")
    if not cond:
        _failures.append(label)


def _make_checkout(parent: Path, name: str) -> Path:
    """Create parent/<name>/.git so candidate_checkouts() treats it as a repo."""
    d = parent / name
    (d / ".git").mkdir(parents=True)
    return d


def test_normalize_git_url() -> None:
    cases = {
        "git@github.com:org/apollo.git": "https://github.com/org/apollo",
        "https://github.com/org/apollo.git": "https://github.com/org/apollo",
        "https://GitHub.com/Org/Apollo/": "https://github.com/org/apollo",
        "": "",
    }
    for raw, want in cases.items():
        check(rr.normalize_git_url(raw) == want, f"normalize_git_url({raw!r}) == {want!r}")


def test_candidate_checkouts_depth1() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_checkout(root, "alpha")          # git checkout — found
        (root / "plain").mkdir()               # no .git — ignored
        nested = root / "nested"               # a grandchild git dir must NOT be walked
        (nested / "deep" / ".git").mkdir(parents=True)
        cands = rr.candidate_checkouts([root, "/no/such/root/xyz"])
        names = sorted(c.name for c in cands)
        check(names == ["alpha"], f"candidate_checkouts is depth-1 only (got {names})")


def test_configured_branch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        checkout = root / "my-apollo"
        checkout.mkdir()
        out = rr.resolve_repo(
            {"name": "apollo", "git_url": "https://github.com/org/apollo.git"},
            repo_paths={"apollo": str(checkout)},
            search_roots=[],
            vault_repo_dir=root / "vault",
            remote_url_fn=lambda p: None,
        )
        check(out.source == rr.CONFIGURED, "configured: source == CONFIGURED")
        check(out.path == checkout, "configured: path is the configured checkout")


def test_none_branch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        out = rr.resolve_repo(
            {"name": "apollo", "git_url": "https://github.com/org/apollo.git"},
            repo_paths={"apollo": str(root / "does-not-exist")},
            search_roots=[],
            vault_repo_dir=root / "vault",
            remote_url_fn=lambda p: None,
        )
        check(out.source == rr.NONE, "none: missing configured path → source == NONE")
        check("does not exist" in out.reason, "none: reason explains the bad mapping")
        check(out.path is None, "none: no path returned")


def test_linked_branch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        match = _make_checkout(root, "apollo-local")
        _make_checkout(root, "unrelated")
        # Fake remotes: only apollo-local points at the target URL (SSH form, to
        # exercise normalization on both sides).
        remotes = {
            match: "git@github.com:org/apollo.git",
            root / "unrelated": "https://github.com/org/other.git",
        }
        out = rr.resolve_repo(
            {"name": "apollo", "git_url": "https://github.com/org/apollo"},
            repo_paths={},
            search_roots=[root],
            vault_repo_dir=root / "vault",
            remote_url_fn=lambda p: remotes.get(p),
        )
        check(out.source == rr.LINKED, "linked: single remote match → source == LINKED")
        check(out.path == match, "linked: path is the matching checkout")


def test_ambiguous_branch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = _make_checkout(root, "apollo-a")
        b = _make_checkout(root, "apollo-b")
        remotes = {
            a: "https://github.com/org/apollo.git",
            b: "git@github.com:org/apollo.git",  # same repo, different remote spelling
        }
        out = rr.resolve_repo(
            {"name": "apollo", "git_url": "https://github.com/org/apollo"},
            repo_paths={},
            search_roots=[root],
            vault_repo_dir=root / "vault",
            remote_url_fn=lambda p: remotes.get(p),
        )
        check(out.source == rr.AMBIGUOUS, "ambiguous: >1 match → source == AMBIGUOUS")
        check(sorted(out.matches) == sorted([a, b]), "ambiguous: both matches reported")
        check("disambiguate" in out.reason, "ambiguous: reason tells the user how to fix it")
        check(out.path is None, "ambiguous: no path auto-picked (Fork-2)")


def test_clone_branch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_checkout(root, "something-else")  # exists but does not match
        vault = root / "vault"
        out = rr.resolve_repo(
            {"name": "apollo", "git_url": "https://github.com/org/apollo.git"},
            repo_paths={},
            search_roots=[root],
            vault_repo_dir=vault,
            remote_url_fn=lambda p: "https://github.com/org/nope.git",
        )
        check(out.source == rr.CLONE, "clone: no match → source == CLONE")
        check(out.path == vault / "apollo", "clone: path is the vault clone target")


def test_clone_when_no_git_url() -> None:
    # A zip/folder-source repo (no git_url) can never link by remote → CLONE target.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        out = rr.resolve_repo(
            {"name": "docs-only"},
            repo_paths={},
            search_roots=[root],
            vault_repo_dir=root / "vault",
            remote_url_fn=lambda p: "anything",
        )
        check(out.source == rr.CLONE, "clone: no git_url → CLONE (archive path)")
        check(out.path == root / "vault" / "docs-only", "clone: vault target named after repo")


if __name__ == "__main__":
    test_normalize_git_url()
    test_candidate_checkouts_depth1()
    test_configured_branch()
    test_none_branch()
    test_linked_branch()
    test_ambiguous_branch()
    test_clone_branch()
    test_clone_when_no_git_url()
    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL TESTS PASSED")
