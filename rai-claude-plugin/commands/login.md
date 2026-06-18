# /rai:login

Log in to your RAI Platform instance.

## Configuration

```
PLATFORM_URL = http://localhost:8001
```

The platform URL is pre-configured. Do **not** ask the user for it.

## Steps

1. Ask the user for their MCPToken (created in their profile under Settings → MCP Tokens).
2. Call `GET http://localhost:8001/api/v1/mcp/whoami` with `Authorization: Bearer <token>`.
3. If the response is 401, tell the user the token is invalid and stop.
4. **Account-switch guard:** if `~/.rkm/config.json` already contains a `user_email` different from the one whoami just returned, AND a workspace `.rkm/` vault exists, warn the user:
   > "This machine's local vault was pulled by <old user_email>. Logging in as <new user_email> on top of it can mix data between accounts (stale etag cache, other tenant's KB/skills). Run `/rai:logout` and choose Archive first, or continue at your own risk."
   Let the user decide before continuing.
5. **Bind the token to a project if it is unbound (T4).** Project-scoped operations
   (`pull-repos`, `pull-kb`, `pull-skills`, `pull-workflows`, all `push-*`, and
   `run-skill` on project items) hard-enforce that the MCP token is bound to a
   project — an unbound token is rejected with **403**.
   - If whoami returned a **non-null** `project_id`, the token is already bound — skip
     to step 6 using that `project_id`/`project_slug`.
   - If whoami returned a **null** `project_id`, the token is unbound. Tell the user:
     > "This token isn't bound to a project. Project-scoped commands (pull-repos,
     > pull-kb/skills/workflows, push-*, run-skill) will be rejected until it's bound."

     Then ask the user which project to bind to and bind it:
     1. Ask for the project **slug** (or numeric **id**). An unbound token cannot list
        projects, so the user must supply it — they can find it in the web UI on the
        project page, or as `<slug>` in an existing `.rkm/projects/<slug>/` vault path.
     2. Call `POST http://localhost:8001/api/v1/mcp/bind-project` with
        `Authorization: Bearer <token>` and JSON body `{"project_slug": "<slug>"}`
        (or `{"project_id": <id>}`).
     3. On **200**: the response has the same shape as whoami — use its `project_id`
        and `project_slug` as the bound scope from here on.
     4. On **404** ("Project not found"): the slug/id isn't in your org — re-ask.
     5. On **409** ("Project is archived"): pick a different project.
     6. If the user declines to bind, continue, but warn that project-scoped MCP calls
        will 403 until they bind (re-run `/rai:login`). Leave `project_id`/`project_slug`
        null in the config.
6. Write `~/.rkm/config.json` (chmod 600), using the bound scope from step 5 (or the
   whoami scope if already bound / left unbound):
   ```json
   {
     "platform_url": "http://localhost:8001",
     "token": "<raw_token>",
     "user_email": "<user_email from whoami>",
     "project_id": <project_id or null>,
     "project_slug": "<slug or null>"
   }
   ```
7. Print a confirmation:
   ```
   Logged in as <user_email>
   Org: <org_name>
   Project scope: <project_slug> (or "org-wide")
   Token: <token_name>, expires <expires_at or "never">
   ```
8. Remind the user: the token is stored at `~/.rkm/config.json` — never commit this file. To sign out (or switch accounts), run `/rai:logout`.

## Notes

- Never write the token to the project repo (`.rkm/config.json` only stores platform URL and project_id, not the token).
- If `~/.rkm/config.json` already exists, ask the user if they want to overwrite it.
