# RKMemoria Claude Code Plugin

Brings your RKMemoria knowledge base, skills, workflows, and code graph into Claude Code.

## Install

```bash
# Option A: Claude Code plugin install (when marketplace support lands)
claude-code plugin install https://github.com/rikkeisoft/rkmemoria-claude-plugin

# Option B: Manual symlink
ln -s /Users/mac/Documents/Projects/RKMemoria/rkmemoria-claude-plugin ~/.claude/plugins/rkmemoria
```

## Requirements

- Python 3.11+
- `pip install httpx mcp pyyaml`
- A running RKMemoria platform instance
- An MCPToken (create one in your profile: Settings → MCP Tokens)

## Quick start

```
/rkm:login         # one-time setup — enter platform URL + MCPToken
/rkm:status        # see what's available
/rkm:pull          # interactive picker
```

## Commands

| Command                 | Purpose                               |
| ----------------------- | ------------------------------------- |
| `/rkm:login`          | Authenticate with your platform       |
| `/rkm:status`         | Show pull status vs platform          |
| `/rkm:pull`           | Interactive category picker           |
| `/rkm:pull-workflows` | Pull workflows (scriptable)           |
| `/rkm:pull-skills`    | Pull skill bundles (scriptable)       |
| `/rkm:pull-repos`     | Clone repositories (scriptable)       |
| `/rkm:pull-kb`        | Pull wiki + sources (scriptable)      |
| `/rkm:push-skill`     | Push local skill edits back           |
| `/rkm:run-skill`      | Execute a script/hybrid skill locally |
| `/rkm:list-workflows` | List available workflows              |
| `/rkm:run-workflow`   | Run a workflow by ID                  |

## Security

- Your MCPToken is stored at `~/.rkm/config.json` (chmod 600). Never in the project repo.
- Script-runtime skills execute with your user UID, no extra sandbox. Review `scripts/` before running — same trust model as `pip install` from your own org's registry.
- Token can be revoked at any time from the platform UI (Settings → MCP Tokens).
