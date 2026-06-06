# /rai:pull

Interactive picker to pull selected categories from the platform.

## Steps

1. Call `rkm_status` to get current counts.
2. Present a checkbox picker to the user:

```
Pull from project "<project_slug>" (<platform_url>):

  [ ] Workflows        (<available> available)
  [ ] Skills           (<available> available)
  [ ] Knowledge base   (<sources_count> sources, <wiki_count> wiki pages)

Refresh strategy: (•) only new/changed   ( ) force re-pull all
```

3. For each category the user checks:
   - **Workflows** → call `rkm_pull_workflows` with `all: true` (or ask which ones)
   - **Skills** → call `rkm_pull_skills` with `all: true` (or ask which slugs/tags)
   - **Knowledge base** → ask: wiki only, sources only, or both → call `rkm_pull_kb`

4. If the user selected "force re-pull all", pass `force: true` to each tool.

5. After each pull, display a summary:
   ```
   ✓ Workflows: 5 pulled, 0 skipped
   ✓ Skills: 3 pulled, 20 skipped (ETag match), 0 conflicts
   ✓ Knowledge base: 5 wiki pulled, 47 sources pulled
   ```

6. If there are conflicts, list them and tell the user to pass `--force` or resolve local edits.

## Notes

- Repo pulls are not included here — use `/rai:pull-repos` (requires git on the machine).
- Run `/rai:status` first to see what's available before pulling.
