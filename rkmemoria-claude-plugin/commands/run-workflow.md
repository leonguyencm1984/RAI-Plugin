# /rkm:run-workflow

Run a platform workflow by ID.

## Usage

```
/rkm:run-workflow <id> [--input '<json>']
```

## Steps

1. Parse arguments: `id` (integer, required), optional `--input` (JSON string).
2. Call `rkm_run_workflow` with `workflow_id` and `inputs`.
3. Display the run ID and initial status:
   ```
   Workflow run started: run_id=<id>, status=<status>
   Use /rkm:run-status <run_id> to check progress.
   ```
4. If the response is an error (e.g. 422 `server_execution_not_yet_enabled`), explain the limitation and suggest running the constituent skills locally with `/rkm:run-skill`.

## Notes

- Workflows with `server_executable: false` script steps will fail server-side until the sandbox phase ships.
- Check `/rkm:list-workflows` for available IDs.
