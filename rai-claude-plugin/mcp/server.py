"""
RKMemoria Claude Code plugin — local MCP shim (stdio).

Reads ~/.rkm/config.json for platform URL + token.
Re-exposes platform REST API as MCP tools and adds pull/push tools
that write files to .rkm/ in the current working directory.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".rkm" / "config.json"
LOCAL_RKM = Path.cwd() / ".rkm"

DEFAULT_PLATFORM_URL: str = os.environ.get("RAI_PLATFORM_URL", "http://localhost:8001")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("Not logged in. Run /rai:login first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text())


def _etag_cache() -> dict:
    p = LOCAL_RKM / ".etag-cache.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_etag_cache(cache: dict) -> None:
    LOCAL_RKM.mkdir(parents=True, exist_ok=True)
    (LOCAL_RKM / ".etag-cache.json").write_text(json.dumps(cache, indent=2))


# Project-level config (.rkm/config.json) — stores platform_url, project_id,
# and the optional kb_dir override. Never stores the token (lives in ~/.rkm/).
PROJECT_CONFIG_PATH = LOCAL_RKM / "config.json"


def _load_project_config() -> dict:
    return json.loads(PROJECT_CONFIG_PATH.read_text()) if PROJECT_CONFIG_PATH.exists() else {}


def _save_project_config(data: dict) -> None:
    LOCAL_RKM.mkdir(parents=True, exist_ok=True)
    PROJECT_CONFIG_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Scope-aware path helpers
# Layout:
#   .rkm/org/                     — org-wide resources (skills, kb)
#   .rkm/projects/<slug>/         — per-project resources (skills, workflows, kb, repos)
# ---------------------------------------------------------------------------

def _project_slug(cfg: dict) -> str:
    """Return the project slug from config, falling back to 'project-<id>'."""
    return cfg.get("project_slug") or f"project-{cfg.get('project_id', 'unknown')}"


def _org_dir() -> Path:
    return LOCAL_RKM / "org"


def _project_dir(cfg: dict) -> Path:
    return LOCAL_RKM / "projects" / _project_slug(cfg)


def _kb_dir(base=None) -> Path:
    """KB root under a given scope base directory.

    When base is provided (scoped pull callers), returns base/kb.
    When base is None (legacy: _push_kb, _set_kb, _ingest, _rkm_status),
    falls back to the project config `kb_dir` override or .rkm/kb.
    """
    if base is not None:
        return base / "kb"
    raw = _load_project_config().get("kb_dir")
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p)
    return LOCAL_RKM / "kb"


# ---------------------------------------------------------------------------
# Native-Claude skill install
# Pulled bundles live in the .rkm vault (runnable via /rai:run-skill). To make a
# pulled skill *immediately* invocable as a native /slash-skill, we also mirror it
# into Claude Code's skill-discovery directory (.claude/skills/<slug>).
# ---------------------------------------------------------------------------

def _claude_skills_dir() -> Path:
    """Claude Code's project-level skill-discovery directory (.claude/skills)."""
    return Path.cwd() / ".claude" / "skills"


def _install_to_claude(slug: str, skill_dir: Path) -> None:
    """Mirror a pulled skill bundle into .claude/skills/<slug> so it is immediately
    invocable as a native /slash-skill — not only via /rai:run-skill.

    Behavior notes:
    - This rmtree+copytree from the vault on every install, so the **vault is the
      source of truth** — edit pulled skills in .rkm/.../skills/<slug>, NOT in
      .claude/skills/<slug> (direct edits there are overwritten on the next pull).
    - Both org and project skills install into the *project* .claude/skills, so an
      org skill becomes a native /skill in every workspace you pull it into.

    Best-effort: any failure is logged and swallowed. The skill is still usable from
    the vault, so a broken native install must never fail the pull.
    """
    import shutil
    try:
        target = _claude_skills_dir() / slug
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill_dir, target)
    except Exception as e:  # noqa: BLE001 — best-effort install, never fatal
        print(
            f"[rkm] warning: could not install skill '{slug}' into .claude/skills: {e}",
            file=sys.stderr,
        )


def _refresh_claude_skills_index(skills: list[dict]) -> None:
    """Merge the given skills (slug/name/runtime) into .claude/skills/_index.json so
    the index reflects what is installed for native discovery. Merge-only and
    best-effort — never fatal, never drops entries it didn't write."""
    try:
        idx_path = _claude_skills_dir() / "_index.json"
        existing: dict[str, dict] = {}
        if idx_path.exists():
            for item in json.loads(idx_path.read_text()):
                if isinstance(item, dict) and item.get("slug"):
                    existing[item["slug"]] = item
        for s in skills:
            existing[s["slug"]] = {
                "slug": s["slug"],
                "name": s.get("name", s["slug"]),
                "runtime": s.get("runtime", "prompt"),
            }
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(json.dumps(list(existing.values()), indent=2))
    except Exception as e:  # noqa: BLE001 — convenience index, never fatal
        print(f"[rkm] warning: could not refresh .claude/skills/_index.json: {e}", file=sys.stderr)


def _client(cfg: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.get("platform_url") or DEFAULT_PLATFORM_URL,
        headers={"Authorization": f"Bearer {cfg['token']}"},
        timeout=30,
    )


server = Server("rkmemoria-plugin")


