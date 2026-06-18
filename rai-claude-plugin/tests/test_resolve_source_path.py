"""Self-contained tests for source-path resolution + traversal guard (T2).

Run with plain python (no pytest dependency required):
    python3 tests/test_resolve_source_path.py

Covers executor._resolve_source_path / _resolve_repo_root:
  - reads resolved_path from .rkm-meta.json (linked/configured checkout)
  - falls back to the vault dir when the sidecar is missing or lacks resolved_path
  - rejects `..` traversal that escapes the repo root (S3 path-traversal)
  - treats an absolute-looking source.path as repo-relative (no escape)
  - returns None when no source / no project slug is declared
  - _resolve_repo_root targets the resolved checkout root (not a subdir)
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

# mcp/ is a flat module dir (no __init__.py) — mirror how the MCP server loads it.
MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(MCP_DIR))

# executor imports PyYAML at module load, but these tests never parse YAML.
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

import executor as ex  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL") + f" — {label}")
    if not cond:
        _failures.append(label)


def _vault(td: str, slug: str, repo: str, resolved_path: str | None = "MISSING") -> Path:
    """Create the vault repo dir and (optionally) a .rkm-meta.json sidecar.

    resolved_path="MISSING" → no sidecar; None → sidecar with no resolved_path;
    a string → sidecar pointing resolved_path at it.
    """
    ex.LOCAL_RKM = Path(td)
    vdir = Path(td) / "projects" / slug / "repos" / repo
    vdir.mkdir(parents=True)
    if resolved_path != "MISSING":
        meta = {"name": repo, "resolution_source": "linked", "resolved_path": resolved_path}
        (vdir / ".rkm-meta.json").write_text(json.dumps(meta))
    return vdir


def test_reads_resolved_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        checkout = Path(td) / "user-checkout" / "apollo"
        checkout.mkdir(parents=True)
        _vault(td, "proj", "apollo", str(checkout))
        p = ex._resolve_source_path({"source": {"repo": "apollo", "path": "src/main"}}, "proj")
        check(p == checkout / "src" / "main", "resolves against resolved_path (linked checkout) + subdir")
        root = ex._resolve_repo_root({"source": {"repo": "apollo", "path": "src/main"}}, "proj")
        check(root == checkout, "_resolve_repo_root returns the resolved checkout root, not the subdir")


def test_fallback_to_vault() -> None:
    with tempfile.TemporaryDirectory() as td:
        vdir = _vault(td, "proj", "apollo", resolved_path="MISSING")  # no sidecar
        p = ex._resolve_source_path({"source": {"repo": "apollo"}}, "proj")
        check(p == vdir, "no .rkm-meta.json → falls back to vault dir")
    with tempfile.TemporaryDirectory() as td:
        vdir = _vault(td, "proj", "apollo", resolved_path=None)  # sidecar, no resolved_path
        p = ex._resolve_source_path({"source": {"repo": "apollo", "path": "pkg"}}, "proj")
        check(p == vdir / "pkg", "sidecar without resolved_path → vault dir + subdir")


def test_traversal_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        checkout = Path(td) / "user-checkout" / "apollo"
        checkout.mkdir(parents=True)
        _vault(td, "proj", "apollo", str(checkout))
        for bad in ["../../etc/passwd", "foo/../../bar", "..", "a/../../b"]:
            raised = False
            try:
                ex._resolve_source_path({"source": {"repo": "apollo", "path": bad}}, "proj")
            except ValueError:
                raised = True
            check(raised, f"rejects traversal escaping repo root: {bad!r}")


def test_absolute_treated_as_relative() -> None:
    with tempfile.TemporaryDirectory() as td:
        checkout = Path(td) / "user-checkout" / "apollo"
        checkout.mkdir(parents=True)
        _vault(td, "proj", "apollo", str(checkout))
        p = ex._resolve_source_path({"source": {"repo": "apollo", "path": "/etc/passwd"}}, "proj")
        check(p == checkout / "etc" / "passwd", "absolute-looking source.path is treated as repo-relative (no escape)")


def test_no_source_or_project() -> None:
    check(ex._resolve_source_path({}, "proj") is None, "no source block → None")
    check(ex._resolve_source_path({"source": {}}, "proj") is None, "source without repo → None")
    check(ex._resolve_source_path({"source": {"repo": "apollo"}}, None) is None, "no project slug → None")
    check(ex._resolve_repo_root({"source": {"repo": "apollo"}}, None) is None, "repo_root None without project slug")


def test_source_resolution() -> None:
    """T6: _source_resolution surfaces the repo's .rkm-meta.json into the run log."""
    with tempfile.TemporaryDirectory() as td:
        ex.LOCAL_RKM = Path(td)
        vdir = Path(td) / "projects" / "proj" / "repos" / "apollo"
        vdir.mkdir(parents=True)
        (vdir / ".rkm-meta.json").write_text(json.dumps({
            "name": "apollo",
            "resolution_source": "linked",
            "resolved_path": "/Users/me/code/apollo",
            "index_status": "indexed",
            "local_indexed_commit": "9f1c2ab",
        }))
        info = ex._source_resolution({"source": {"repo": "apollo"}}, "proj")
        check(info is not None and info["resolution_source"] == "linked", "source_resolution reads resolution_source")
        check(info["resolved_path"] == "/Users/me/code/apollo", "source_resolution reads resolved_path")
        check(info["index_status"] == "indexed", "source_resolution reads index_status")
    with tempfile.TemporaryDirectory() as td2:
        ex.LOCAL_RKM = Path(td2)
        check(ex._source_resolution({}, "proj") is None, "source_resolution None when no source declared")
        check(ex._source_resolution({"source": {"repo": "ghost"}}, "proj") is None, "source_resolution None when no .rkm-meta.json")


if __name__ == "__main__":
    test_reads_resolved_path()
    test_fallback_to_vault()
    test_traversal_rejected()
    test_absolute_treated_as_relative()
    test_no_source_or_project()
    test_source_resolution()
    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL TESTS PASSED")
