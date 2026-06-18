# /rai:run-skill

Execute a pulled skill locally (script and hybrid runtimes only). Outputs are
staged into the skill's `output/` folder; pushing to the KB is your decision.

## Usage

```
/rai:run-skill <slug> [--input '<json>'] [--push]
```

## Steps

1. Parse arguments: `slug`, optional `--input` (JSON string), `--push` flag.
2. Load the skill's `skill.yaml` (found under `.rkm/projects/<slug>/skills/<slug>/`
   or `.rkm/org/skills/<slug>/`) to determine `runtime`.
3. If `runtime: prompt`:
   - Tell the user: "This is a prompt-runtime skill. The SKILL.md is at
     `<skill_dir>/SKILL.md` — use it as context for Claude directly." Stop.
4. If `runtime: script` or `hybrid`:
   - Import and call `executor.execute(slug, input_json)` from `mcp/executor.py`.
   - The executor:
     - Resolves the skill's **source code path** from `skill.yaml` `source:`
       (`repo` + `path`) against the pulled checkout
       `.rkm/projects/<slug>/repos/<repo>/<path>`, and exposes it to the script as
       the env var `RAI_SOURCE_PATH` (plus `RAI_OUTPUT_DIR`, `RAI_PROJECT_SLUG`).
       If the repo has not been pulled, the run still proceeds and the run log
       carries a `source_warning` — pull it with `/rai:pull-repos`.
     - **Stages the output** into `<skill_dir>/output/<timestamp>.md` and writes the
       run log to `<skill_dir>/output/<timestamp>.run.json`.
   - Display the output and its `output_path`.
5. **Decide whether to push to the KB** (outputs are never auto-pushed):
   - Ask the user: "Push this output to the Knowledge Base? (y/n)".
   - On yes, **or** when `--push` was supplied, call the `rkm_push_run` MCP tool with
     `{slug, status, output_md, error}` — `status` is `succeeded` when the run exit
     code was 0, else `failed`; `output_md` is the staged output. This records the
     run via `POST /api/v1/skill-runs/record` (record-only — it does NOT re-execute)
     and, when the skill's `kb_output_contract.enabled` is set server-side, ingests
     the output into the KB. Set `kb_pushed: true` in the local run log afterward.
   - On no, leave the output staged in `output/`; it can be pushed later.

## `skill.yaml` — source declaration

```yaml
runtime: script            # script | hybrid | prompt
source:                    # optional — links the skill to repo code (req 2)
  repo: apollo             # a repo name pulled via /rai:pull-repos
  path: services/billing   # subdir within that repo (optional)
script:
  entry_point: scripts/run.py
  timeout_seconds: 60
kb_output_contract:
  enabled: true            # gates server-side KB ingest on push (still user-decided)
```

## Notes

- First run provisions a venv at `~/.rkm/venvs/<slug>-<hash>/` — may take 30–60s.
  Subsequent runs reuse the cached venv (fast).
- Output layout per skill: `<skill_dir>/output/<timestamp>.md` (result) and
  `<timestamp>.run.json` (run log). Re-running appends a new timestamped pair.
- Scripts read input on stdin (JSON) and can write artifacts under `RAI_OUTPUT_DIR`.
- Hybrid skills: the command handles the pre → Claude → post chain. For the LLM
  step it uses the current Claude Code conversation.
