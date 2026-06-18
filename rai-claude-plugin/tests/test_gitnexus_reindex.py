"""Self-contained tests for the local GitNexus reindex path (T7 + T3).

Run with plain python (no pytest dependency required):
    python3 tests/test_gitnexus_reindex.py

Covers:
  - gitnexus_index: bin override, index_path_for, is_indexed, graceful missing
    binary, reindex gating on an existing index.
  - executor._maybe_reindex: None when no source repo, "no local gitnexus index"
    when the repo root is unindexed, and ran=True via a fake gitnexus module.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# mcp/ is a flat module dir (no __init__.py) — mirror how the MCP server loads it.
MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(MCP_DIR))

# executor imports PyYAML at module load, but these tests never parse YAML.
# Stub it so the suite runs without the dependency installed.
import types  # noqa: E402

sys.modules.setdefault("yaml", types.ModuleType("yaml"))

import gitnexus_index as gnx  # noqa: E402
import executor  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL") + f" — {label}")
    if not cond:
        _failures.append(label)


def test_gitnexus_index() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)

        # index_path_for / is_indexed
        check(gnx.index_path_for(repo) == repo / ".gitnexus", "index_path_for points at <repo>/.gitnexus")
        check(gnx.is_indexed(repo) is False, "is_indexed False with no .gitnexus")
        (repo / ".gitnexus").mkdir()
        check(gnx.is_indexed(repo) is True, "is_indexed True once .gitnexus exists")

        # bin override
        old = os.environ.get("RAI_GITNEXUS_BIN")
        os.environ["RAI_GITNEXUS_BIN"] = "rai-gitnexus-does-not-exist-xyz"
        try:
            check(gnx.gitnexus_bin() == "rai-gitnexus-does-not-exist-xyz", "RAI_GITNEXUS_BIN overrides binary")
            # graceful on missing binary — must NOT raise, returns (False, reason)
            ok, log = gnx.analyze(repo)
            check(ok is False and "not found" in log, "analyze degrades gracefully when binary missing")
            ok2, log2 = gnx.reindex(repo)  # index exists, so it proceeds → hits missing binary
            check(ok2 is False and "not found" in log2, "reindex runs (index present) and degrades gracefully")
        finally:
            if old is None:
                os.environ.pop("RAI_GITNEXUS_BIN", None)
            else:
                os.environ["RAI_GITNEXUS_BIN"] = old

    # reindex no-ops (no analyze attempt) when there is no index
    with tempfile.TemporaryDirectory() as td2:
        ok, log = gnx.reindex(Path(td2))
        check(ok is False and "skipping reindex" in log, "reindex skips cleanly when repo is unindexed")

    # analyze on a non-existent path is graceful
    ok, log = gnx.analyze(Path("/no/such/repo/path/xyz"))
    check(ok is False and "does not exist" in log, "analyze handles a missing repo path")


def test_maybe_reindex() -> None:
    # No source declared → None
    check(executor._maybe_reindex({}, "proj") is None, "_maybe_reindex None when no source repo")
    check(executor._maybe_reindex({"source": {"repo": "r"}}, None) is None, "_maybe_reindex None when no project slug")

    # Source declared but no local index → ran False, reason given
    res = executor._maybe_reindex({"source": {"repo": "ghost-repo"}}, "ghost-proj")
    check(res is not None and res.get("ran") is False, "_maybe_reindex ran=False when repo not indexed")

    # Success path via a fake gitnexus module (no real CLI / no real repo needed)
    class _FakeGnx:
        @staticmethod
        def is_indexed(p):  # noqa: ANN001
            return True

        @staticmethod
        def reindex(p):  # noqa: ANN001
            return True, "indexed 3 changed files"

    orig = executor._gnx
    executor._gnx = _FakeGnx
    try:
        res = executor._maybe_reindex({"source": {"repo": "r"}}, "proj")
        check(
            res == {"ran": True, "ok": True, "log": "indexed 3 changed files"},
            "_maybe_reindex returns ran=True/ok=True with fresh log on success",
        )
    finally:
        executor._gnx = orig

    # Module-unavailable fallback
    orig2 = executor._gnx
    executor._gnx = None
    try:
        res = executor._maybe_reindex({"source": {"repo": "r"}}, "proj")
        check(res == {"ran": False, "reason": "gitnexus_index module unavailable"},
              "_maybe_reindex reports module unavailable when _gnx is None")
    finally:
        executor._gnx = orig2


if __name__ == "__main__":
    test_gitnexus_index()
    test_maybe_reindex()
    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL TESTS PASSED")
