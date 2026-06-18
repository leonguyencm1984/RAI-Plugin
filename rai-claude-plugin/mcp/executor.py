"""
Local skill executor for script and hybrid runtimes.
Invoked by /rai:run-skill.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import gitnexus_index as _gnx
except ImportError:  # pragma: no cover - import-layout fallback; reindex is best-effort
    _gnx = None

LOCAL_RKM = Path.cwd() / ".rkm"
VENVS_DIR = Path.home() / ".rkm" / "venvs"
RUNS_DIR = LOCAL_RKM / "runs"
SKILL_MD = "SKILL.md"  # noqa: S1192


def _skill_dir(slug: str) -> Path:
    # Pull writes bundles to scope-partitioned dirs (.rkm/org/skills/<slug> and
    # .rkm/projects/<project>/skills/<slug>); the legacy flat .rkm/skills/<slug>
    # is kept first for back-compat. Search all three so /rai:run-skill can find
    # a freshly pulled script/hybrid skill.
    # Project-first: a project skill overrides an org skill of the same slug.
    # Order: .rkm/projects/*/skills/<slug> → .rkm/org/skills/<slug> → legacy flat.
    candidates = []
    projects_root = LOCAL_RKM / "projects"
    if projects_root.is_dir():
        candidates += [p / "skills" / slug for p in sorted(projects_root.iterdir()) if p.is_dir()]
    candidates += [LOCAL_RKM / "org" / "skills" / slug, LOCAL_RKM / "skills" / slug]
    for d in candidates:
        if d.exists():
            return d
    raise FileNotFoundError(
        f"Skill '{slug}' not found in .rkm/skills/, .rkm/org/skills/, or "
        f".rkm/projects/*/skills/. Pull it first with rkm_pull_skills."
    )


def _load_manifest(skill_dir: Path) -> dict:
    manifest_path = skill_dir / "skill.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"skill.yaml not found in {skill_dir}")
    return yaml.safe_load(manifest_path.read_text())


def _project_slug_from_dir(skill_dir: Path) -> str | None:
    """Derive the project slug from a skill bundle path.

    Project skills live at .rkm/projects/<slug>/skills/<slug>; org skills at
    .rkm/org/skills/<slug> (returns None). Deriving from the path is more robust
    than the manifest 'project' field, which platform bundles may omit.
    """
    parts = skill_dir.parts
    try:
        return parts[parts.index("projects") + 1]
    except (ValueError, IndexError):
        return None


def _output_dir(skill_dir: Path) -> Path:
    """Per-skill output folder: <skill_dir>/output.

    For a project skill this resolves to
    .rkm/projects/<project>/skills/<skill>/output — every run stages its result
    here, and pushing to the KB is an explicit, user-decided step.
    """
    return skill_dir / "output"


def _vault_repo_dir(repo: str, project_slug: str) -> Path:
    """The vault dir for a repo — always holds the .rkm-meta.json sidecar, even
    when the code itself resolves elsewhere (a linked/configured checkout)."""
    return LOCAL_RKM / "projects" / project_slug / "repos" / repo


def _resolved_repo_path(repo: str, project_slug: str) -> Path:
    """The local checkout a repo resolves to.

    Reads `resolved_path` from the repo's .rkm-meta.json (written by
    /rai:pull-repos' approach-C resolution) so linked/configured repos point at
    the user's own checkout rather than the vault clone. Falls back to the vault
    dir when the sidecar is missing or has no resolved_path (legacy pulls).
    """
    vault = _vault_repo_dir(repo, project_slug)
    meta = vault / ".rkm-meta.json"
    if meta.is_file():
        try:
            resolved = (json.loads(meta.read_text()) or {}).get("resolved_path")
        except (OSError, ValueError):
            resolved = None
        if resolved:
            return Path(resolved)
    return vault


def _resolve_source_path(manifest: dict, project_slug: str | None) -> Path | None:
    """Resolve a skill's source-code path inside its declared repository.

    skill.yaml declares:
        source:
          repo: <repo-name>      # a repo pulled via /rai:pull-repos
          path: <relative/path>  # subdir within that repo (optional)

    Resolves against the repo's recorded `resolved_path` (the linked/configured
    checkout or the vault clone) so the skill operates on the actual code.
    Returns None when no source is declared (prompt skills, self-contained
    scripts) or no project slug is known.

    Security (T2 / S3): `source.path` must stay within the repo root. A path that
    escapes via `..` or an absolute path raises ValueError rather than letting a
    malicious bundle read/write outside the checkout.
    """
    src = manifest.get("source") or {}
    repo = src.get("repo")
    if not repo or not project_slug:
        return None
    root = _resolved_repo_path(repo, project_slug)
    # source.path is repo-relative (a subdir of the checkout). Strip a leading
    # slash so an absolute-looking value is treated as repo-relative, not as a
    # filesystem-absolute escape; `..` traversal is caught by the guard below.
    rel = (src.get("path") or "").strip().lstrip("/")
    if not rel:
        return root
    candidate = root / rel
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and not candidate_resolved.is_relative_to(root_resolved):
        raise ValueError(
            f"source.path {src.get('path')!r} escapes repo root {root}; "
            f"declare a path inside the repository"
        )
    return candidate


def _resolve_repo_root(manifest: dict, project_slug: str | None) -> Path | None:
    """Repo checkout root for a skill's declared source.

    `_resolve_source_path` may point at a subdir (source.path); the gitnexus
    index always lives at the repo root, so reindexing targets the root, not the
    subdir. Uses the same resolved_path map so reindex hits the linked/configured
    checkout (where the index lives), not the empty vault dir. Returns None when
    no source repo is declared.
    """
    src = manifest.get("source") or {}
    repo = src.get("repo")
    if not repo or not project_slug:
        return None
    return _resolved_repo_path(repo, project_slug)


def _source_resolution(manifest: dict, project_slug: str | None) -> dict | None:
    """Surface a skill's source-repo resolution metadata for the run log (T6).

    Reads the vault .rkm-meta.json written by /rai:pull-repos so the .run.json
    records which checkout the source resolved to (linked/configured/clone) and
    its local index status — making a run's code context auditable. Best-effort:
    returns None when no source is declared or the metadata is unavailable.
    """
    src = manifest.get("source") or {}
    repo = src.get("repo")
    if not repo or not project_slug:
        return None
    meta = _vault_repo_dir(repo, project_slug) / ".rkm-meta.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text()) or {}
    except (OSError, ValueError):
        return None
    return {
        "repo": repo,
        "resolution_source": data.get("resolution_source"),
        "resolved_path": data.get("resolved_path"),
        "index_status": data.get("index_status"),
        "local_indexed_commit": data.get("local_indexed_commit"),
    }


def _maybe_reindex(manifest: dict, project_slug: str | None) -> dict | None:
    """Refresh the GitNexus index after a successful run (T3).

    Returns a run_log["reindex"] dict, or None when no source repo is declared.
    Best-effort and gated on an existing index — building the initial index is
    /rai:pull-repos' job, not a side effect of running a skill. `reindex` stays
    incremental on a clean tree (cheap) but escalates to a full --force re-parse
    when the checkout is dirty, because incremental analyze only indexes committed
    state — the uncommitted files a code-gen skill just wrote would otherwise be
    invisible to the next step ("force only when files changed"). Never raises.
    """
    repo_root = _resolve_repo_root(manifest, project_slug)
    if repo_root is None:
        return None
    if _gnx is None:
        return {"ran": False, "reason": "gitnexus_index module unavailable"}
    if not _gnx.is_indexed(repo_root):
        return {"ran": False, "reason": "no local gitnexus index"}
    ok, log = _gnx.reindex(repo_root)
    return {"ran": True, "ok": ok, "log": log[-500:]}


def _venv_path(slug: str, bundle_hash: str) -> Path:
    return VENVS_DIR / f"{slug}-{bundle_hash[:12]}"


def _bundle_hash(skill_dir: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file() and f.name != ".rkm-etag":
            h.update(f.read_bytes())
    return h.hexdigest()


def _ensure_venv(skill_dir: Path, slug: str, manifest: dict) -> Path:
    bh = _bundle_hash(skill_dir)
    venv = _venv_path(slug, bh)
    if (venv / "bin" / "python").exists():
        return venv

    venv.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

    req_file = skill_dir / "scripts" / "requirements.txt"
    if req_file.exists():
        pip = venv / "bin" / "pip"
        subprocess.run([str(pip), "install", "-r", str(req_file)], check=True)

    return venv


def _run_script(
    script: Path,
    venv: Path,
    input_json: str,
    timeout: int,
    env_extra: dict | None = None,
) -> str:
    python = venv / "bin" / "python"
    env = os.environ.copy()
    if env_extra:
        env.update({k: v for k, v in env_extra.items() if v is not None})
    result = subprocess.run(
        [str(python), str(script)],
        input=input_json,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Script exited {result.returncode}:\n{result.stderr}")
    return result.stdout


def _read_local_kb(project_slug: str | None) -> str:
    """Read local KB vault files for context injection. Best-effort, returns '' on any failure."""
    try:
        kb_dirs = []
        if project_slug:
            kb_dirs.append(LOCAL_RKM / "projects" / project_slug / "kb")
        kb_dirs.append(LOCAL_RKM / "org" / "kb")
        for kb_dir in kb_dirs:
            if kb_dir.is_dir():
                files = sorted(kb_dir.glob("*.md"))[:3]
                if files:
                    parts = [f.read_text(encoding="utf-8", errors="replace")[:1000] for f in files]
                    return "<retrieved_context>\n" + "\n\n".join(parts) + "\n</retrieved_context>"
    except Exception:
        pass
    return ""


def _stage_output(output_dir: Path, ts: int, output_text: str) -> str:
    """Stage a run's output into the per-skill output folder and return its path.

    Output is *staged*, never auto-pushed: the user decides afterwards whether to
    send it to the KB (see /rai:run-skill and /rai:push-kb). Best-effort write —
    a staging failure must not fail an otherwise-successful run, so we swallow and
    return ''.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{ts}.md"
        out_file.write_text(output_text, encoding="utf-8")
        return str(out_file)
    except Exception:
        return ""


