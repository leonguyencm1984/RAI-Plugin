# /rai:pull

Interactive scope × category picker. Pulls org-level and/or project-level resources from the platform into a structured, Obsidian-readable `.rkm/` vault.

## Folder layout

```
.rkm/
├── .obsidian/app.json          ← single Obsidian vault root
├── INDEX.md                    ← root Map of Content
├── org/
│   ├── kb/wiki/                ← org-visibility wiki pages
│   └── kb/sources/             ← org-visibility sources
│   └── skills/                 ← org-level skills (project_id = null)
└── projects/<slug>/
    ├── kb/wiki/                ← project-visibility wiki pages
    ├── kb/sources/             ← project-visibility sources
    ├── skills/                 ← project-level skills
    ├── workflows/              ← workflows (always project-scoped)
    └── repos/                  ← repository metadata
```

## Steps

1. Call `rkm_status` to get current counts and the active project slug.

2. Present **two** multi-select questions:

   **Question 1 — Scope** (multi-select):
   ```
   Which scope(s) to pull from?
     [ ] Org-wide resources  (.rkm/org/)
     [ ] Project: <project_slug>  (.rkm/projects/<project_slug>/)
   ```

   **Question 2 — Categories** (multi-select):
   ```
   Which categories?
     [ ] Skills
     [ ] Workflows
     [ ] Knowledge base — Wiki
     [ ] Knowledge base — Sources
   ```

   **Question 3 — Strategy** (single-select):
   ```
   Refresh strategy:
     (•) Only new/changed   (skip items matching platform ETag)
     ( ) Force re-pull all  (overwrite local copies)
   ```

3. Build the call matrix. For each (scope, category) combination:

   | Scope | Category | Tool call |
   |-------|----------|-----------|
   | Org | Skills | **sub-picker** — see step 3a below |
   | Org | Workflows | **Skip** — workflows are project-scoped only (explain to user) |
   | Org | KB — Wiki | `rkm_pull_kb` with `wiki_only: true, scope: "org"` |
   | Org | KB — Sources | `rkm_pull_kb` with `sources_only: true, scope: "org"` |
   | Project | Skills | `rkm_pull_skills` with `all: true, scope: "project"` |
   | Project | Workflows | `rkm_pull_workflows` with `all: true` |
   | Project | KB — Wiki | `rkm_pull_kb` with `wiki_only: true, scope: "project"` |
   | Project | KB — Sources | `rkm_pull_kb` with `sources_only: true, scope: "project"` |

   **Note:** Do NOT merge Org + Project Skills into a single `scope:"both"` call — they must run as separate calls so the org sub-picker (3a) can be applied to org skills while project pulls all. KB categories (wiki/sources) may still be merged with `scope:"both"` if both scopes are selected.

   **3a. Org × Skills sub-picker:**
   1. Call `rkm_list_skills` with `scope: "org"`.
   2. If the returned `org` list is empty → tell the user "No org-level skills available" and skip.
   3. Present the org skills so the user can choose which ones to pull:
      - **If there are ≤ 4 org skills:** use a multi-select `AskUserQuestion` with one option per skill (label = skill name, description = `slug · tags · runtime`), plus an **"All org skills"** option as the last choice.
      - **If there are > 4 org skills:** list them as a numbered markdown table (columns: #, Name, Slug, Tags, Runtime), then ask the user to reply with the numbers or slugs they want (or "all"). Wait for the reply before proceeding.
   4. Resolve the selection:
      - User chose "All" (or replied "all") → call `rkm_pull_skills` with `all: true, scope: "org"`.
      - User chose specific skills → call `rkm_pull_skills` with `slugs: [<chosen slugs>], scope: "org"`.
      - In both cases, include `force: true` if the user selected the "Force re-pull all" strategy.

   - If user selected "Force re-pull all", add `force: true` to every call.

4. Run the tool calls (Skills and KB can run in parallel for each scope).

5. After all calls complete, print a structured summary:
   ```
   ✓ Org skills:      <N> pulled, <M> skipped (ETag), <K> conflicts
   ✓ Project skills:  <N> pulled, <M> skipped (ETag), <K> conflicts
   ✓ Workflows:       <N> pulled, <M> skipped (ETag)
   ✓ Org wiki:        <N> pulled, <M> skipped (ETag)
   ✓ Project wiki:    <N> pulled, <M> skipped (ETag)
   ✓ Org sources:     <N> pulled, <M> skipped (ETag)
   ✓ Project sources: <N> pulled, <M> skipped (ETag)

   Vault: .rkm/  →  open in Obsidian
   ```

6. If there are conflicts (skills only), list each slug and explain:
   > Local edits detected in `.rkm/org/skills/<slug>/` or `.rkm/projects/<slug>/skills/<slug>/`.
   > Re-run with `--force` flag or use `/rai:pull-skills --slug <slug> --force` to overwrite.

## Notes

- Repo pulls are not included — use `/rai:pull-repos` (requires git on the machine).
- The MCP token is bound to one project. Other project folders accumulate naturally when you re-login to a different project via `/rai:login`.
- Open `.rkm/` as an Obsidian vault — the `.obsidian/` config and `INDEX.md` are written automatically.
- `kb_dir` in `.rkm/config.json` (set via `/rai:set-kb`) overrides the **project** KB root only; org KB always lives at `.rkm/org/kb/`.
