# /rai:pull-kb

Pull knowledge base (sources and wiki pages) to `.rkm/kb/`.

## Usage

```
/rai:pull-kb [--all] [--wiki-only] [--sources-only] [--force]
```

## Steps

1. Parse arguments:
   - `--wiki-only` → `wiki_only: true`
   - `--sources-only` → `sources_only: true`
   - `--force` → `force: true`
2. Call `rkm_pull_kb` with the parsed arguments.
3. Display the result:
   ```
   KB: <N> wiki pages pulled, <M> sources pulled, <K> skipped (ETag match)
   Written to .rkm/kb/wiki/ and .rkm/kb/sources/
   ```

## Notes

- Wiki pages are written as markdown: `.rkm/kb/wiki/<slug>.md`
- Sources are written as JSON metadata: `.rkm/kb/sources/<id>.json` (extracted text included, original binary skipped)
- Use `--wiki-only` for writing and documentation workflows (curated knowledge)
- Use `--sources-only` for research workflows (raw ingested content)