def _execute_script_runtime(
    skill_dir: Path,
    manifest: dict,
    venv: Path,
    input_json: str,
    script_timeout: int,
    start: float,
    run_log: dict,
    env_extra: dict | None = None,
) -> str:
    """Run a 'script' runtime skill and update run_log in-place. Returns output text."""
    entry_point = manifest.get("script", {}).get("entry_point", "run.py").removeprefix("scripts/")
    entry = skill_dir / "scripts" / entry_point
    output = _run_script(entry, venv, input_json, script_timeout, env_extra)
    run_log["output"] = output
    run_log["status"] = "succeeded"
    run_log["duration_ms"] = int((time.time() - start) * 1000)
    return output


def _execute_hybrid_runtime(
    skill_dir: Path,
    venv: Path,
    input_json: str,
    script_timeout: int,
    start: float,
    run_log: dict,
    env_extra: dict | None = None,
) -> str:
    """Run a 'hybrid' runtime skill and update run_log in-place. Returns serialised log as output."""
    pre_script = skill_dir / "scripts" / "pre.py"
    post_script = skill_dir / "scripts" / "post.py"
    skill_md = (skill_dir / SKILL_MD).read_text() if (skill_dir / SKILL_MD).exists() else ""

    script_context = ""
    if pre_script.exists():
        script_context = _run_script(pre_script, venv, input_json, script_timeout, env_extra)

    # Claude invocation placeholder — in plugin context, caller handles LLM step
    run_log["hybrid_pre_output"] = script_context
    run_log["skill_md"] = skill_md
    run_log["message"] = (
        "Hybrid pre-processing complete. Pass 'skill_md' and 'hybrid_pre_output' "
        "as context to Claude, then call execute() again with the LLM output as input_json "
        "to run the post-processor."
    )

    if post_script.exists() and input_json.strip().startswith("{"):
        try:
            output = _run_script(post_script, venv, input_json, script_timeout, env_extra)
            run_log["output"] = output
        except Exception:
            pass  # post not yet applicable

    run_log["status"] = "succeeded"
    run_log["duration_ms"] = int((time.time() - start) * 1000)
    return json.dumps(run_log)


