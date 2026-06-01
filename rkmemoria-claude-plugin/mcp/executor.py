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
from pathlib import Path

import yaml

LOCAL_RKM = Path.cwd() / ".rkm"
VENVS_DIR = Path.home() / ".rkm" / "venvs"
RUNS_DIR = LOCAL_RKM / "runs"


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


def execute(slug: str, input_json: str, timeout: int = 60) -> dict:
    skill_dir = _skill_dir(slug)
    manifest = _load_manifest(skill_dir)
    runtime = manifest.get("runtime", "prompt")

    if runtime == "prompt":
        skill_md = (skill_dir / "SKILL.md").read_text() if (skill_dir / "SKILL.md").exists() else ""
        return {
            "runtime": "prompt",
            "message": "Prompt-runtime skills run via Claude directly.",
            "skill_md_path": str(skill_dir / "SKILL.md"),
            "skill_md_preview": skill_md[:500],
        }

    script_timeout = manifest.get("script", {}).get("timeout_seconds", timeout)
    venv = _ensure_venv(skill_dir, slug, manifest)
    start = time.time()
    run_log: dict = {"slug": slug, "runtime": runtime, "input": input_json}

    if runtime == "script":
        entry = skill_dir / "scripts" / manifest.get("script", {}).get("entry_point", "run.py").lstrip("scripts/")
        output = _run_script(entry, venv, input_json, script_timeout)
        run_log["output"] = output
        run_log["duration_ms"] = int((time.time() - start) * 1000)

    elif runtime == "hybrid":
        pre_script = skill_dir / "scripts" / "pre.py"
        post_script = skill_dir / "scripts" / "post.py"
        skill_md = (skill_dir / "SKILL.md").read_text() if (skill_dir / "SKILL.md").exists() else ""

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
                # If caller passes LLM output as input, run post
                output = _run_script(post_script, venv, input_json, script_timeout)
                run_log["output"] = output
            except Exception:
                pass  # post not yet applicable

        run_log["duration_ms"] = int((time.time() - start) * 1000)
        output = json.dumps(run_log)

    else:
        raise ValueError(f"Unknown runtime: {runtime}")

    # Write run log
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    log_path = RUNS_DIR / f"{ts}-{slug}.json"
    log_path.write_text(json.dumps(run_log, indent=2))
    run_log["log_path"] = str(log_path)

    return run_log
