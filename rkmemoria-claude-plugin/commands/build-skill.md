# /rai:build-skill

Build a `.rai` skill archive from a local `SKILL.md` file, ready to upload to the platform.

## Usage

```
/rai:build-skill <path-to-SKILL.md> [--output <path>] [--runtime prompt|script|hybrid] [--force-yaml]
```

## Steps

1. Parse arguments:
   - `<path-to-SKILL.md>` (required) — path to the `SKILL.md` file inside the skill directory.
   - `--output <path>` — destination for the `.rai` file (default: `<slug>.rai` next to the skill directory).
   - `--runtime prompt|script|hybrid` — override the `runtime` field when generating `skill.yaml`.
   - `--force-yaml` — re-generate `skill.yaml` even if one already exists.

2. Run the packager via Bash:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/mcp/skill_packager.py" <path-to-SKILL.md> [--output <path>] [--runtime <rt>] [--force-yaml]
   ```

3. Display the output verbatim — it includes:
   - The `.rai` file path and whether it is signed.
   - A full member listing of the archive.
   - Next-step instructions (upload via UI, dry-run curl, or `/rai:push-skill`).

4. If the packager exits with an error (missing `SKILL.md`, missing required archive
   members, bad slug), surface the error to the user and stop.

## Notes

- **`skill.yaml` auto-generation**: if no `skill.yaml` exists beside the `SKILL.md`, one
  is generated from the `SKILL.md` YAML frontmatter (`name`, `description`, `version`).
  The slug is derived from `name` (lowercased, spaces → hyphens). The generated file is
  written into the skill directory so it is included in the archive.

- **`runtime: script` skills**: if the skill directory has a `scripts/` subdirectory but
  `--runtime` was not given, the packager defaults to `runtime: prompt` and prints a
  warning. Pass `--runtime script` (or `hybrid`) in that case, then edit the generated
  `skill.yaml` to add `variables` entries and confirm the entry-point script name before
  uploading.

- **Security**: the packager warns if any `*.env`, `credentials*`, or other sensitive
  filenames are included, and scans text files for patterns like `password=` or `api_key=`.
  Review all warnings before uploading to the platform.

- **Uploading**: after building, push to the platform with:
  - UI: **Skills → Import** — drop the `.rai` file, review the dry-run preview, import.
  - CLI: use the curl dry-run command shown in the output to validate first.
  - Plugin: `/rai:push-skill <slug>` (requires the skill to have been pulled first with
    `/rai:pull-skills`).

## Example

```
/rai:build-skill skills/my-skill/SKILL.md
/rai:build-skill skills/my-skill/SKILL.md --runtime script --output /tmp/my-skill.rai
```