# ---------------------------------------------------------------------------
# Tool list
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="rkm_login",
            description="Authenticate with the RKMemoria platform using the device authorization flow",
            inputSchema={
                "type": "object",
                "properties": {
                    "platform_url": {"type": "string", "description": "Platform base URL (default: http://localhost:8001)"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="rkm_logout",
            description=(
                "Log out from the RKMemoria platform: clears token + project binding from ~/.rkm/config.json. "
                "purge='keep' leaves the local .rkm/ vault untouched (default); 'archive' renames .rkm/ to "
                ".rkm-archive-<timestamp> (recommended when switching accounts); 'delete' permanently removes "
                "the vault contents and requires confirm=true. Server-side token revocation is NOT performed — "
                "revoke in the web UI under Settings → MCP Tokens."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "purge": {
                        "type": "string",
                        "enum": ["keep", "archive", "delete"],
                        "default": "keep",
                        "description": "What to do with the local .rkm/ vault (KB, skills, workflows, etag cache)",
                    },
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "Must be true when purge='delete' (irreversible)",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="rkm_status",
            description="Show what's pulled locally vs available on the platform",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="rkm_pull_workflows",
            description=(
                "Pull workflows from the platform to .rkm/projects/<slug>/workflows/. "
                "Workflows are always project-scoped; scope='org' is ignored."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "force": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="rkm_pull_skills",
            description=(
                "Pull skill bundles from the platform. "
                "Org-level skills (project_id=null) go to .rkm/org/skills/; "
                "project-level skills go to .rkm/projects/<slug>/skills/. "
                "scope: 'org'=org-only, 'project'=project-only, 'both'=all (default)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "slugs": {"type": "array", "items": {"type": "string"}},
                    "tag": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                    "scope": {"type": "string", "enum": ["org", "project", "both"], "default": "both"},
                },
            },
        ),
        types.Tool(
            name="rkm_pull_kb",
            description=(
                "Pull knowledge base from the platform. "
                "Org-visibility items go to .rkm/org/kb/; "
                "project-visibility items go to .rkm/projects/<slug>/kb/. "
                "scope: 'org'=org-only, 'project'=project-only, 'both'=all (default)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "wiki_only": {"type": "boolean", "default": False},
                    "sources_only": {"type": "boolean", "default": False},
                    "force": {"type": "boolean", "default": False},
                    "scope": {"type": "string", "enum": ["org", "project", "both"], "default": "both"},
                },
            },
        ),
        types.Tool(
            name="rkm_push_skill",
            description="Push a local skill bundle back to the platform",
            inputSchema={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Skill slug (folder name under .rkm/skills/)"},
                    "on_conflict": {"type": "string", "enum": ["replace", "rename", "skip"], "default": "replace"},
                    "scope": {"type": "string", "enum": ["org", "project"], "description": "Scope to search for skill dir (default: org-then-project fallback)"},
                },
                "required": ["slug"],
            },
        ),
        types.Tool(
            name="rkm_push_workflows",
            description=(
                "Push local workflow JSON files from .rkm/projects/<slug>/workflows/ back to the platform. "
                "Remaps skill slugs to current environment skill IDs. Workflows are project-scoped only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "slugs": {"type": "array", "items": {"type": "string"}, "description": "Specific workflow slugs to push (omit or use all:true for all)"},
                    "force": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="rkm_pull_repos",
            description=(
                "Resolve the current project's code repositories to one local checkout each "
                "(link-first: configured repo_paths → remote match → clone into the vault; "
                "fail loud on ambiguous/missing), build/refresh the local GitNexus index, and "
                "write per-repo .rkm-meta.json (resolved_path, resolution_source, "
                "local_indexed_commit, index_status). Skills resolve their source-code path "
                "against the recorded resolved_path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "default": False, "description": "Re-fetch and hard-reset existing checkouts"},
                    "shallow": {"type": "boolean", "default": False, "description": "git clone --depth 1"},
                    "names": {"type": "array", "items": {"type": "string"}, "description": "Only pull these repo names (default: all)"},
                },
            },
        ),
        types.Tool(
            name="rkm_list_skills",
            description=(
                "List available skills (org and/or project) without downloading bundles. "
                "Use this to browse skill names/slugs/tags before deciding which org skills to pull. "
                "Returns {\"org\": [...], \"project\": [...]} each containing {slug, name, tags, runtime, project_id, updated_at}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["org", "project", "both"],
                        "default": "both",
                    },
                },
            },
        ),
        types.Tool(
            name="rkm_list_workflows",
            description="List available workflows on the platform",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="rkm_run_workflow",
            description="Run a platform workflow by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "integer"},
                    "inputs": {"type": "object"},
                },
                "required": ["workflow_id"],
            },
        ),
        types.Tool(
            name="rkm_run_status",
            description="Get status of a skill run",
            inputSchema={
                "type": "object",
                "properties": {"run_id": {"type": "integer"}},
                "required": ["run_id"],
            },
        ),
        types.Tool(
            name="rkm_run_skill_streaming",
            description="Run a skill and stream stdout line-by-line until complete. Returns final output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer", "description": "ID of an already-created SkillRun"},
                },
                "required": ["run_id"],
            },
        ),
        types.Tool(
            name="rkm_download_artifact",
            description="Get a presigned download URL for a run artifact",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer"},
                    "filename": {"type": "string", "description": "Artifact filename to download"},
                },
                "required": ["run_id", "filename"],
            },
        ),
        types.Tool(
            name="search_knowledge",
            description="Search the RKMemoria knowledge base using semantic similarity",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_wiki_page",
            description="Retrieve a specific wiki page by slug",
            inputSchema={
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        ),
        types.Tool(
            name="rkm_ingest",
            description=(
                "Extract text from a local source (file, folder, zip, URL, or raw text) "
                "and save it to .rkm/kb/sources/. Returns the extracted raw_text so the "
                "agent can generate wiki pages. Supported file types: "
                ".txt .md .pdf .docx .xlsx .xls .csv; folders and .zip archives."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Human-readable title for this source"},
                    "text": {"type": "string", "description": "Raw text to ingest directly"},
                    "url": {"type": "string", "description": "URL to fetch and extract"},
                    "file": {"type": "string", "description": "Absolute path to a local file"},
                    "folder": {"type": "string", "description": "Absolute path to a local directory or .zip archive"},
                    "project_id": {"type": "integer", "description": "Project ID (defaults to config project_id)"},
                    "knowledge_type_id": {"type": "integer"},
                },
                "required": ["title"],
            },
        ),
        types.Tool(
            name="rkm_push_kb",
            description=(
                "Push local knowledge base to the platform. "
                "Reads from .rkm/org/kb/ (scope='org'), .rkm/projects/<slug>/kb/ (scope='project'), "
                "or both (default). Uploads sources and wiki pages; skips unchanged via etag cache."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "wiki_only": {"type": "boolean", "default": False},
                    "sources_only": {"type": "boolean", "default": False},
                    "force": {"type": "boolean", "default": False},
                    "scope": {"type": "string", "enum": ["org", "project", "both"], "default": "both"},
                },
            },
        ),
        types.Tool(
            name="rkm_set_kb",
            description=(
                "Set the local knowledge-base folder (persisted in project .rkm/config.json). "
                "Default is .rkm/kb. Pass clear=true to reset to the default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Folder to use as KB root (absolute, or relative to project root)"},
                    "clear": {"type": "boolean", "default": False, "description": "Reset to the default .rkm/kb"},
                },
            },
        ),
        types.Tool(
            name="rkm_push_run",
            description=(
                "Record an already-completed LOCAL skill run on the platform so it appears in the "
                "run history / audit log (the /rai:run-skill --push action). Resolves the skill slug "
                "to its platform id and POSTs to /api/v1/skill-runs/record — this does NOT re-execute "
                "the skill. Call after a local script/hybrid run completes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Skill slug that was run locally"},
                    "status": {"type": "string", "enum": ["succeeded", "failed", "cancelled"], "default": "succeeded"},
                    "input_md": {"type": "string", "default": ""},
                    "output_md": {"type": "string", "description": "Run output / summary"},
                    "error": {"type": "string", "description": "Error message if the run failed"},
                },
                "required": ["slug"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # Local-only tools that do not require existing config
    if name == "rkm_login":
        try:
            result = await _login(arguments)
        except Exception as exc:
            result = {"error": str(exc)}
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    if name == "rkm_logout":
        try:
            result = await _logout(arguments)
        except Exception as exc:
            result = {"error": str(exc)}
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    if name == "rkm_set_kb":
        try:
            result = _set_kb(arguments)
        except Exception as exc:
            result = {"error": str(exc)}
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    cfg = _load_config()
    try:
        if name == "rkm_status":
            result = await _rkm_status(cfg)
        elif name == "rkm_pull_workflows":
            result = await _pull_workflows(cfg, arguments)
        elif name == "rkm_pull_skills":
            result = await _pull_skills(cfg, arguments)
        elif name == "rkm_pull_kb":
            result = await _pull_kb(cfg, arguments)
        elif name == "rkm_push_skill":
            result = await _push_skill(cfg, arguments)
        elif name == "rkm_pull_repos":
            result = await _pull_repos(cfg, arguments)
        elif name == "rkm_list_skills":
            result = await _list_skills(cfg, arguments)
        elif name == "rkm_list_workflows":
            result = await _list_workflows(cfg)
        elif name == "rkm_run_workflow":
            result = await _run_workflow(cfg, arguments)
        elif name == "rkm_run_status":
            result = await _run_status(cfg, arguments)
        elif name == "rkm_run_skill_streaming":
            result = await _run_skill_streaming(cfg, arguments)
        elif name == "rkm_push_run":
            result = await _push_run(cfg, arguments)
        elif name == "rkm_download_artifact":
            result = await _download_artifact(cfg, arguments)
        elif name == "search_knowledge":
            result = await _search_knowledge(cfg, arguments)
        elif name == "get_wiki_page":
            result = await _get_wiki_page(cfg, arguments)
        elif name == "rkm_ingest":
            result = await _ingest(cfg, arguments)
        elif name == "rkm_push_kb":
            result = await _push_kb(cfg, arguments)
        elif name == "rkm_push_workflows":
            result = await _push_workflows(cfg, arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except httpx.HTTPStatusError as exc:
        # Preserve HTTP status so callers can distinguish 401/403/404/422
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text
        result = {
            "error": f"HTTP {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "detail": detail,
        }
    except Exception as exc:
        result = {"error": str(exc)}
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

async def _rkm_status(cfg: dict) -> dict:
    async with _client(cfg) as c:
        project_id = cfg.get("project_id")
        if project_id is not None:
            manifest_resp = await c.get(f"/api/v1/projects/{project_id}/manifest")
            manifest_resp.raise_for_status()
            manifest = manifest_resp.json()
        else:
            manifest = {"workflows_count": None, "skills_count": None, "repos_count": None, "sources_count": None, "wiki_count": None}

    etag_cache = _etag_cache()
    org = _org_dir()
    proj = _project_dir(cfg)

    def _count(path, pattern):
        return len(list(path.glob(pattern))) if path.exists() else 0

    def _count_dirs(path):
        return len([p for p in path.iterdir() if p.is_dir()]) if path.exists() else 0

    pulled_skills_org = _count_dirs(org / "skills")
    pulled_skills_proj = _count_dirs(proj / "skills")
    pulled_workflows = _count(proj / "workflows", "*.json")
    pulled_wiki_org = _count(org / "kb" / "wiki", "*.md")
    pulled_wiki_proj = _count(proj / "kb" / "wiki", "*.md")
    pulled_sources_org = _count(org / "kb" / "sources", "*.json")
    pulled_sources_proj = _count(proj / "kb" / "sources", "*.json")

    return {
        "platform": cfg.get("platform_url") or DEFAULT_PLATFORM_URL,
        "project": _project_slug(cfg),
        "project_id": project_id,
        "workflows": {"pulled": pulled_workflows, "available": manifest["workflows_count"]},
        "skills": {
            "pulled": pulled_skills_org + pulled_skills_proj,
            "org": pulled_skills_org,
            "project": pulled_skills_proj,
            "available": manifest["skills_count"],
        },
        "repos": {"available": manifest["repos_count"]},
        "sources": {
            "pulled": pulled_sources_org + pulled_sources_proj,
            "org": pulled_sources_org,
            "project": pulled_sources_proj,
            "available": manifest["sources_count"],
        },
        "wiki": {
            "pulled": pulled_wiki_org + pulled_wiki_proj,
            "org": pulled_wiki_org,
            "project": pulled_wiki_proj,
            "available": manifest["wiki_count"],
        },
        "etag_entries": len(etag_cache),
    }


# ---------------------------------------------------------------------------
# Pull workflows
# ---------------------------------------------------------------------------

async def _pull_workflows(cfg: dict, args: dict) -> dict:
    # Workflows are always project-scoped; org scope is not applicable.
    if args.get("scope") == "org":
        return {"pulled": [], "skipped_etag_match": [], "note": "Workflows are project-scoped only; org scope skipped."}

    force = args.get("force", False)
    etag_cache = _etag_cache()
    dest = _project_dir(cfg) / "workflows"
    dest.mkdir(parents=True, exist_ok=True)

    async with _client(cfg) as c:
        resp = await c.get("/api/v1/workflows/mine")
        resp.raise_for_status()
        workflows = resp.json()

    if not args.get("all") and args.get("ids"):
        id_set = set(args["ids"])
        workflows = [w for w in workflows if w["id"] in id_set]

    pulled, skipped = [], []
    for wf in workflows:
        key = f"workflow:{wf['id']}"
        etag = str(wf.get("updated_at", ""))
        if not force and etag_cache.get(key) == etag:
            skipped.append(wf["slug"])
            continue
        (dest / f"{wf['slug']}.json").write_text(json.dumps(wf, indent=2))
        etag_cache[key] = etag
        pulled.append(wf["slug"])

    _save_etag_cache(etag_cache)
    index = [{"id": w["id"], "slug": w["slug"], "name": w["name"]} for w in workflows]
    (dest / "_index.json").write_text(json.dumps(index, indent=2))
    return {"pulled": pulled, "skipped_etag_match": skipped}


# ---------------------------------------------------------------------------
# Pull skills
# ---------------------------------------------------------------------------

async def _pull_skills(cfg: dict, args: dict) -> dict:
    import zipfile, io as _io
    force = args.get("force", False)
    scope = args.get("scope", "both")  # "org" | "project" | "both"
    etag_cache = _etag_cache()

    async with _client(cfg) as c:
        resp = await c.get("/api/v1/skills/", params={"limit": 100})
        resp.raise_for_status()
        skills = resp.json()["items"]

    if not args.get("all"):
        if args.get("slugs"):
            slug_set = set(args["slugs"])
            skills = [s for s in skills if s["slug"] in slug_set]
        if args.get("tag"):
            tag = args["tag"]
            skills = [s for s in skills if tag in (s.get("tags") or [])]

    # Partition by scope: project_id=None → org-level, else project-level
    org_skills = [s for s in skills if s.get("project_id") is None]
    project_skills = [s for s in skills if s.get("project_id") is not None]

    buckets = []
    if scope in ("org", "both"):
        buckets.append((_org_dir() / "skills", org_skills))
    if scope in ("project", "both"):
        buckets.append((_project_dir(cfg) / "skills", project_skills))

    pulled, skipped, conflicts = [], [], []
    for dest, bucket in buckets:
        dest.mkdir(parents=True, exist_ok=True)
        for skill in bucket:
            slug = skill["slug"]
            key = f"skill:{slug}"
            etag = str(skill.get("updated_at", ""))
            skill_dir = dest / slug

            claude_target = _claude_skills_dir() / slug
            # Up-to-date only when the etag matches AND the bundle is on disk AND it
            # is installed for native discovery. A matching etag with a missing
            # bundle (vault cleared but cache kept) must not short-circuit —
            # otherwise the user "pulls" a skill and silently gets nothing.
            if (
                not force
                and etag_cache.get(key) == etag
                and skill_dir.exists()
                and claude_target.exists()
            ):
                skipped.append(slug)
                continue
            # Etag matches and the bundle is present, but the native install
            # drifted (missing) — repair it from the existing vault bundle without
            # a re-download, then skip.
            if not force and etag_cache.get(key) == etag and skill_dir.exists():
                _install_to_claude(slug, skill_dir)
                skipped.append(slug)
                continue

            # Check for local edits by comparing stored marker
            if skill_dir.exists() and not force:
                marker = skill_dir / ".rkm-etag"
                if marker.exists() and marker.read_text() != etag:
                    conflicts.append(slug)
                    continue

            async with _client(cfg) as c:
                bundle_resp = await c.get(f"/api/v1/skills/{slug}/bundle")
                if bundle_resp.status_code == 200:
                    skill_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(_io.BytesIO(bundle_resp.content)) as zf:
                        zf.extractall(skill_dir)
                    (skill_dir / ".rkm-etag").write_text(etag)
                    etag_cache[key] = etag
                    # Mirror into Claude's skill-discovery path so the freshly
                    # pulled skill is immediately invocable as a native /slash-skill.
                    _install_to_claude(slug, skill_dir)
                    pulled.append(slug)
                else:
                    conflicts.append(slug)

        # Ensure every on-disk skill has its output/ folder so runs have a stable
        # destination: .rkm/.../skills/<slug>/output (req 3). KB push stays opt-in.
        for skill in bucket:
            sd = dest / skill["slug"]
            if sd.exists():
                (sd / "output").mkdir(exist_ok=True)

        # Write scoped index
        index = [{"slug": s["slug"], "name": s["name"], "runtime": s.get("runtime", "prompt")} for s in bucket]
        (dest / "_index.json").write_text(json.dumps(index, indent=2))

    # Regenerate scope INDEXes so skills appear in the Obsidian graph.
    # Uses disk-fallback (all_pages=None) — no wiki re-fetch needed.
    for dest, _bucket in buckets:
        scope_dir = dest.parent  # dest = <scope>/skills → parent is org/ or projects/<slug>/
        label = "Org" if scope_dir == _org_dir() else _project_slug(cfg)
        _write_scope_index(None, scope_dir, label)
    if buckets:
        _write_root_index()

    # Refresh the native-discovery index for everything installed this call.
    _installed = set(pulled) | set(skipped)
    _refresh_claude_skills_index(
        [s for _dest, bucket in buckets for s in bucket if s["slug"] in _installed]
    )

    _save_etag_cache(etag_cache)
    return {
        "pulled": pulled,
        "skipped_etag_match": skipped,
        "conflicts": conflicts,
        "org_skills": len(org_skills),
        "project_skills": len(project_skills),
    }


# ---------------------------------------------------------------------------
# Pull KB — helpers
# ---------------------------------------------------------------------------

def _extract_items(resp_data: Any) -> list:
    """Extract the items list from a paginated or plain-list API response."""
    if isinstance(resp_data, dict) and "items" in resp_data:
        return resp_data["items"]
    return resp_data  # legacy bare-list fallback


def _wiki_frontmatter(page: dict) -> str:
    """Render YAML frontmatter for an Obsidian note from a WikiPageResponse dict."""
    def _esc(v: str) -> str:
        return str(v).replace('"', '\\"')

    source_ids = page.get("source_ids") or []
    source_ids_str = "[" + ", ".join(str(i) for i in source_ids) + "]"
    lines = [
        "---",
        f'title: "{_esc(page.get("title", ""))}"',
        f'slug: {page.get("slug", "")}',
        f'summary: "{_esc(page.get("summary", ""))}"',
        f'project_id: {page.get("project_id")}',
        f'knowledge_type_id: {page.get("knowledge_type_id")}',
        f'version: {page.get("version", 1)}',
        f'created: {page.get("created_at", "")}',
        f'updated: {page.get("updated_at", "")}',
        f'source_ids: {source_ids_str}',
        "tags: [rkmemoria, wiki]",
        "---",
    ]
    return "\n".join(lines)


def _ensure_vault_obsidian() -> None:
    """Create .rkm/.obsidian/app.json once — single vault root for Obsidian."""
    obsidian_dir = LOCAL_RKM / ".obsidian"
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        app_json.write_text(json.dumps({"alwaysUpdateLinks": True}, indent=2))


# ---------------------------------------------------------------------------
# Pull KB — cross-linking helpers
# ---------------------------------------------------------------------------

def _screen_code(slug: str) -> "str | None":
    """Extract the screen code prefix from a slug (e.g. 'm002', 'p001-2', 'pxxx').

    Returns None for non-screen pages (org index pages, ute-meta pages, etc.).
    """
    m = re.match(r'^([mpr](?:xxx|20x|\d{3})(?:-\d+)*)', slug)
    return m.group(1) if m else None


def _build_link_index(pages: list) -> dict:
    """Build a link-resolution index for a set of wiki pages.

    Returns:
    - by_code:     screen_code -> {"design": [slug...], "ut": [slug...]}
    - slugs:       set of all real slugs in this scope
    - title_of:    slug -> title
    - alias_table: short broken slug -> real slug (static)
    """
    by_code: dict = {}
    slugs: set = set()
    title_of: dict = {}

    for page in pages:
        slug = page.get("slug", "")
        title = page.get("title", "")
        slugs.add(slug)
        title_of[slug] = title

        code = _screen_code(slug)
        if code is None:
            continue
        bucket = by_code.setdefault(code, {"design": [], "ut": []})
        bucket["ut" if slug.endswith("-unit-tests") else "design"].append(slug)

    alias_table: dict = {
        "ute-overview": "sanyu-adelie-ute-overview-unit-test-specifications",
        "ute-testcase-translation": "ute-testcase-translation-management",
    }

    return {"by_code": by_code, "slugs": slugs, "title_of": title_of, "alias_table": alias_table}


def _related_section(slug: str, index: dict) -> str:
    """Return a '## Related' section linking design<->UTE counterparts, or '' if none."""
    code = _screen_code(slug)
    if not code:
        return ""
    by_code = index["by_code"]
    if code not in by_code:
        return ""
    title_of = index["title_of"]
    is_ut = slug.endswith("-unit-tests")
    partners = by_code[code]["design" if is_ut else "ut"]
    if not partners:
        return ""
    label = "Detail Design" if is_ut else "Unit Tests"
    link_lines = "\n".join(
        f"- **{label}:** [[{ps}|{title_of.get(ps, ps)}]]"
        for ps in sorted(partners)
    )
    return f"\n\n---\n\n## Related\n\n{link_lines}\n"


def _resolve_wikilinks(body: str, index: dict) -> str:
    """Repair broken wikilinks by mapping short/canonical slugs to real filenames.

    Targets that cannot be resolved unambiguously are left untouched (safe no-op).
    """
    slugs = index["slugs"]
    alias_table = index["alias_table"]
    by_code = index["by_code"]
    pattern = re.compile(r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]')

    def _fix(m: "re.Match") -> str:
        target = m.group(1).strip()
        orig_alias = m.group(2)

        if target in slugs:
            return m.group(0)  # already resolves

        if target in alias_table:
            real = alias_table[target]
            return f"[[{real}|{orig_alias or target}]]"

        lookup = target[4:] if target.startswith("ute-") else target
        code = _screen_code(lookup)
        if not code or code not in by_code:
            return m.group(0)

        is_ut_target = target.endswith("-unit-tests") or target.startswith("ute-")
        candidates = by_code[code]["ut" if is_ut_target else "design"]
        if not candidates:
            candidates = by_code[code]["design" if is_ut_target else "ut"]
        if len(candidates) == 1:
            real = candidates[0]
            return f"[[{real}|{orig_alias or target}]]"
        return m.group(0)  # ambiguous — leave untouched

    return pattern.sub(_fix, body)


def _read_skills_index(scope_dir: "Path") -> list:
    """Load scope_dir/skills/_index.json, returning [] if absent or unreadable."""
    index_path = scope_dir / "skills" / "_index.json"
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text())
    except Exception:
        return []


def _read_wiki_pages_on_disk(wiki_dir: "Path") -> list:
    """Parse frontmatter from wiki .md files (written by _wiki_frontmatter) to
    reconstruct minimal page metadata: {slug, title, knowledge_type_id}.
    Used as a fallback when _write_scope_index is called without live page data
    (e.g. during a skills-only pull).
    """
    pages = []
    if not wiki_dir.exists():
        return pages
    for md_file in sorted(wiki_dir.glob("*.md")):
        if md_file.name == "INDEX.md":
            continue
        try:
            text = md_file.read_text()
        except OSError:
            continue
        fm_match = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
        if not fm_match:
            continue
        fm = fm_match.group(1)
        slug_m = re.search(r'^slug:\s*(.+)$', fm, re.MULTILINE)
        title_m = re.search(r'^title:\s*"?(.*?)"?$', fm, re.MULTILINE)
        kt_m = re.search(r'^knowledge_type_id:\s*(.+)$', fm, re.MULTILINE)
        if not slug_m:
            continue
        slug = slug_m.group(1).strip()
        title = title_m.group(1).strip() if title_m else slug
        kt_raw = kt_m.group(1).strip() if kt_m else None
        kt = None
        if kt_raw and kt_raw not in ("None", "null", ""):
            try:
                kt = int(kt_raw)
            except ValueError:
                kt = kt_raw
        pages.append({"slug": slug, "title": title, "knowledge_type_id": kt})
    return pages


def _skills_section_lines(scope_dir: "Path") -> list:
    """Return markdown lines for a '## Skills' section, or [] if the scope has none."""
    skills = _read_skills_index(scope_dir)
    if not skills:
        return []
    try:
        rel_prefix = scope_dir.relative_to(LOCAL_RKM)
    except ValueError:
        rel_prefix = scope_dir.name
    result = ["## Skills", ""]
    for s in sorted(skills, key=lambda x: x.get("name", "")):
        slug = s["slug"]
        name = s.get("name", slug)
        result.append(f"- [[{rel_prefix}/skills/{slug}/SKILL|{name}]]")
    result.append("")
    return result


def _write_scope_index(all_pages, scope_dir: "Path", scope_label: str) -> None:
    """Write an INDEX.md (Map of Content) into a scope directory (org/ or projects/<slug>/).

    all_pages: list of wiki page dicts, or None to load from disk (skills-only pull fallback).
    """
    if all_pages is None:
        all_pages = _read_wiki_pages_on_disk(scope_dir / "kb" / "wiki")

    link_index = _build_link_index(all_pages)
    by_code = link_index["by_code"]
    title_of = link_index["title_of"]

    # Partition into screen-coded pages vs. everything else
    other_pages = [p for p in all_pages if _screen_code(p.get("slug", "")) is None]

    lines = [
        f"# Knowledge Base — {scope_label}",
        "",
        "_Auto-generated by `/rai:pull`. Open `.rkm/` as an Obsidian vault._",
        "",
    ]

    # --- Screens section: grouped by screen code, Design + Unit Tests nested ---
    if by_code:
        lines.append("## Screens")
        lines.append("")
        for code in sorted(by_code):
            lines.append(f"### {code}")
            lines.append("")
            for design_slug in sorted(by_code[code]["design"]):
                lines.append(
                    f"- **Design:** [[{design_slug}|{title_of.get(design_slug, design_slug)}]]"
                )
            for ut_slug in sorted(by_code[code]["ut"]):
                lines.append(
                    f"- **Unit Tests:** [[{ut_slug}|{title_of.get(ut_slug, ut_slug)}]]"
                )
            lines.append("")

    # --- Other pages: policies, ute-meta, test-page, etc. ---
    if other_pages:
        lines.append("## Other pages")
        lines.append("")
        for p in sorted(other_pages, key=lambda x: x.get("title", "")):
            lines.append(f"- [[{p['slug']}|{p['title']}]]")
        lines.append("")

    # --- Cross-scope navigation ---
    lines.append("---")
    lines.append("")
    projects_dir = LOCAL_RKM / "projects"
    if scope_label == "Org":
        if projects_dir.exists():
            proj_slugs = sorted(d.name for d in projects_dir.iterdir() if d.is_dir())
            if proj_slugs:
                lines.append("## Related Projects")
                lines.append("")
                for ps in proj_slugs:
                    lines.append(f"- [[projects/{ps}/kb/wiki/INDEX|{ps}]]")
                lines.append("")
    else:
        lines.append("## Org Knowledge Base")
        lines.append("")
        lines.append("- [[org/kb/wiki/INDEX|Org Knowledge Base]]")
        lines.append("")

    # --- Skills ---
    lines.extend(_skills_section_lines(scope_dir))

    wiki_dir = scope_dir / "kb" / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "INDEX.md").write_text("\n".join(lines))


