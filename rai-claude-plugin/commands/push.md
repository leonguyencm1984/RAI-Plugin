# /rai:push

Interactive scope × category picker. Pushes local `.rkm/` vault resources back to the platform.

## Folder layout

```
.rkm/
├── org/
│   ├── kb/wiki/                ← org-visibility wiki pages
│   ├── kb/sources/             ← org-visibility sources
│   └── skills/                 ← org-level skills
└── projects/<slug>/
    ├── kb/wiki/                ← project-visibility wiki pages
    ├── kb/sources/             ← project-visibility sources
    ├── skills/                 ← project-level skills
    └── workflows/              ← workflows (always project-scoped)
```

## Steps

1. Call `rkm_status` to get current local counts and the active project slug.

2. Present **three** questions:

   **Question 1 — Scope** (multi-select):
   ```
   Which scope(s) to push?
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
   Push strategy:
     (•) Only new/changed   (skip items matching local ETag)
     ( ) Force re-push all  (re-upload everything)
   ```

3. Build the call matrix. For each (scope, category) combination:

   | Scope | Category | Action |
   |-------|----------|--------|
   | Org | Skills | Glob `.rkm/org/skills/*/skill.yaml`, loop `rkm_push_skill` per slug with `scope: "org"` |
   | Org | Workflows | **Skip** — workflows are project-scoped only (explain to user) |
   | Org | KB — Wiki | `rkm_push_kb` with `wiki_only: true, scope: "org"` |
   | Org | KB — Sources | `rkm_push_kb` with `sources_only: true, scope: "org"` |
   | Project | Skills | Glob `.rkm/projects/<slug>/skills/*/skill.yaml`, loop `rkm_push_skill` per slug with `scope: "project"` |
   | Project | Workflows | `rkm_push_workflows` with `all: true` |
   | Project | KB — Wiki | `rkm_push_kb` with `wiki_only: true, scope: "project"` |
   | Project | KB — Sources | `rkm_push_kb` with `sources_only: true, scope: "project"` |

   - Add `force: true` to every call when Strategy = "Force re-push all".
   - Skills and KB calls for the same scope can run in parallel.

4. After all calls complete, print a structured summary:
   ```
   ✓ Org skills:      <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Project skills:  <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Workflows:       <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Org wiki:        <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Project wiki:    <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Org sources:     <N> pushed, <M> skipped (ETag), <K> errors
   ✓ Project sources: <N> pushed, <M> skipped (ETag), <K> errors
   ```

5. If any `errors` entries exist, list each one clearly (slug + error message) so the user can fix and retry.

6. For skills, surface any `import_status: pending` messages — these mean the push was accepted but requires owner approval at `/admin/approvals`.

## Notes

- Workflows are always project-scoped. Selecting "Org × Workflows" is silently skipped with an explanation.
- Workflow steps reference skills by slug. If a skill slug from the local JSON is not found on the platform, that workflow is skipped and reported in errors — it will not be pushed half-broken.
- **ETag caching:** subsequent pushes skip unchanged items (content-hash based). Use Force strategy to re-push everything.
- Run `/rai:login` first if you haven't authenticated.
- To push a single skill: `/rai:push-skill <slug>`. To push KB only: `/rai:push-kb`.
