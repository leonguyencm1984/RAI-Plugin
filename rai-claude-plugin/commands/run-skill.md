# /rai:run-skill

Execute a pulled skill locally (script and hybrid runtimes only).

## Usage

```
/rai:run-skill <slug> [--input '<json>'] [--push]
```

## Steps

1. Parse arguments: `slug`, optional `--input` (JSON string), `--push` flag.
2. Load `.rkm/skills/<slug>/skill.yaml` to determine runtime.
3. If `runtime: prompt`:
   - Tell the user: "This is a prompt-runtime skill. The SKILL.md is at `.rkm/skills/<slug>/SKILL.md` — use it as context for Claude directly."
   - Stop.
4. If `runtime: script` or `hybrid`:
   - Import and call `executor.execute(slug, input_json)` from `mcp/executor.py`.
   - Display the output JSON.
   - Write a run log to `.rkm/runs/`.
5. If `--push` is set, POST the run to `POST /api/v1/skill-runs` so it appears in the platform audit trail.

## Notes

- First run provisions a venv at `~/.rkm/venvs/<slug>-<hash>/` — may take 30–60s.
- Subsequent runs reuse the cached venv (fast).
- Hybrid skills: the command handles the pre → Claude → post chain. For the LLM step, it uses the current Claude Code conversation.
- Run logs are saved to `.rkm/runs/<timestamp>-<slug>.json`.
