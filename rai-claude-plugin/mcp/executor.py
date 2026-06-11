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

LOCAL_RKM = Path.cwd() / ".rkm"
VENVS_DIR = Path.home() / ".rkm" / "venvs"
RUNS_DIR = LOCAL_RKM / "runs"
SKILL_MD = "SKILL.md"  # noqa: S1192


def _skill_dir(slug: str) -> Path:
    d = LOCAL_RKM / "skills" / slug
    if not d.exists():
        raise FileNotFoundError(f"Skill '{slug}' not found in .rkm/skills/")
    return d


def _load_manifest(skill_dir: Path) -> dict:
    manifest_path = skill_dir / "skill.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"skill.yaml not found in {skill_dir}")
    return yaml.safe_load(manifest_path.read_text())


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


def _run_script(script: Path, venv: Path, input_json: str, timeout: int) -> str:
    python = venv / "bin" / "python"
    result = subprocess.run(
        [str(python), str(script)],
        input=input_json,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def _push_local_kb(project_slug: str | None, slug: str, ts: int, output_text: str) -> None:
    """Write skill output to local KB vault. Best-effort, silently ignores failures."""
    try:
        if project_slug:
            kb_dir = LOCAL_RKM / "projects" / project_slug / "kb"
        else:
            kb_dir = LOCAL_RKM / "org" / "kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        kb_file = kb_dir / f"run-{ts}-{slug}.md"
        kb_file.write_text(output_text, encoding="utf-8")
    except Exception:
        pass


def _execute_script_runtime(
    skill_dir: Path,
    manifest: dict,
    venv: Path,
    input_json: str,
    script_timeout: int,
    start: float,
    run_log: dict,
) -> str:
    """Run a 'script' runtime skill and update run_log in-place. Returns output text."""
    entry_point = manifest.get("script", {}).get("entry_point", "run.py").removeprefix("scripts/")
    entry = skill_dir / "scripts" / entry_point
    output = _run_script(entry, venv, input_json, script_timeout)
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
) -> str:
    """Run a 'hybrid' runtime skill and update run_log in-place. Returns serialised log as output."""
    pre_script = skill_dir / "scripts" / "pre.py"
    post_script = skill_dir / "scripts" / "post.py"
    skill_md = (skill_dir / SKILL_MD).read_text() if (skill_dir / SKILL_MD).exists() else ""

    script_context = ""
    if pre_script.exists():
        script_context = _run_script(pre_script, venv, input_json, script_timeout)

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
            output = _run_script(post_script, venv, input_json, script_timeout)
            run_log["output"] = output
        except Exception:
            pass  # post not yet applicable

    run_log["status"] = "succeeded"
    run_log["duration_ms"] = int((time.time() - start) * 1000)
    return json.dumps(run_log)


def _write_run_log(run_log: dict, slug: str, ts: int) -> str:
    """Persist run_log to disk and return the log file path."""
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
    project_slug: str | None = manifest.get("project") or None
    runner = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    skill_name = manifest.get("name", slug)

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
        "status": "pending",
    }

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
                skill_dir, manifest, venv, input_json, script_timeout, start, run_log
            )
        elif runtime == "hybrid":
            output = _execute_hybrid_runtime(
                skill_dir, venv, input_json, script_timeout, start, run_log
            )
        else:
            raise ValueError(f"Unknown runtime: {runtime}")

    except Exception as exc:
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        run_log["duration_ms"] = int((time.time() - start) * 1000)
        run_log["log_path"] = _write_run_log(run_log, slug, ts)
        raise

    # KB-push: write output to local vault on success if skill opts in (E1b)
    kb_out = manifest.get("kb_output_contract") or {}
    if kb_out.get("enabled") and output:
        _push_local_kb(project_slug, slug, ts, output)

    run_log["log_path"] = _write_run_log(run_log, slug, ts)
    return run_log
