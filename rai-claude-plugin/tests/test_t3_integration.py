"""Integration test for T3: a symbol written by a skill run is queryable after
the post-write reindex.

Run with plain python:
    python3 tests/test_t3_integration.py

Two parts:
  - test_dirty_detection: pure-git check of has_working_tree_changes()
    (clean → False, uncommitted write → True, non-git → None). No gitnexus needed.
  - test_written_symbol_queryable: the real end-to-end T3 acceptance criterion,
    using the actual `gitnexus` CLI. SKIPPED (not failed) when the binary is
    absent, so CI without gitnexus still passes.

The reindex policy under test ("force only when files changed"): incremental
analyze indexes only committed state, so an uncommitted file a skill just wrote
is invisible to it; reindex() must escalate to --force on a dirty tree to make
the new symbol queryable. Each test repo is removed from the gitnexus registry
in a finally so the global registry is left clean.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(MCP_DIR))

import gitnexus_index as gnx  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL") + f" — {label}")
    if not cond:
        _failures.append(label)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t3@test.local")
    _git(repo, "config", "user.name", "t3")


def test_dirty_detection() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        (repo / "a.py").write_text("def alpha():\n    return 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        check(gnx.has_working_tree_changes(repo) is False, "clean tree → has_working_tree_changes False")
        (repo / "b.py").write_text("def beta():\n    return 2\n")
        check(gnx.has_working_tree_changes(repo) is True, "uncommitted write → has_working_tree_changes True")
    with tempfile.TemporaryDirectory() as td2:
        check(gnx.has_working_tree_changes(Path(td2)) is None, "non-git dir → has_working_tree_changes None")


def test_written_symbol_queryable() -> None:
    if shutil.which("gitnexus") is None:
        print("SKIP — gitnexus binary not on PATH (integration test)")
        return

    td = tempfile.mkdtemp(prefix="rai-t3-")
    repo = Path(td)
    repo_name = repo.name  # gitnexus registers under the dir basename
    symbol = f"t3_written_symbol_{os.getpid()}"
    try:
        _init_repo(repo)
        (repo / "seed.py").write_text("def seed_fn():\n    return 0\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")

        # Initial index (what /rai:pull-repos does via _initial_index → analyze).
        ok, log = gnx.analyze(repo)
        check(ok, f"initial gitnexus index built (log tail: {log[-120:]!r})")
        check(gnx.is_indexed(repo), "repo is_indexed after initial analyze")

        # Simulate a skill writing a NEW symbol into the working tree (uncommitted).
        (repo / "generated.py").write_text(f"def {symbol}():\n    return 42\n")

        # Post-write reindex — must --force because the tree is dirty, else the
        # uncommitted symbol stays invisible.
        ok, log = gnx.reindex(repo)
        check(ok, f"reindex succeeded (log tail: {log[-120:]!r})")

        # The written symbol must now be queryable in the graph.
        q = subprocess.run(
            ["gitnexus", "cypher",
             f"MATCH (f:Function) WHERE f.name CONTAINS '{symbol}' RETURN f.name",
             "--repo", repo_name],
            capture_output=True, text=True, timeout=120,
        )
        found = symbol in (q.stdout or "")
        check(found, f"written symbol '{symbol}' is queryable after reindex")
        if not found:
            print(f"    query stdout: {(q.stdout or '')[:300]!r}")
            print(f"    query stderr: {(q.stderr or '')[:300]!r}")
    finally:
        # Always unregister the throwaway repo from the global registry.
        subprocess.run(["gitnexus", "remove", str(repo)],
                       capture_output=True, text=True)
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    test_dirty_detection()
    test_written_symbol_queryable()
    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL TESTS PASSED")