def _write_root_index() -> None:
    """Regenerate .rkm/INDEX.md linking into org/ and projects/<slug>/."""
    lines = [
        "# RKMemoria Vault",
        "",
        "_Auto-generated by `/rai:pull`. Open this folder as an Obsidian vault._",
        "",
        "## Org-wide resources",
        "",
        "- [[org/kb/wiki/INDEX|Org Knowledge Base]]",
        "",
        "## Projects",
        "",
    ]
    projects_dir = LOCAL_RKM / "projects"
    if projects_dir.exists():
        for slug_dir in sorted(projects_dir.iterdir()):
            if slug_dir.is_dir():
                n_skills = len(_read_skills_index(slug_dir))
                if n_skills == 1:
                    skill_note = " — 1 skill"
                elif n_skills > 1:
                    skill_note = f" — {n_skills} skills"
                else:
                    skill_note = ""
                lines.append(f"- [[projects/{slug_dir.name}/kb/wiki/INDEX|{slug_dir.name}]]{skill_note}")
    lines.append("")
    (LOCAL_RKM / "INDEX.md").write_text("\n".join(lines))


# Keep legacy name as alias so any external callers are unbroken
def _write_vault_scaffold(all_pages: list, vault_dir: "Path") -> None:
    """Legacy shim: write scope INDEX.md. .obsidian now lives at .rkm/ root."""
    _ensure_vault_obsidian()
    # Derive a label from the vault_dir name (e.g. "wiki" → parent scope dir)
    scope_dir = vault_dir.parent.parent  # vault_dir = <scope>/kb/wiki
    scope_label = scope_dir.name.replace("-", " ").title()
    _write_scope_index(all_pages, scope_dir, scope_label)
    _write_root_index()


