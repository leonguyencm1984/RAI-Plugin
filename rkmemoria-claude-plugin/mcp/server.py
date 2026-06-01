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
import sys
import time
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


def _client(cfg: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg["platform_url"],
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
                    "platform_url": {"type": "string", "description": "Platform base URL, e.g. http://localhost:8001"},
                },
                "required": ["platform_url"],
            },
        ),
        types.Tool(
            name="rkm_status",
            description="Show what's pulled locally vs available on the platform",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="rkm_pull_workflows",
            description="Pull workflows from the platform to .rkm/workflows/",
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
            description="Pull skill bundles from the platform to .rkm/skills/",
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "slugs": {"type": "array", "items": {"type": "string"}},
                    "tag": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="rkm_pull_kb",
            description="Pull knowledge base (sources + wiki pages) to .rkm/kb/",
            inputSchema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False},
                    "wiki_only": {"type": "boolean", "default": False},
                    "sources_only": {"type": "boolean", "default": False},
                    "force": {"type": "boolean", "default": False},
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
                },
                "required": ["slug"],
            },
        ),
        types.Tool(
            name="rkm_pull_repos",
            description="Pull repository metadata for the current project to .rkm/repos/",
            inputSchema={
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "default": False},
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
    ]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # Login does not require existing config
    if name == "rkm_login":
        try:
            result = await _login(arguments)
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
        elif name == "rkm_list_workflows":
            result = await _list_workflows(cfg)
        elif name == "rkm_run_workflow":
            result = await _run_workflow(cfg, arguments)
        elif name == "rkm_run_status":
            result = await _run_status(cfg, arguments)
        elif name == "rkm_run_skill_streaming":
            result = await _run_skill_streaming(cfg, arguments)
        elif name == "rkm_download_artifact":
            result = await _download_artifact(cfg, arguments)
        elif name == "search_knowledge":
            result = await _search_knowledge(cfg, arguments)
        elif name == "get_wiki_page":
            result = await _get_wiki_page(cfg, arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        result = {"error": str(exc)}
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

async def _rkm_status(cfg: dict) -> dict:
    async with _client(cfg) as c:
        project_id = cfg.get("project_id")
        manifest_resp = await c.get(f"/api/v1/projects/{project_id}/manifest")
        manifest_resp.raise_for_status()
        manifest = manifest_resp.json()

    etag_cache = _etag_cache()
    pulled_workflows = len(list((LOCAL_RKM / "workflows").glob("*.json"))) if (LOCAL_RKM / "workflows").exists() else 0
    pulled_skills = len(list((LOCAL_RKM / "skills").iterdir())) if (LOCAL_RKM / "skills").exists() else 0
    pulled_wiki = len(list((LOCAL_RKM / "kb" / "wiki").glob("*.md"))) if (LOCAL_RKM / "kb" / "wiki").exists() else 0
    pulled_sources = len(list((LOCAL_RKM / "kb" / "sources").glob("*.json"))) if (LOCAL_RKM / "kb" / "sources").exists() else 0

    return {
        "platform": cfg["platform_url"],
        "project_id": project_id,
        "workflows": {"pulled": pulled_workflows, "available": manifest["workflows_count"]},
        "skills": {"pulled": pulled_skills, "available": manifest["skills_count"]},
        "repos": {"available": manifest["repos_count"]},
        "sources": {"pulled": pulled_sources, "available": manifest["sources_count"]},
        "wiki": {"pulled": pulled_wiki, "available": manifest["wiki_count"]},
        "etag_entries": len(etag_cache),
    }


# ---------------------------------------------------------------------------
# Pull workflows
# ---------------------------------------------------------------------------

async def _pull_workflows(cfg: dict, args: dict) -> dict:
    force = args.get("force", False)
    etag_cache = _etag_cache()
    dest = LOCAL_RKM / "workflows"
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
    force = args.get("force", False)
    etag_cache = _etag_cache()
    dest = LOCAL_RKM / "skills"
    dest.mkdir(parents=True, exist_ok=True)

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

    pulled, skipped, conflicts = [], [], []
    for skill in skills:
        slug = skill["slug"]
        key = f"skill:{slug}"
        etag = str(skill.get("updated_at", ""))
        skill_dir = dest / slug

        if not force and etag_cache.get(key) == etag:
            skipped.append(slug)
            continue

        # Check for local edits by comparing stored hash
        if skill_dir.exists() and not force:
            marker = skill_dir / ".rkm-etag"
            if marker.exists() and marker.read_text() != etag:
                conflicts.append(slug)
                continue

        async with _client(cfg) as c:
            bundle_resp = await c.get(f"/api/v1/skills/{slug}/bundle")
            if bundle_resp.status_code == 200:
                import zipfile, io
                skill_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(bundle_resp.content)) as zf:
                    zf.extractall(skill_dir)
                (skill_dir / ".rkm-etag").write_text(etag)
                etag_cache[key] = etag
                pulled.append(slug)
            else:
                conflicts.append(slug)

    _save_etag_cache(etag_cache)
    index = [{"slug": s["slug"], "name": s["name"], "runtime": s.get("runtime", "prompt")} for s in skills]
    (dest / "_index.json").write_text(json.dumps(index, indent=2))
    return {"pulled": pulled, "skipped_etag_match": skipped, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Pull KB
# ---------------------------------------------------------------------------

async def _pull_kb(cfg: dict, args: dict) -> dict:
    force = args.get("force", False)
    wiki_only = args.get("wiki_only", False)
    sources_only = args.get("sources_only", False)
    project_id = cfg.get("project_id")
    etag_cache = _etag_cache()
    pulled_wiki, pulled_sources, skipped = [], [], []

    if not sources_only:
        wiki_dest = LOCAL_RKM / "kb" / "wiki"
        wiki_dest.mkdir(parents=True, exist_ok=True)
        async with _client(cfg) as c:
            resp = await c.get("/api/v1/wiki/", params={"project_id": project_id, "limit": 200})
            resp.raise_for_status()
            pages = resp.json()
        for page in pages:
            key = f"wiki:{page['slug']}"
            etag = str(page.get("updated_at", ""))
            if not force and etag_cache.get(key) == etag:
                skipped.append(page["slug"])
                continue
            (wiki_dest / f"{page['slug']}.md").write_text(
                f"# {page['title']}\n\n{page.get('content_md', '')}"
            )
            etag_cache[key] = etag
            pulled_wiki.append(page["slug"])

    if not wiki_only:
        sources_dest = LOCAL_RKM / "kb" / "sources"
        sources_dest.mkdir(parents=True, exist_ok=True)
        async with _client(cfg) as c:
            resp = await c.get("/api/v1/sources/", params={"project_id": project_id, "limit": 200})
            resp.raise_for_status()
            sources = resp.json()
        for src in sources:
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

def _load_ignore_patterns(skill_dir: Path) -> list[str]:
    """Read .rkmpackignore from the skill directory and return glob patterns."""
    ignore_file = skill_dir / ".rkmpackignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def _should_ignore(rel_path: str, patterns: list[str]) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(Path(rel_path).name, p) for p in patterns)


def _pack_skill(skill_dir: Path) -> bytes:
    """Build a zip from the skill directory, honoring .rkmpackignore."""
    import io, zipfile
    patterns = _load_ignore_patterns(skill_dir)
    always_skip = {".rkm-etag", ".rkmpackignore"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(skill_dir))
            if f.name in always_skip:
                continue
            if _should_ignore(rel, patterns):
                continue
            zf.write(f, rel)
    return buf.getvalue()


def _sign_bundle(zip_bytes: bytes, slug: str) -> bytes:
    """Sign zip_bytes with ed25519 key from ~/.rkm/keys/{slug}.ed25519 and embed signature."""
    import io, zipfile
    key_path = Path.home() / ".rkm" / "keys" / f"{slug}.ed25519"
    if not key_path.exists():
        return zip_bytes  # No key — return unsigned

    try:
        from nacl.signing import SigningKey
        private_key = SigningKey(bytes.fromhex(key_path.read_text().strip()))
        sig = private_key.sign(zip_bytes).signature

        # Add signature into the zip
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr("signature.ed25519", sig)
        return buf.getvalue()
    except Exception as exc:
        print(f"[rkm] Warning: signing failed ({exc}), pushing unsigned", file=sys.stderr)
        return zip_bytes


async def _push_skill(cfg: dict, args: dict) -> dict:
    slug = args["slug"]
    on_conflict = args.get("on_conflict", "replace")
    skill_dir = LOCAL_RKM / "skills" / slug
    if not skill_dir.exists():
        return {"error": f"Skill '{slug}' not found in .rkm/skills/"}

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

async def _pull_repos(cfg: dict, args: dict) -> dict:
    force = args.get("force", False)
    project_id = cfg.get("project_id")
    if not project_id:
        return {"error": "No project_id in config. Run /rai:login first."}

    etag_cache = _etag_cache()
    dest = LOCAL_RKM / "repos"
    dest.mkdir(parents=True, exist_ok=True)

    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/projects/{project_id}/repositories")
        resp.raise_for_status()
        repos = resp.json()

    pulled, skipped = [], []
    for repo in repos:
        key = f"repo:{repo['id']}"
        etag = str(repo.get("updated_at", repo.get("created_at", "")))
        if not force and etag_cache.get(key) == etag:
            skipped.append(repo["name"])
            continue
        (dest / f"{repo['name']}.json").write_text(json.dumps(repo, indent=2))
        etag_cache[key] = etag
        pulled.append(repo["name"])

    _save_etag_cache(etag_cache)
    index = [{"id": r["id"], "name": r["name"], "url": r.get("url", "")} for r in repos]
    (dest / "_index.json").write_text(json.dumps(index, indent=2))
    return {"pulled": pulled, "skipped_etag_match": skipped, "total": len(repos)}


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------

async def _list_workflows(cfg: dict) -> list:
    async with _client(cfg) as c:
        resp = await c.get("/api/v1/workflows/mine")
        resp.raise_for_status()
    return resp.json()


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

    platform_url = args["platform_url"].rstrip("/")

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


# ---------------------------------------------------------------------------
# Streaming run + artifact download
# ---------------------------------------------------------------------------

async def _run_skill_streaming(cfg: dict, args: dict) -> dict:
    """Subscribe to SSE stream for a run; collect all events; return summary."""
    import httpx
    run_id = args["run_id"]
    base_url = cfg.get("platform_url", "").rstrip("/")
    token = cfg.get("mcp_token", "")
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


async def _download_artifact(cfg: dict, args: dict) -> dict:
    """Return a presigned download URL for a named artifact on a completed run."""
    run_id = args["run_id"]
    filename = args["filename"]
    async with _client(cfg) as c:
        resp = await c.get(f"/api/v1/skill-runs/{run_id}/artifacts/{filename}/url")
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
