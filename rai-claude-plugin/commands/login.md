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
4. Write `~/.rkm/config.json` (chmod 600):
   ```json
   {
     "platform_url": "http://localhost:8001",
     "token": "<raw_token>",
     "project_id": <project_id or null>,
     "project_slug": "<slug or null>"
   }
   ```
5. Print a confirmation:
   ```
   Logged in as <user_email>
   Org: <org_name>
   Project scope: <project_slug> (or "org-wide")
   Token: <token_name>, expires <expires_at or "never">
   ```
6. Remind the user: the token is stored at `~/.rkm/config.json` — never commit this file.

## Notes

- Never write the token to the project repo (`.rkm/config.json` only stores platform URL and project_id, not the token).
- If `~/.rkm/config.json` already exists, ask the user if they want to overwrite it.
