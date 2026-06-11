# /rai:pull-kb

Pull knowledge base (sources and wiki pages) to `.rkm/`. Org-visibility items go to `.rkm/org/kb/`; project-visibility items go to `.rkm/projects/<slug>/kb/`.

## Usage

```
/rai:pull-kb [--wiki-only] [--sources-only] [--scope org|project|both] [--force]
```

## Steps

1. Parse arguments:
   - `--wiki-only` → `wiki_only: true`
   - `--sources-only` → `sources_only: true`
   - `--scope org` → `scope: "org"` (default: `"both"`)
   - `--force` → `force: true`
2. Call `rkm_pull_kb` with the parsed arguments.
3. Display the result:
   ```
   KB: <N> wiki pages pulled, <M> sources pulled, <K> skipped (ETag match)
   Org wiki      → .rkm/org/kb/wiki/
   Project wiki  → .rkm/projects/<slug>/kb/wiki/
   Org sources   → .rkm/org/kb/sources/
   Proj sources  → .rkm/projects/<slug>/kb/sources/
   Vault root    → .rkm/  (open in Obsidian)
   ```

## Output layout

```
.rkm/
├── .obsidian/app.json           ← Single vault root (created once; user tweaks preserved)
├── INDEX.md                     ← Root Map of Content
├── org/kb/
│   ├── wiki/
│   │   ├── INDEX.md             ← Org wiki Map of Content
│   │   └── <slug>.md
│   └── sources/
│       └── <id>.json
└── projects/<slug>/kb/
    ├── wiki/
    │   ├── INDEX.md             ← Project wiki Map of Content
    │   └── <slug>.md
    └── sources/
        └── <id>.json
```

Each wiki note has YAML frontmatter (title, slug, summary, project_id, visibility, knowledge_type_id, version, created, updated, source_ids, tags) followed by the full markdown body. `[[wikilinks]]` in note bodies resolve within the single Obsidian vault because all slugs are unique across org and project.

## Notes

- **Browse in Obsidian:** open `.rkm/` as a vault (`File → Open folder as vault`). Start from `INDEX.md`.
- Pages are ETag-cached (`updated_at`); unchanged pages are skipped. INDEX files are always regenerated.
- Use `--force` to re-pull all pages even if unchanged.
- Use `--wiki-only` for writing and documentation workflows (curated knowledge).
- Use `--sources-only` for research workflows (raw ingested content).
- A page is org-level when the platform returns `visibility: "org"`; otherwise it is project-level.
