# /rkm:pull-skills

Pull skill bundles from the platform to `.rkm/skills/`.

## Usage

```
/rkm:pull-skills [--all] [--slug <slug>...] [--tag <tag>] [--force]
```

## Steps

1. Parse arguments:
   - `--all` → `all: true`
   - `--slug foo --slug bar` → `slugs: ["foo", "bar"]`
   - `--tag meetings` → `tag: "meetings"`
   - `--force` → `force: true`
2. Call `rkm_pull_skills` with the parsed arguments.
3. Display the result:
   ```
   Skills: <N> pulled, <M> skipped (ETag match), <K> conflicts
   Written to .rkm/skills/
   ```
4. If there are conflicts, list them and explain: edit `.rkm/skills/<slug>/` was detected. Pass `--force` to overwrite with the platform version.

## Notes

- Each skill is unpacked as a Phase B bundle: `skill.yaml`, `SKILL.md`, optional `scripts/`.
- Script and hybrid skills show a runtime badge — review `scripts/` before executing.
