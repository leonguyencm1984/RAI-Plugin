# /rai:set-kb

Set the local knowledge-base folder for this project.

## Usage

```
/rai:set-kb <folder>
/rai:set-kb --clear
```

`<folder>` may be absolute (`/data/kb`) or relative to the project root (`./docs/kb`).

## Steps

1. Parse arguments:
   - If `--clear` is present, set `clear: true` and omit `folder`.
   - Otherwise, `<folder>` is required.
2. Call `rkm_set_kb` with `{folder, clear}`.
3. Print the result:
   ```
   KB folder set to: <kb_dir>
     wiki/    → <wiki_dir>
     sources/ → <sources_dir>
   Config:    <config_path>
   ```
   If `--clear` was used, print:
   ```
   KB folder reset to default: <kb_dir>
   ```

## Notes

- The default KB folder (when unset) is `.rkm/kb` in the project root.
- `--clear` removes the `kb_dir` override and reverts to the default.
- Relative paths resolve against the project root (current working directory); absolute paths are used as-is.
- The setting is persisted in `.rkm/config.json` under the key `kb_dir`. This file is safe to commit — it never stores your token.
- All KB commands honour this setting: `/rai:ingest`, `/rai:pull-kb`, `/rai:push-kb`, and `/rai:status`.
- This command does not require being logged in (`/rai:login`).