# ---------------------------------------------------------------------------
# Pull KB
# ---------------------------------------------------------------------------

async def _fetch_all_pages(client: "httpx.AsyncClient", url: str, params: dict) -> list:
    """Fetch all items from a paginated endpoint (max limit=100 per page)."""
    items: list = []
    page = 1
    while True:
        resp = await client.get(url, params={**params, "page": page, "limit": 100})
        resp.raise_for_status()
        data = resp.json()
        batch = _extract_items(data)
        items.extend(batch)
        total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
        if len(items) >= total or not batch:
            break
        page += 1
    return items


async def _pull_kb(cfg: dict, args: dict) -> dict:
    force = args.get("force", False)
    wiki_only = args.get("wiki_only", False)
    sources_only = args.get("sources_only", False)
    scope = args.get("scope", "both")  # "org" | "project" | "both"
    project_id = cfg.get("project_id")
    etag_cache = _etag_cache()
    pulled_wiki, pulled_sources, skipped = [], [], []

    _ensure_vault_obsidian()

    if not sources_only:
        async with _client(cfg) as c:
            all_pages = await _fetch_all_pages(c, "/api/v1/wiki/", {"project_id": project_id})

        # Partition by visibility
        org_pages = [p for p in all_pages if p.get("visibility") == "org"]
        proj_pages = [p for p in all_pages if p.get("visibility") != "org"]

        wiki_buckets = []
        if scope in ("org", "both"):
            wiki_buckets.append((_kb_dir(_org_dir()) / "wiki", org_pages, "Org"))
        if scope in ("project", "both"):
            wiki_buckets.append((_kb_dir(_project_dir(cfg)) / "wiki", proj_pages, _project_slug(cfg)))

        for wiki_dest, pages, label in wiki_buckets:
            wiki_dest.mkdir(parents=True, exist_ok=True)
            link_index = _build_link_index(pages)
            for page in pages:
                key = f"wiki:{page['slug']}"
                etag = str(page.get("updated_at", ""))
                if not force and etag_cache.get(key) == etag:
                    skipped.append(page["slug"])
                    continue
                body = _resolve_wikilinks(page.get("content_md", ""), link_index)
                body += _related_section(page["slug"], link_index)
                content = f"{_wiki_frontmatter(page)}\n\n# {page['title']}\n\n{body}"
                (wiki_dest / f"{page['slug']}.md").write_text(content)
                etag_cache[key] = etag
                pulled_wiki.append(page["slug"])
            # Regenerate scope INDEX.md + root INDEX.md on every pull
            _write_scope_index(pages, wiki_dest.parent.parent, label)
        _write_root_index()

    if not wiki_only:
        async with _client(cfg) as c:
            sources = await _fetch_all_pages(c, "/api/v1/sources/", {"project_id": project_id})

        org_sources = [s for s in sources if s.get("visibility") == "org"]
        proj_sources = [s for s in sources if s.get("visibility") != "org"]

        src_buckets = []
        if scope in ("org", "both"):
            src_buckets.append((_kb_dir(_org_dir()) / "sources", org_sources))
        if scope in ("project", "both"):
            src_buckets.append((_kb_dir(_project_dir(cfg)) / "sources", proj_sources))

        for sources_dest, bucket in src_buckets:
            sources_dest.mkdir(parents=True, exist_ok=True)
            for src in bucket:
                key = f"source:{src['id']}"
                etag = str(src.get("updated_at", src.get("created_at", "")))
                if not force and etag_cache.get(key) == etag:
                    skipped.append(f"source:{src['id']}")
                    continue
                (sources_dest / f"{src['id']}.json").write_text(json.dumps(src, indent=2))
                etag_cache[key] = etag
                pulled_sources.append(src["id"])

    _save_etag_cache(etag_cache)
    return {"pulled_wiki": pulled_wiki, "pulled_sources": pulled_sources, "skipped_etag_match": skipped}


