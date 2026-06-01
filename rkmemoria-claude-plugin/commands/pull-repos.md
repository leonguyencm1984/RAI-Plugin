# /rai:pull-repos

Clone or update git repositories to `.rkm/repos/`.

## Usage

```
/rai:pull-repos [--all] [--name <name>...] [--shallow] [--force]
```

## Steps

1. Parse arguments:
   - `--all` → pull all connected repos
   - `--name apollo` → pull only the repo named "apollo"
   - `--shallow` → `git clone --depth 1`
   - `--force` → re-clone even if already present

2. Load `~/.rkm/config.json` for platform URL + token.

3. Fetch the repo list from `GET /api/v1/projects/<project_id>/repositories`.

4. For each selected repo:
   - If `.rkm/repos/<name>/` does not exist → `git clone <git_url> .rkm/repos/<name>/`
   - If it exists and not `--force` → `git -C .rkm/repos/<name>/ fetch && git -C .rkm/repos/<name>/ reset --hard origin/<default_branch>`
   - Write `.rkm/repos/<name>/.rkm-meta.json`:
     ```json
     {
       "name": "<name>",
       "git_url": "<url>",
       "default_branch": "<branch>",
       "last_synced_at": "<ISO timestamp>",
       "gitnexus_indexed_commit": "<sha or null>"
     }
     ```

5. Display summary:
   ```
   Repos: apollo cloned (shallow), apollo-infra skipped (already up-to-date)
   Written to .rkm/repos/
   ```

## Notes

- Repos can be large. Use `--shallow` for code review workflows where you only need recent history.
- PAT authentication is handled server-side (the platform clones for syncing). For plugin pulls, git must have credentials configured locally (SSH keys, credential helper, etc.) or the repo must be public.
- GitNexus symbol index is on the server — use `search_knowledge` MCP tool for symbol search without cloning.
