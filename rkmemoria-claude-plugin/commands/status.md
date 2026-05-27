# /rkm:status

Show what's currently pulled in `.rkm/` vs what's available on the platform.

## Steps

1. Call the `rkm_status` MCP tool.
2. Display the result as a formatted table:

```
Project: <project_slug> (<platform_url>)
Token:   scoped to project  (or "org-wide")

Workflows    <pulled>/<available>  pulled
Skills       <pulled>/<available>  pulled
Code repos   <available> connected  (not pulled — use /rkm:pull-repos)
Sources      <pulled>/<available>  pulled
Wiki pages   <pulled>/<available>  pulled
ETag cache   <etag_entries> entries
```

3. If nothing is pulled yet, suggest running `/rkm:pull`.
4. If not logged in (`~/.rkm/config.json` missing), suggest `/rkm:login`.