# ---------------------------------------------------------------------------
# Push skill
# ---------------------------------------------------------------------------

# Pack/sign helpers live in skill_packager.py (shared with /rai:build-skill CLI).
from skill_packager import _pack_skill, _sign_bundle  # noqa: E402


async def _push_skill(cfg: dict, args: dict) -> dict:
    slug = args["slug"]
    on_conflict = args.get("on_conflict", "replace")
    scope = args.get("scope")  # "org" | "project" | None (search both)
    # Resolve skill directory based on scope
    if scope == "org":
        candidates = [_org_dir() / "skills" / slug]
    elif scope == "project":
        candidates = [_project_dir(cfg) / "skills" / slug]
    else:
        # Default: search org then project
        candidates = [
            _org_dir() / "skills" / slug,
            _project_dir(cfg) / "skills" / slug,
        ]
    skill_dir = next((p for p in candidates if p.exists()), None)
    if skill_dir is None:
        return {"error": f"Skill '{slug}' not found in .rkm/org/skills/ or .rkm/projects/<slug>/skills/"}

    zip_bytes = _sign_bundle(_pack_skill(skill_dir), slug)

    project_id = cfg.get("project_id")
    files = {"file": (f"{slug}.skillpack", zip_bytes, "application/zip")}
    data: dict[str, Any] = {"on_conflict": on_conflict}
    if project_id is not None:
        data["project_id"] = str(project_id)

    async with _client(cfg) as c:
        resp = await c.post("/api/v1/skills/import", files=files, data=data)
        resp.raise_for_status()
    result = resp.json()
    # Surface approval status clearly for the IDE user
    if result.get("import_status") == "pending":
        result["message"] = (
            f"Push accepted — pending owner approval. "
            f"Version ID: {result.get('pending_version_id')}. "
            f"Owner will receive a notification to review at /admin/approvals."
        )
    else:
        result["message"] = f"Push successful — skill '{slug}' is now live."
    return result


