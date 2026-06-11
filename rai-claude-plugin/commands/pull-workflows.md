# /rai:pull-workflows

Pull workflows from the platform to `.rkm/projects/<slug>/workflows/`. Workflows are always project-scoped.

## Usage

```
/rai:pull-workflows [--all] [--id <id>...] [--force]
```

## Steps

1. Parse arguments from the user's message:
   - `--all` → `all: true`
   - `--id 1 --id 2` → `ids: [1, 2]`
   - `--force` → `force: true`
2. Call `rkm_pull_workflows` with the parsed arguments.
3. Display the result:
   ```
   Workflows: <N> pulled, <M> skipped (ETag match)
   Written to .rkm/projects/<slug>/workflows/
   ```

## Notes

- Workflows are always project-scoped (no org-level workflows exist on the platform).
- The active project slug comes from `~/.rkm/config.json` set at login.
