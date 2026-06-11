# /rai:pull-skills

Pull skill bundles from the platform. Org-level skills (shared across projects) go to `.rkm/org/skills/`; project-level skills go to `.rkm/projects/<slug>/skills/`.

## Usage

```
/rai:pull-skills [--all] [--slug <slug>...] [--tag <tag>] [--scope org|project|both] [--force]
```

## Steps

1. Parse arguments:
   - `--all` → `all: true`
   - `--slug foo --slug bar` → `slugs: ["foo", "bar"]`
   - `--tag meetings` → `tag: "meetings"`
   - `--scope org` → `scope: "org"` (default: `"both"`)
   - `--force` → `force: true`
2. Call `rkm_pull_skills` with the parsed arguments.
3. Display the result:
   ```
   Skills: <N> pulled, <M> skipped (ETag match), <K> conflicts
   Org skills   → .rkm/org/skills/
   Proj skills  → .rkm/projects/<slug>/skills/
   ```
4. If there are conflicts, list them and explain: local edits detected in `.rkm/org/skills/<slug>/` or `.rkm/projects/<slug>/skills/<slug>/`. Pass `--force` to overwrite.

## Notes

- Each skill is unpacked as a Phase B bundle: `skill.yaml`, `SKILL.md`, optional `scripts/`.
- Script and hybrid skills show a runtime badge — review `scripts/` before executing.
- A skill is org-level when the platform returns `project_id: null`; otherwise it is project-level.
- **Org skills can be selected individually** — use `--slug <slug>...` here, or use the `/rai:pull` interactive picker which lists org skills by name before pulling. `--scope project` always pulls all project skills (no per-skill selection).