# ---------------------------------------------------------------------------
# Pull repos
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    """Run a git command, returning (returncode, combined_output). Best-effort:
    a missing git binary surfaces as returncode 127 rather than an exception."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "git binary not found on PATH"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


# Optional sibling modules — repo resolution (T1) + local GitNexus indexing.
# Degrade gracefully if unavailable so a pull never hard-fails on import.
try:
    import repo_resolve as _repo_resolve  # noqa: E402
except Exception:  # noqa: BLE001
    _repo_resolve = None
try:
    import gitnexus_index as _gnx  # noqa: E402
except Exception:  # noqa: BLE001
    _gnx = None


def _head_commit(path: Path) -> str | None:
    """`git rev-parse HEAD` for the local_indexed_commit metadata, or None when
    the path is not a git checkout (e.g. a zip/folder-source repo)."""
    if not (path / ".git").is_dir():
        return None
    rc, out = _git(["-C", str(path), "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else None


def _initial_index(path: Path) -> str:
    """Build (or incrementally refresh) the local GitNexus index for a resolved
    checkout and return an index_status: 'indexed' | 'failed' | 'unavailable'.

    `gitnexus analyze` is incremental by default, so this one call both builds the
    first index and keeps an existing one fresh. skip_git is derived from the
    presence of a .git dir so folder-source checkouts still index.
    """
    if _gnx is None:
        return "unavailable"
    skip_git = not (path / ".git").is_dir()
    ok, _log = _gnx.analyze(path, skip_git=skip_git)
    return "indexed" if ok else "failed"


def _clone_or_update_repo(repo: dict, repo_dir: Path, shallow: bool, force: bool) -> str:
    """Clone a repo's code into repo_dir or fast-forward an existing checkout.

    Returns one of: 'cloned', 'updated', 'skipped', 'meta_only', or 'error:<msg>'.
    Authentication relies on the local git environment (SSH keys / credential
    helper); a private repo without local creds fails to 'error' and we keep the
    metadata so the rest of the pull still succeeds.
    """
    git_url = repo.get("git_url") or repo.get("url")
    branch = repo.get("default_branch") or "main"
    if not git_url:
        return "meta_only"  # zip/folder-source repo: no clonable URL

    if (repo_dir / ".git").is_dir():
        if not force:
            return "skipped"
        rc, out = _git(["-C", str(repo_dir), "fetch", "--all", "--prune"])
        if rc != 0:
            return f"error:{out.strip()[:200]}"
        rc, out = _git(["-C", str(repo_dir), "reset", "--hard", f"origin/{branch}"])
        return "updated" if rc == 0 else f"error:{out.strip()[:200]}"

    clone_args = ["clone", "--branch", branch]
    if shallow:
        clone_args += ["--depth", "1"]
    clone_args += [git_url, str(repo_dir)]
    rc, out = _git(clone_args)
    return "cloned" if rc == 0 else f"error:{out.strip()[:200]}"


async def _download_repo_archive(cfg: dict, repo: dict, repo_dir: Path, force: bool) -> str:
    """Download a zip/folder-source repo's code via the platform archive endpoint
    and extract it into repo_dir. Returns 'downloaded', 'skipped', 'meta_only'
    (not indexed yet → 409), or 'error:<msg>'.

    The MCP token is accepted by GET /repositories/<id>/archive. We clear any prior
    extraction (keeping .rkm-meta.json) so the checkout mirrors the latest snapshot.
    """
    import io as _io
    import shutil
    import zipfile
    marker = repo_dir / ".rkm-archive"
    etag = str(repo.get("updated_at", repo.get("created_at", "")))
    if not force and marker.exists() and marker.read_text() == etag:
        return "skipped"
    try:
        async with _client(cfg) as c:
            r = await c.get(f"/api/v1/repositories/{repo['id']}/archive", timeout=600)
        if r.status_code == 409:
            return "meta_only"  # repo not indexed yet — trigger a sync first
        if r.status_code != 200:
            return f"error:HTTP {r.status_code} {r.text[:160]}"
        for child in repo_dir.iterdir():
            if child.name == ".rkm-meta.json":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        with zipfile.ZipFile(_io.BytesIO(r.content)) as zf:
            zf.extractall(repo_dir)
        marker.write_text(etag)
        return "downloaded"
    except Exception as e:  # noqa: BLE001
        return f"error:{str(e)[:160]}"


async def _pull_repos(cfg: dict, args: dict) -> dict:
    force = args.get("force", False)
    shallow = args.get("shallow", False)
    names = set(args.get("names") or [])
    project_id = cfg.get("project_id")
    if not project_id:
        return {"error": "No project_id in config. Run /rai:login first."}

    etag_cache = _etag_cache()
    dest = _project_dir(cfg) / "repos"
    dest.mkdir(parents=True, exist_ok=True)

    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/projects/{project_id}/repositories")
        resp.raise_for_status()
        repos = resp.json()

    if names:
        repos = [r for r in repos if r["name"] in names]

    # Approach C resolution inputs: explicit name→path mapping and the bounded
    # search roots scanned for an existing local checkout. Both live in the
    # project-level .rkm/config.json (never the global token config).
    project_cfg = _load_project_config()
    repo_paths = project_cfg.get("repo_paths") or {}
    search_roots = project_cfg.get("repo_search_roots") or []

    cloned, updated, downloaded, skipped, meta_only, linked, errors = [], [], [], [], [], [], []
    for repo in repos:
        name = repo["name"]
        # The vault dir always holds the .rkm-meta.json sidecar, even when the code
        # itself resolves to (and is indexed at) the user's own checkout elsewhere.
        meta_dir = dest / name
        meta_dir.mkdir(parents=True, exist_ok=True)
        key = f"repo:{repo['id']}"
        etag = str(repo.get("updated_at", repo.get("created_at", "")))
        has_git_url = bool(repo.get("git_url") or repo.get("url"))

        # Resolve which local checkout this platform repo maps to (link-first).
        if _repo_resolve is not None:
            outcome = _repo_resolve.resolve_repo(
                repo, repo_paths=repo_paths, search_roots=search_roots, vault_repo_dir=dest,
            )
            resolution_source, resolved_dir, resolve_reason = (
                outcome.source, outcome.path, outcome.reason,
            )
        else:  # module unavailable → behave like the legacy clone-into-vault path
            resolution_source, resolved_dir, resolve_reason = "clone", dest / name, ""

        resolved_path: str | None = None    # path skills should use (set on success)
        local_indexed_commit: str | None = None
        index_status = "skipped"

        if resolution_source in ("ambiguous", "none"):
            # Fail loud: never auto-pick or clone over an unresolved repo. The
            # reason tells the user how to disambiguate via repo_paths.
            errors.append({"name": name, "error": resolve_reason})

        elif resolution_source in ("configured", "linked"):
            # Use the user's own checkout in place — never clone or mutate it.
            # Build/refresh its local symbol index so skills get live impact data.
            repo_dir = Path(resolved_dir)
            resolved_path = str(repo_dir)
            linked.append(name)
            index_status = _initial_index(repo_dir)
            local_indexed_commit = _head_commit(repo_dir)

        else:  # "clone" — pull into the vault, then index the vault checkout
            repo_dir = dest / name
            resolved_path = str(repo_dir)
            # Skip the network/git work when nothing changed and the checkout exists.
            if not force and etag_cache.get(key) == etag and (repo_dir / ".git").is_dir():
                skipped.append(name)
            elif has_git_url:
                result = _clone_or_update_repo(repo, repo_dir, shallow, force)
                if result == "cloned":
                    cloned.append(name)
                elif result == "updated":
                    updated.append(name)
                elif result == "skipped":
                    skipped.append(name)
                elif result == "meta_only":
                    meta_only.append(name)
                elif result.startswith("error:"):
                    # T5: a private repo without local git creds fails to clone.
                    # Fall back to the server archive endpoint (the platform PAT stays
                    # server-side) so the skill still gets code. Surface the original
                    # clone error only if the archive fallback also fails.
                    fb = await _download_repo_archive(cfg, repo, repo_dir, force)
                    if fb == "downloaded":
                        downloaded.append(name)
                    elif fb == "skipped":
                        skipped.append(name)
                    elif fb == "meta_only":
                        meta_only.append(name)
                    else:
                        errors.append({"name": name, "error": result[len("error:"):]})
                etag_cache[key] = etag
            else:
                # zip/folder-source repo (no git_url): pull code via the archive endpoint.
                result = await _download_repo_archive(cfg, repo, repo_dir, force)
                if result == "downloaded":
                    downloaded.append(name)
                elif result == "skipped":
                    skipped.append(name)
                elif result == "meta_only":
                    meta_only.append(name)
                elif result.startswith("error:"):
                    errors.append({"name": name, "error": result[len("error:"):]})
                etag_cache[key] = etag
            # Index whatever code now exists in the vault checkout. Skip pure
            # meta-only repos (no .git and nothing but the metadata sidecars).
            indexable = (repo_dir / ".git").is_dir() or any(
                c.name not in (".rkm-meta.json", ".rkm-archive")
                for c in repo_dir.iterdir()
            )
            if indexable:
                index_status = _initial_index(repo_dir)
                local_indexed_commit = _head_commit(repo_dir)

        # Always persist the per-repo metadata alongside the vault dir so skills can
        # resolve their source path even for meta-only (zip/folder) or linked repos.
        (meta_dir / ".rkm-meta.json").write_text(json.dumps({
            "id": repo["id"],
            "name": name,
            "git_url": repo.get("git_url") or repo.get("url"),
            "default_branch": repo.get("default_branch") or "main",
            "source_type": repo.get("source_type"),
            "last_synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gitnexus_indexed_commit": repo.get("gitnexus_indexed_commit"),
            "resolved_path": resolved_path,
            "resolution_source": resolution_source,
            "local_indexed_commit": local_indexed_commit,
            "index_status": index_status,
        }, indent=2))

    _save_etag_cache(etag_cache)
    index = [{"id": r["id"], "name": r["name"], "git_url": r.get("git_url") or r.get("url", "")} for r in repos]
    (dest / "_index.json").write_text(json.dumps(index, indent=2))
    return {
        "cloned": cloned,
        "updated": updated,
        "downloaded": downloaded,
        "linked": linked,
        "skipped": skipped,
        "meta_only": meta_only,
        "errors": errors,
        "total": len(repos),
        "repos_dir": str(dest),
    }


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------

async def _list_workflows(cfg: dict) -> list:
    async with _client(cfg) as c:
        resp = await c.get("/api/v1/workflows/mine")
        resp.raise_for_status()
    return resp.json()


async def _list_skills(cfg: dict, args: dict) -> dict:
    """List available skills without downloading bundles.

    Returns {"org": [...], "project": [...]} where each entry is a compact
    {slug, name, tags, runtime, project_id, updated_at} dict.
    Supports optional 'tag' filter and 'scope' (org|project|both, default both).
    """
    async with _client(cfg) as c:
        resp = await c.get("/api/v1/skills/", params={"limit": 100})
        resp.raise_for_status()
    rows = _extract_items(resp.json())

    # Optional tag filter (mirrors _pull_skills)
    tag = args.get("tag")
    if tag:
        rows = [s for s in rows if tag in (s.get("tags") or [])]

    # Partition org vs project (same rule as _pull_skills)
    org_skills = [s for s in rows if s.get("project_id") is None]
    project_skills = [s for s in rows if s.get("project_id") is not None]

    # Compact projection — only metadata needed for the picker
    _compact = lambda s: {
        "slug": s.get("slug"),
        "name": s.get("name"),
        "tags": s.get("tags") or [],
        "runtime": s.get("runtime", "prompt"),
        "project_id": s.get("project_id"),
        "updated_at": s.get("updated_at"),
    }

    scope = args.get("scope", "both")
    return {
        "org": [_compact(s) for s in org_skills] if scope in ("org", "both") else [],
        "project": [_compact(s) for s in project_skills] if scope in ("project", "both") else [],
    }


async def _run_workflow(cfg: dict, args: dict) -> dict:
    async with _client(cfg) as c:
        resp = await c.post(f"/api/v1/workflows/{args['workflow_id']}/run", json=args.get("inputs", {}))
        resp.raise_for_status()
    return resp.json()


async def _run_status(cfg: dict, args: dict) -> dict:
    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/skill-runs/{args['run_id']}")
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Pass-through knowledge tools
# ---------------------------------------------------------------------------

async def _search_knowledge(cfg: dict, args: dict) -> list:
    async with _client(cfg) as c:
        resp = await c.get("/api/v1/search/", params={"q": args["query"], "limit": args.get("limit", 10)})
        resp.raise_for_status()
    return resp.json()


async def _get_wiki_page(cfg: dict, args: dict) -> dict:
    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/wiki/{args['slug']}")
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Device login
# ---------------------------------------------------------------------------

async def _login(args: dict) -> dict:
    """Device authorization flow: opens browser, polls until user approves."""
    import webbrowser

    platform_url = (args.get("platform_url") or DEFAULT_PLATFORM_URL).rstrip("/")

    # 1. Start device flow
    async with httpx.AsyncClient(base_url=platform_url, timeout=15) as c:
        resp = await c.post("/api/v1/auth/device/start")
        resp.raise_for_status()
        device = resp.json()

    device_code = device["device_code"]
    user_code = device["user_code"]
    verification_url = device["verification_url"]
    expires_in = device.get("expires_in", 600)

    print(f"\n[rkm] Opening browser to authorize this device.", file=sys.stderr)
    print(f"[rkm] If the browser doesn't open, visit: {verification_url}", file=sys.stderr)
    print(f"[rkm] Enter code: {user_code}\n", file=sys.stderr)
    webbrowser.open(verification_url)

    # 2. Poll until authorized or expired
    deadline = time.time() + expires_in
    async with httpx.AsyncClient(base_url=platform_url, timeout=10) as c:
        while time.time() < deadline:
            await asyncio.sleep(5)
            poll = await c.get("/api/v1/auth/device/poll", params={"device_code": device_code})
            data = poll.json()
            status = data.get("status", "pending")
            if status == "authorized":
                token = data["token"]
                break
            if status == "expired":
                return {"error": "Device code expired. Run rkm_login again."}
        else:
            return {"error": "Login timed out."}

    # 3. Save config
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    existing["platform_url"] = platform_url
    existing["token"] = token
    CONFIG_PATH.write_text(json.dumps(existing, indent=2))

    return {"status": "logged_in", "platform_url": platform_url, "message": "Credentials saved to ~/.rkm/config.json"}


async def _logout(args: dict) -> dict:
    """Local sign-out: clear credentials from ~/.rkm/config.json and optionally
    archive or delete the workspace .rkm/ vault (KB, skills, workflows, etag cache).

    The MCP bearer token cannot revoke itself server-side (DELETE /api/v1/mcp/tokens/{id}
    requires web-session auth) — the user is pointed to Settings → MCP Tokens instead.
    """
    import shutil
    from datetime import datetime, timezone

    purge = args.get("purge", "keep")
    if purge not in ("keep", "archive", "delete"):
        return {"error": f"Invalid purge mode '{purge}' — use keep | archive | delete."}
    if purge == "delete" and not args.get("confirm"):
        return {"error": "purge='delete' is irreversible and requires confirm=true. "
                         "Un-pushed local edits and skill scripts/.env files will be lost."}

    result: dict = {"purge": purge}

    # 1. Identify who is being signed out (best-effort; never blocks logout).
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            cfg = {}
    was_logged_in = bool(cfg.get("token"))
    if was_logged_in:
        try:
            async with _client(cfg) as c:
                resp = await c.get("/api/v1/mcp/whoami")
                if resp.status_code == 200:
                    result["was_user"] = resp.json().get("user_email") or resp.json().get("email")
        except Exception:
            pass  # platform down / token already invalid — proceed with local sign-out

    # 2. Clear credentials + project binding; keep platform_url for the next login.
    if cfg:
        for key in ("token", "project_id", "project_slug", "user_email"):
            cfg.pop(key, None)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        try:
            CONFIG_PATH.chmod(0o600)
        except Exception:
            pass
    result["status"] = "logged_out" if was_logged_in else "not_logged_in"
    result["config"] = str(CONFIG_PATH)

    # 3. Vault handling.
    if purge == "archive":
        if LOCAL_RKM.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest = LOCAL_RKM.with_name(f".rkm-archive-{ts}")
            LOCAL_RKM.rename(dest)
            result["archived_to"] = str(dest)
        else:
            result["note_vault"] = "No local .rkm/ vault found — nothing to archive."
    elif purge == "delete":
        if LOCAL_RKM.exists():
            removed = []
            for entry in ("org", "projects", "kb", "runs", "INDEX.md",
                          ".etag-cache.json", ".obsidian", "config.json"):
                target = LOCAL_RKM / entry
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                    removed.append(entry)
            result["removed"] = removed
        else:
            result["note_vault"] = "No local .rkm/ vault found — nothing to delete."

    result["note"] = ("Server-side token NOT revoked — revoke it in the web UI under "
                      "Settings → MCP Tokens if this device should lose access. "
                      "Run rkm_login (or /rai:login) to log in again with any account.")
    return result


# ---------------------------------------------------------------------------
# Streaming run + artifact download
# ---------------------------------------------------------------------------

async def _run_skill_streaming(cfg: dict, args: dict) -> dict:
    """Subscribe to SSE stream for a run; collect all events; return summary."""
    import httpx
    run_id = args["run_id"]
    base_url = cfg.get("platform_url", "").rstrip("/")
    token = cfg.get("token", "")
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    stdout_lines: list[str] = []
    artifacts: list[dict] = []
    final_status = "unknown"

    async with httpx.AsyncClient(base_url=base_url, timeout=None) as c:
        async with c.stream("GET", f"/api/v1/skill-runs/{run_id}/stream", headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                etype = event.get("type", "")
                if etype == "stdout":
                    stdout_lines.append(event.get("data", ""))
                elif etype == "artifact":
                    artifacts.append({"filename": event.get("filename"), "object_key": event.get("object_key")})
                elif etype == "complete":
                    final_status = event.get("status", "complete")
                    break

    return {
        "run_id": run_id,
        "status": final_status,
        "stdout": "".join(stdout_lines),
        "artifacts": artifacts,
    }


async def _push_run(cfg: dict, args: dict) -> dict:
    """Record a completed LOCAL run on the platform (the /rai:run-skill --push action).

    Resolves the skill slug → platform id, then POSTs to /api/v1/skill-runs/record.
    Records history/audit only; it does NOT re-execute the skill.
    """
    slug = args["slug"]
    async with _client(cfg) as c:
        # Resolve slug → platform id. `search` filters by slug server-side (ilike),
        # so this narrows the result set instead of paging the whole skill list —
        # a slug past a fixed page size is no longer silently missed.
        resp = await c.get("/api/v1/skills/", params={"search": slug, "limit": 100})
        resp.raise_for_status()
        skills = resp.json().get("items", [])
        match = next((s for s in skills if s.get("slug") == slug), None)
        if match is None:
            return {"error": f"Skill '{slug}' not found on the platform; cannot push run history."}

        payload = {
            "skill_id": match["id"],
            "status": args.get("status", "succeeded"),
            "input_md": args.get("input_md", ""),
            "output_md": args.get("output_md"),
            "error": args.get("error"),
            "project_id": cfg.get("project_id"),
        }
        resp = await c.post("/api/v1/skill-runs/record", json=payload)
        if resp.status_code not in (200, 201):
            return {"error": f"push failed: HTTP {resp.status_code} {resp.text[:200]}"}
        run = resp.json()
    return {"pushed": True, "run_id": run.get("id"), "status": run.get("status"), "skill": slug}


async def _download_artifact(cfg: dict, args: dict) -> dict:
    """Return a presigned download URL for a named artifact on a completed run."""
    run_id = args["run_id"]
    filename = args["filename"]
    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/skill-runs/{run_id}/artifacts/{filename}/url")
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# KB ingest helpers (imported from kb_ingest.py in the same directory)
# ---------------------------------------------------------------------------

from kb_ingest import (  # noqa: E402
    clean_text as _kb_clean_text,
    extract_url as _kb_extract_url,
    file_to_text as _kb_file_to_text,
    ingest_folder as _kb_ingest_folder,
    ingest_zip as _kb_ingest_zip,
    local_source_id as _kb_local_source_id,
    _MAX_RAW_TEXT_CHARS as _KB_MAX_CHARS,
)


def _parse_wiki_frontmatter(file_content: str) -> dict | None:
    """Parse the YAML-like frontmatter + body from a wiki .md file written by _pull_kb."""
    if not file_content.startswith("---\n"):
        return None
    rest = file_content[4:]
    end = rest.find("\n---\n")
    if end == -1:
        return None
    fm_block = rest[:end]
    body = rest[end + 5:]  # skip the closing "\n---\n"

    result: dict = {"content_md": body.strip()}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        list_m = re.match(r"^\[([^\]]*)\]$", val)
        if list_m:
            raw = list_m.group(1).strip()
            if raw:
                items = [i.strip() for i in raw.split(",") if i.strip()]
                try:
                    val = [int(i) for i in items]
                except ValueError:
                    val = items
            else:
                val = []
        elif val in ("None", "null"):
            val = None
        elif val.lstrip("-").isdigit():
            val = int(val)
        result[key] = val
    return result


async def _ingest(cfg: dict, args: dict) -> dict:
    """Extract a local/remote source and write it to .rkm/kb/sources/<id>.json."""
    title = args.get("title") or "Untitled"
    project_id = args.get("project_id") or cfg.get("project_id")
    knowledge_type_id = args.get("knowledge_type_id")

    # Determine source type and extract raw text
    if "text" in args:
        source_type = "text"
        raw_text = args["text"]
        extra: dict = {}
    elif "url" in args:
        source_type = "url"
        raw_text = await _kb_extract_url(args["url"])
        extra = {"url": args["url"]}
    elif "file" in args:
        source_type = "file"
        p = Path(args["file"]).expanduser().resolve()
        if not p.exists():
            return {"error": f"File not found: {p}"}
        raw_bytes = p.read_bytes()
        extracted = _kb_file_to_text(str(p), raw_bytes)
        if extracted is None:
            return {"error": f"Unsupported or empty file: {p.suffix}"}
        raw_text = extracted
        extra = {"file_path": str(p)}
    elif "folder" in args:
        source_type = "folder"
        p = Path(args["folder"]).expanduser().resolve()
        if not p.exists():
            return {"error": f"Path not found: {p}"}
        if p.is_dir():
            raw_text = _kb_ingest_folder(p)
        elif p.suffix.lower() == ".zip":
            raw_text = _kb_ingest_zip(p)
        else:
            return {"error": f"--folder must be a directory or .zip file: {p}"}
        extra = {"file_path": str(p)}
    else:
        return {"error": "Provide one of: text, url, file, or folder"}

    raw_text = _kb_clean_text(raw_text)
    if len(raw_text) > _KB_MAX_CHARS:
        raw_text = raw_text[:_KB_MAX_CHARS]

    source_id = _kb_local_source_id(raw_text or title)

    sources_dest = _kb_dir() / "sources"
    sources_dest.mkdir(parents=True, exist_ok=True)

    record: dict = {
        "id": source_id,
        "title": title,
        "source_type": source_type,
        "status": "completed",
        "project_id": project_id,
        "knowledge_type_id": knowledge_type_id,
        "raw_text": raw_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    (sources_dest / f"{source_id}.json").write_text(json.dumps(record, indent=2))

    return {
        "source_id": source_id,
        "title": title,
        "source_type": source_type,
        "char_count": len(raw_text),
        "raw_text": raw_text,
    }


async def _push_kb(cfg: dict, args: dict) -> dict:
    """Push local KB (sources/wiki) to the platform.

    scope='org'     → reads .rkm/org/kb/,                   visibility='org'
    scope='project' → reads .rkm/projects/<slug>/kb/,        visibility='project'
    scope='both'    → reads both scoped dirs (default)
    scope=None      → legacy: reads _kb_dir() (kb_dir override or .rkm/kb/)
    """
    wiki_only = args.get("wiki_only", False)
    sources_only = args.get("sources_only", False)
    force = args.get("force", False)
    scope = args.get("scope")  # "org" | "project" | "both" | None (legacy)
    project_id = cfg.get("project_id")
    etag_cache = _etag_cache()
    pushed_sources: list = []
    pushed_wiki: list = []
    skipped: list = []
    errors: list = []

    # Resolve (kb_base_dir, visibility) pairs
    if scope == "org":
        kb_roots = [(_kb_dir(_org_dir()), "org")]
    elif scope == "project":
        kb_roots = [(_kb_dir(_project_dir(cfg)), "project")]
    elif scope == "both":
        kb_roots = [
            (_kb_dir(_org_dir()), "org"),
            (_kb_dir(_project_dir(cfg)), "project"),
        ]
    else:
        # Legacy path: kb_dir override or .rkm/kb/
        kb_roots = [(_kb_dir(), "project")]

    for kb_base, visibility in kb_roots:
        if not wiki_only:
            sources_dir = kb_base / "sources"
            if sources_dir.exists():
                for src_file in sorted(sources_dir.glob("*.json")):
                    record = json.loads(src_file.read_text())
                    src_id = record["id"]
                    raw_text = record.get("raw_text", "")
                    etag_key = f"push-source:{src_id}"
                    etag = hashlib.sha1(raw_text.encode()).hexdigest()[:16]

                    if not force and etag_cache.get(etag_key) == etag:
                        skipped.append(src_id)
                        continue

                    src_project_id = record.get("project_id") or project_id
                    try:
                        async with _client(cfg) as c:
                            if record.get("source_type") == "url" and record.get("url"):
                                resp = await c.post("/api/v1/sources/url", json={
                                    "title": record["title"],
                                    "url": record["url"],
                                    "project_id": src_project_id,
                                    "visibility": visibility,
                                })
                            else:
                                resp = await c.post("/api/v1/sources/text", json={
                                    "title": record["title"],
                                    "text": raw_text,
                                    "project_id": src_project_id,
                                    "visibility": visibility,
                                })
                            resp.raise_for_status()
                        platform_record = resp.json()
                        record["platform_id"] = platform_record["id"]
                        src_file.write_text(json.dumps(record, indent=2))
                        etag_cache[etag_key] = etag
                        pushed_sources.append(src_id)
                    except Exception as exc:
                        errors.append({"source_id": src_id, "error": str(exc)})

        if not sources_only:
            wiki_dir = kb_base / "wiki"
            if wiki_dir.exists():
                for wiki_file in sorted(wiki_dir.glob("*.md")):
                    if wiki_file.name == "INDEX.md":
                        continue
                    content = wiki_file.read_text()
                    page = _parse_wiki_frontmatter(content)
                    if not page:
                        errors.append({"wiki_file": wiki_file.name, "error": "Could not parse frontmatter"})
                        continue

                    slug = page.get("slug", "")
                    if not slug:
                        errors.append({"wiki_file": wiki_file.name, "error": "Missing slug in frontmatter"})
                        continue

                    etag_key = f"push-wiki:{slug}"
                    etag = hashlib.sha1(content.encode()).hexdigest()[:16]

                    if not force and etag_cache.get(etag_key) == etag:
                        skipped.append(slug)
                        continue

                    page_project_id = page.get("project_id") or project_id
                    try:
                        async with _client(cfg) as c:
                            check = await c.get(f"/api/v1/wiki/slug/{slug}")
                            if check.status_code == 404:
                                resp = await c.post("/api/v1/wiki/", json={
                                    "title": page.get("title", slug),
                                    "content_md": page["content_md"],
                                    "project_id": page_project_id,
                                    "knowledge_type_id": page.get("knowledge_type_id"),
                                    "visibility": visibility,
                                })
                            else:
                                check.raise_for_status()
                                existing = check.json()
                                resp = await c.patch(f"/api/v1/wiki/{existing['id']}", json={
                                    "title": page.get("title", slug),
                                    "content_md": page["content_md"],
                                    "summary": page.get("summary"),
                                })
                            resp.raise_for_status()
                        etag_cache[etag_key] = etag
                        pushed_wiki.append(slug)
                    except Exception as exc:
                        errors.append({"slug": slug, "error": str(exc)})

    _save_etag_cache(etag_cache)
    return {
        "pushed_sources": pushed_sources,
        "pushed_wiki": pushed_wiki,
        "skipped_etag_match": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Push workflows
# ---------------------------------------------------------------------------

async def _push_workflows(cfg: dict, args: dict) -> dict:
    """Push local workflow JSON files back to the platform.

    Reads .rkm/projects/<slug>/workflows/*.json, remaps skill slugs to current
    environment skill IDs, then creates or updates workflows via the platform API.
    Workflows are always project-scoped; org scope is not applicable.
    """
    force = args.get("force", False)
    project_id = cfg.get("project_id")
    if not project_id:
        return {"error": "No project_id in config. Run /rai:login first."}

    src_dir = _project_dir(cfg) / "workflows"
    if not src_dir.exists():
        return {
            "pushed": [], "skipped_etag_match": [], "errors": [],
            "note": "No local workflows found. Run /rai:pull (Project × Workflows) first.",
        }

    etag_cache = _etag_cache()
    errors: list = []
    pushed: list = []
    skipped: list = []

    # Build slug→id map from the platform's current skills
    try:
        async with _client(cfg) as c:
            skills_resp = await c.get("/api/v1/skills/", params={"limit": 500})
            skills_resp.raise_for_status()
            slug_to_id: dict[str, int] = {
                s["slug"]: s["id"]
                for s in _extract_items(skills_resp.json())
            }
    except Exception as exc:
        return {"error": f"Failed to fetch skills for slug→id remapping: {exc}"}

    # Fetch existing workflows once (for upsert matching by name)
    try:
        async with _client(cfg) as c:
            mine_resp = await c.get("/api/v1/workflows/mine")
            mine_resp.raise_for_status()
            existing_by_name: dict[str, dict] = {w["name"]: w for w in mine_resp.json()}
    except Exception as exc:
        return {"error": f"Failed to fetch existing workflows: {exc}"}

    # Collect workflow files; filter to requested slugs if not pushing all
    wf_files = sorted(f for f in src_dir.glob("*.json") if f.name != "_index.json")
    if not args.get("all") and args.get("slugs"):
        slug_set = set(args["slugs"])
        wf_files = [f for f in wf_files if f.stem in slug_set]

    for wf_file in wf_files:
        wf_slug = wf_file.stem
        content = wf_file.read_text()
        etag_key = f"push-workflow:{wf_slug}"
        etag = hashlib.sha1(content.encode()).hexdigest()[:16]

        if not force and etag_cache.get(etag_key) == etag:
            skipped.append(wf_slug)
            continue

        try:
            wf = json.loads(content)
        except Exception as exc:
            errors.append({"slug": wf_slug, "error": f"JSON parse error: {exc}"})
            continue

        steps = wf.get("steps", [])
        if not steps:
            errors.append({"slug": wf_slug, "error": "Workflow has no steps; platform requires at least one step"})
            continue

        # Remap step skill_ids using current platform slug→id mapping
        remapped_steps = []
        remap_errors = []
        for step in steps:
            skill_slug = step.get("skill_slug")
            if not skill_slug:
                remap_errors.append(f"step {step.get('step_index', '?')} has no skill_slug")
                continue
            skill_id = slug_to_id.get(skill_slug)
            if skill_id is None:
                remap_errors.append(f"skill '{skill_slug}' not found on platform")
                continue
            remapped_steps.append({"step_index": step["step_index"], "skill_id": skill_id})

        if remap_errors:
            errors.append({"slug": wf_slug, "error": "; ".join(remap_errors)})
            continue

        payload: dict[str, Any] = {
            "name": wf.get("name", wf_slug),
            "description": wf.get("description"),
            "tags": wf.get("tags", []),
            "steps": remapped_steps,
        }

        try:
            async with _client(cfg) as c:
                existing = existing_by_name.get(wf.get("name", wf_slug))
                if existing:
                    resp = await c.patch(f"/api/v1/workflows/{existing['id']}", json=payload)
                else:
                    resp = await c.post("/api/v1/workflows/", json={**payload, "project_id": project_id})
                resp.raise_for_status()
            etag_cache[etag_key] = etag
            pushed.append(wf_slug)
        except Exception as exc:
            errors.append({"slug": wf_slug, "error": str(exc)})

    _save_etag_cache(etag_cache)
    return {"pushed": pushed, "skipped_etag_match": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Set KB folder
# ---------------------------------------------------------------------------

def _set_kb(arguments: dict) -> dict:
    """Set (or clear) the project-level KB folder override in .rkm/config.json."""
    folder = arguments.get("folder")
    clear = arguments.get("clear", False)
    cfg = _load_project_config()

    if clear:
        cfg.pop("kb_dir", None)
        _save_project_config(cfg)
        kb = LOCAL_RKM / "kb"
    else:
        if not folder:
            return {"error": "folder is required (or pass clear=true to reset to default)"}
        p = Path(folder).expanduser()
        kb = p if p.is_absolute() else (Path.cwd() / p)
        cfg["kb_dir"] = folder  # store as-given so relative paths stay portable
        _save_project_config(cfg)

    (kb / "wiki").mkdir(parents=True, exist_ok=True)
    (kb / "sources").mkdir(parents=True, exist_ok=True)
    return {
        "kb_dir": str(kb),
        "wiki_dir": str(kb / "wiki"),
        "sources_dir": str(kb / "sources"),
        "config_path": str(PROJECT_CONFIG_PATH),
        "cleared": clear,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
