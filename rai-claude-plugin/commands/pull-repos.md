# /rai:pull-repos

Resolve each of the current project's code repositories to **one** local checkout,
keep its GitNexus symbol index fresh, and record where the code lives so skills can
generate into the tree you actually ship. The resolved path is written to each repo's
`.rkm-meta.json`; skills point at it via `source.repo` / `source.path`.

## Usage

```
/rai:pull-repos [--all] [--name <name>...] [--shallow] [--force]
```

- `--all` → pull all connected repos (default when no `--name` given)
- `--name apollo` → pull only the repo named "apollo" (repeatable)
- `--shallow` → `git clone --depth 1` (clone branch only)
- `--force` → re-fetch and hard-reset a *vault* checkout to `origin/<branch>`
  (never touches a linked/configured checkout — your working tree is left alone)

## Resolution pipeline (approach C, link-first)

For each repo the `rkm_pull_repos` MCP tool runs `resolve_repo` (`mcp/repo_resolve.py`),
which picks exactly one local path using this precedence:

| # | Source | When | What pull does |
|---|--------|------|----------------|
| 1 | `configured` | `repo_paths[<name>]` in `.rkm/config.json` points at an existing dir | Use that checkout **in place**; index it. Never clones. |
| 2 | `linked` | Exactly one checkout under a search root has an `origin` matching `git_url` | Use that checkout **in place**; index it. Never clones. |
| 3 | `clone` | No mapping and no remote match | Clone (or `git_url`-less: archive-download) into the vault, then index. |
| 4 | `ambiguous` | **>1** checkout matches `git_url` | **Fail loud** — recorded in `errors`, no auto-pick. Set `repo_paths[<name>]` to disambiguate. |
| 5 | `none` | `repo_paths[<name>]` is set but the dir is missing | **Fail loud** — recorded in `errors`. Fix the mapping. |

URL matching normalizes case, `git@host:org/repo` ↔ `https://host/org/repo`, and a
trailing `.git`/`/`, matching the server's `repo_service.normalize_git_url`. The
remote scan is **depth-1 only** under each search root — it never walks `$HOME`.

### Configuration (project `.rkm/config.json`)

```json
{
  "project_id": 4,
  "repo_paths": { "apollo": "/Users/me/code/apollo" },
  "repo_search_roots": ["/Users/me/code", "/Users/me/work"]
}
```

- `repo_paths` — explicit name→path overrides (highest precedence; resolves `configured`/`none`).
- `repo_search_roots` — directories whose immediate children are scanned for a
  remote match (resolves `linked`/`ambiguous`). Omit to disable link-by-remote;
  everything then falls through to `clone`.

## Indexing

The resolved path is indexed with `gitnexus analyze .` (`mcp/gitnexus_index.py`).
Analyze is **incremental by default**, so the call both builds the first index and
refreshes an existing one. Folder-source checkouts (no `.git`) index with `--skip-git`.
After a skill writes code, `executor._maybe_reindex` refreshes the same index —
incremental on a clean tree, but a full `--force` re-parse when the checkout is
**dirty**, since incremental analyze only indexes *committed* state and would miss
the uncommitted files a code-gen skill just wrote. So symbol queries never silently
go stale mid-skill, even before you commit.

## Steps

1. Parse arguments (above) and call `rkm_pull_repos` with `{force, shallow, names}`. It:
   - Reads `~/.rkm/config.json` for platform URL + token + the bound `project_id`,
     and `.rkm/config.json` for `repo_paths` / `repo_search_roots`.
   - Fetches the repo list from `GET /api/v1/projects/<project_id>/repositories`.
   - Resolves each repo (table above), pulling/indexing or failing loud accordingly.
   - Writes `.rkm/projects/<slug>/repos/<name>/.rkm-meta.json`:
     ```json
     {
       "id": 12,
       "name": "apollo",
       "git_url": "https://github.com/org/apollo.git",
       "default_branch": "main",
       "source_type": "git",
       "last_synced_at": "2026-06-13T09:00:00Z",
       "gitnexus_indexed_commit": "abc123",
       "resolved_path": "/Users/me/code/apollo",
       "resolution_source": "configured",
       "local_indexed_commit": "9f1c2ab",
       "index_status": "indexed"
     }
     ```
     - `resolved_path` — the checkout skills must read/write (`null` for ambiguous/none).
     - `resolution_source` — `configured` | `linked` | `clone` | `ambiguous` | `none`.
     - `local_indexed_commit` — HEAD SHA that was indexed (`null` when not a git checkout).
     - `index_status` — `indexed` | `failed` | `skipped` | `unavailable` (binary missing).
   - Writes `.rkm/projects/<slug>/repos/_index.json`.

2. Display the tool's summary, e.g.:
   ```
   Repos → .rkm/projects/<slug>/repos/
     linked:   apollo (/Users/me/code/apollo, indexed)
     cloned:   apollo-infra (shallow, indexed)
     updated:  shared-libs
     skipped:  docs (up-to-date)
     errors:   billing (2 local checkouts match — set repo_paths['billing'])
   ```

## Notes

- **Linked/configured checkouts are never mutated** — `--force` only re-fetches vault
  clones. Code-gen lands in your real working tree; you commit normally (write-back
  helper is intentionally out of scope).
- Repos can be large. Use `--shallow` for review-only clone-branch workflows.
- Clone-branch auth relies on the **local** git environment (SSH keys / credential
  helper). **T5:** when a clone fails (e.g. a private repo with no local creds), the
  pull **falls back to the server archive endpoint** (`/repositories/<id>/archive`,
  PAT stays server-side) so the skill still gets code — the repo lands in
  `downloaded`, not `errors`. Only if the archive fallback also fails (e.g. the repo
  isn't indexed yet → 409) does the original clone error surface in `errors`.
  `git_url`-less (zip/folder) repos use the same archive endpoint directly.
- A missing `gitnexus` binary degrades gracefully (`index_status: "unavailable"`) —
  the pull still succeeds and files remain readable; only symbol queries are skipped.
- The server-side GitNexus index still exists — use `search_knowledge` for symbol
  search without a local checkout.
