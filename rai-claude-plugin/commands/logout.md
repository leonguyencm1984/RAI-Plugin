# /rai:logout

Log out from the RAI Platform. Clears the saved token so you can log in again — including with a different account — and optionally archives or deletes the local `.rkm/` resources (KB wiki, sources, skills, workflows, etag cache).

## Steps

1. Call `rkm_status` and show the user a one-line summary of what exists locally (project slug, wiki/sources/skills counts). If it errors with an auth problem, note that the token may already be invalid and continue.

2. Ask the user what to do with the local `.rkm/` vault (use the `AskUserQuestion` tool, single-select):

   | Option | Meaning |
   |---|---|
   | **Keep local resources** (default) | Sign out only. The vault stays; right choice when re-logging into the same account. |
   | **Archive** (recommended when switching accounts) | The whole `.rkm/` folder is renamed to `.rkm-archive-<timestamp>` — nothing is lost, and the new account starts with a clean vault on its first pull. |
   | **Delete permanently** | Removes the vault contents (org/, projects/, kb/, runs/, INDEX.md, etag cache, .obsidian). Un-pushed local edits and skill `scripts/.env` files are lost. |

   Why this matters when switching accounts: the etag cache and pulled KB/skills belong to the old account — keeping them under a new login can silently skip pushes/pulls and mix data between tenants.

3. If the user chose **Delete permanently**, ask one explicit confirmation question first ("This is irreversible — un-pushed wiki edits and skill .env files will be lost. Proceed?"). Do not proceed without a clear yes.

4. Call `rkm_logout` with:
   - Keep → `{ "purge": "keep" }`
   - Archive → `{ "purge": "archive" }`
   - Delete → `{ "purge": "delete", "confirm": true }`

5. Print the result:
   ```
   Logged out <user_email if reported>.
   Vault: kept | archived to <path> | deleted (<entries>)
   Token cleared from ~/.rkm/config.json (platform URL kept).
   ```

6. Remind the user:
   - The platform-side token was **not** revoked — to fully invalidate it, delete it in the web UI under **Settings → MCP Tokens**.
   - Run `/rai:login` to log in again with any account.
   - If they archived: the old vault can be restored by renaming `.rkm-archive-<timestamp>` back to `.rkm` after logging back into the original account.

## Notes

- `rkm_logout` never touches archives from previous logouts.
- Logging out does not affect anything on the platform — only local credentials and (optionally) local copies.