def _write_run_log(run_log: dict, slug: str, ts: int, output_dir: Path | None = None) -> str:
    """Persist run_log next to its output and return the log file path.

    When an output_dir is given (project/org skills) the log lands in
    <skill>/output/<ts>.run.json, beside the staged <ts>.md. Falls back to the
    legacy .rkm/runs/ location only when no output dir is available.
    """
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / f"{ts}.run.json"
    else:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = RUNS_DIR / f"{ts}-{slug}.json"
    log_path.write_text(json.dumps(run_log, indent=2))
    return str(log_path)


def execute(slug: str, input_json: str, timeout: int = 60) -> dict:
    skill_dir = _skill_dir(slug)
    manifest = _load_manifest(skill_dir)
    runtime = manifest.get("runtime", "prompt")

    if runtime == "prompt":
        skill_md = (skill_dir / SKILL_MD).read_text() if (skill_dir / SKILL_MD).exists() else ""
        return {
            "runtime": "prompt",
            "message": "Prompt-runtime skills run via Claude directly.",
            "skill_md_path": str(skill_dir / SKILL_MD),
            "skill_md_preview": skill_md[:500],
        }

    script_timeout = manifest.get("script", {}).get("timeout_seconds", timeout)
    venv = _ensure_venv(skill_dir, slug, manifest)
    start = time.time()
    ts = int(start)
    run_date = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Prefer the path-derived project slug (robust for platform bundles that omit
    # the manifest 'project' field); fall back to the manifest value.
    project_slug: str | None = _project_slug_from_dir(skill_dir) or manifest.get("project") or None
    runner = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    skill_name = manifest.get("name", slug)

    # Per-skill output folder (req 3) + source-code path inside a pulled repo (req 2).
    output_dir = _output_dir(skill_dir)
    source_path = _resolve_source_path(manifest, project_slug)
    source_missing = bool(source_path) and not source_path.exists()
    env_extra = {
        "RAI_PROJECT_SLUG": project_slug,
        "RAI_OUTPUT_DIR": str(output_dir),
        "RAI_SOURCE_PATH": str(source_path) if source_path else None,
    }

    run_log: dict = {
        "slug": slug,
        "skill_name": skill_name,
        "runtime": runtime,
        "project": project_slug,
        "runner": runner,
        "run_date": run_date,
        "input": input_json,
        "input_files": [],
        "output_files": [],
        "source_path": str(source_path) if source_path else None,
        "output_dir": str(output_dir),
        "kb_pushed": False,
        "status": "pending",
    }
    if source_missing:
        run_log["source_warning"] = (
            f"Declared source repo not found at {source_path}. "
            f"Pull it first with /rai:pull-repos."
        )

    # T6 observability: record where the source resolved (linked/clone) + index status.
    resolution = _source_resolution(manifest, project_slug)
    if resolution is not None:
        run_log["source_resolution"] = resolution

    # KB-read: inject local vault context if skill opts in (E1a)
    kb_in = manifest.get("kb_input_contract") or {}
    if kb_in.get("enabled") and kb_in.get("retrieval", "none") != "none":
        ctx = _read_local_kb(project_slug)
        if ctx:
            input_json = ctx + "\n\n" + input_json

    output = ""
    try:
        if runtime == "script":
            output = _execute_script_runtime(
                skill_dir, manifest, venv, input_json, script_timeout, start, run_log, env_extra
            )
        elif runtime == "hybrid":
            output = _execute_hybrid_runtime(
                skill_dir, venv, input_json, script_timeout, start, run_log, env_extra
            )
        else:
            raise ValueError(f"Unknown runtime: {runtime}")

    except Exception as exc:
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        run_log["duration_ms"] = int((time.time() - start) * 1000)
        run_log["log_path"] = _write_run_log(run_log, slug, ts, output_dir)
        raise

    # Stage the output into <skill>/output (req 3). Pushing to the KB is a separate,
    # user-decided step (/rai:run-skill --push or /rai:push-kb) — never automatic.
    if output:
        run_log["output_path"] = _stage_output(output_dir, ts, output)
        run_log["output_files"] = [run_log["output_path"]] if run_log["output_path"] else []

    reindex = _maybe_reindex(manifest, project_slug)
    if reindex is not None:
        run_log["reindex"] = reindex

    run_log["log_path"] = _write_run_log(run_log, slug, ts, output_dir)
    return run_log
