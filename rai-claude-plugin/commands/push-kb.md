# /rai:push-kb

Push local knowledge base (sources and wiki pages) from `.rkm/kb/` to the platform.

## Usage

```
/rai:push-kb [--sources-only] [--wiki-only] [--force]
```

## Steps

1. Parse arguments:
   - `--sources-only` → `sources_only: true`
   - `--wiki-only` → `wiki_only: true`
   - `--force` → `force: true`
2. Call `rkm_push_kb` with the parsed arguments.
3. Display the result:
   ```
   Pushed <S> sources, <W> wiki pages (<K> skipped — ETag match)
   Sources → platform will auto-ingest and compile its own wiki.
   Wiki    → pages created or updated on the platform.
   ```
4. If `errors` is non-empty, list each error clearly so the user can fix and retry.

## Output layout

```
.rkm/kb/
├── wiki/<slug>.md      ← pushed to POST /api/v1/wiki/ (create) or PATCH /api/v1/wiki/<id> (update)
└── sources/<id>.json   ← pushed to POST /api/v1/sources/text or /url; platform_id written back
```

## Notes

- **Pushing a source triggers the platform's worker** to also auto-compile its own wiki for that source — you may end up with both the curated local wiki AND a platform-generated one. Use `--wiki-only` to push only the curated local pages without re-uploading sources.
- **ETag caching:** subsequent pushes skip unchanged sources/wiki (based on content hash). Use `--force` to re-push everything.
- **Slugs:** wiki pages are matched on the platform by slug (from the frontmatter). Keep titles (and therefore slugs) stable to allow idempotent updates.
- **source_ids linkage:** manually-pushed wiki pages are not DB-linked to source rows via `wiki_page_sources` (that linkage only happens via the platform's own `compile_source` worker). The `source_ids` frontmatter is preserved locally but is informational only on push.
- Run `/rai:login` first if you haven't authenticated yet.
