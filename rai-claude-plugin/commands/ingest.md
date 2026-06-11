# /rai:ingest

Extract a local or remote source and generate wiki pages into `.rkm/kb/` for review.

## Usage

```
/rai:ingest --title "<title>" [--text "<text>"] [--url <url>] [--file <path>] [--folder <path>]
```

Supported file types: `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx`, `.xls`, `.csv`. For `--folder`, pass a directory path or a `.zip` archive — all supported files inside are extracted and concatenated.

## Steps

1. Parse arguments: `--title` (required), plus exactly one of `--text`, `--url`, `--file`, or `--folder`. Optionally `--project-id` and `--knowledge-type-id`.
2. Call `rkm_ingest` with the parsed arguments. This extracts the source text and writes it to `.rkm/kb/sources/<id>.json`. It returns `{source_id, title, source_type, char_count, raw_text}`.
3. Generate wiki pages from `raw_text` using this brief:
   - Identify the distinct topics and concepts in the text.
   - For each topic, produce one wiki page with:
     - `title` — concise page title
     - `slug` — URL-friendly slug (lowercase letters, digits, hyphens only; e.g. `api-authentication`)
     - `summary` — one-paragraph summary of the page
     - `content_md` — full Markdown content; use `[[lowercase-slug]]` wiki-link syntax **only** (never `[[Page Title]]`) for cross-references between pages in this set
   - Aim for focused pages (one concept per page) rather than one giant page.
4. For each wiki page, write `.rkm/kb/wiki/<slug>.md` with this exact structure:
   ```
   ---
   title: "<page title>"
   slug: <slug>
   summary: "<one-paragraph summary>"
   project_id: <project_id from source record, or null>
   knowledge_type_id: <knowledge_type_id or null>
   version: 1
   created: <ISO timestamp>
   updated: <ISO timestamp>
   source_ids: [<source_id>]
   tags: [rkmemoria, wiki]
   ---

   # <page title>

   <content_md>
   ```
5. Display the result:
   ```
   Ingested "<title>" (<source_type>, <N> chars) → <M> wiki pages in .rkm/kb/wiki/
   Review the pages, then run /rai:push-kb to upload them.
   ```

## Notes

- Run `/rai:login` first if you haven't connected to the platform yet.
- Pages are written locally only — nothing is uploaded until you run `/rai:push-kb`.
- If the source is very large (> 80 000 chars) it will be truncated before wiki generation. Consider splitting large sources.
- Pushing the source (via `/rai:push-kb`) will also trigger the platform's worker to auto-compile its own wiki. Use `/rai:push-kb --wiki-only` if you want to upload only the curated local pages.

## Example

```
/rai:ingest --title "Auth Design Doc" --file ./docs/auth-design.pdf
/rai:ingest --title "API Reference" --url https://internal.example.com/api-docs
/rai:ingest --title "Sprint Notes" --folder ./sprint-notes/
```
