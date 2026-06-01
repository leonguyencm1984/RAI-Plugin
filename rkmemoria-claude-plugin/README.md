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

## Building a skill bundle (.skillpack)

A `.skillpack` is a renamed `.zip` file that you upload to the platform to create or update a skill.

### Required layout

```
my-skill/
├── skill.yaml      ← manifest (required)
├── SKILL.md        ← prompt / skill body (required)
├── scripts/        ← optional scripts (required for runtime: script)
│   ├── main.py
│   ├── requirements.txt
│   └── config.env.example   ← document required vars here (no real secrets)
└── README.md       ← optional, ignored by platform
```

### `skill.yaml` fields

```yaml
schema_version: 1           # must be 1
slug: my-skill              # ^[a-z0-9][a-z0-9-]*$  (URL key, immutable after first import)
name: My Skill              # display name
description: |              # shown on the skill card
  One or two sentences.
version: 1.0.0
tags: [api, documentation]
runtime: script             # prompt | script | hybrid
server_executable: false    # must always be false
variables:                  # declare config vars users must set before running
  - name: BASE_URL
    description: Backend base URL
    default: http://localhost:8080
    required: true
    secret: false
  - name: AUTH_PASSWORD
    description: Login password
    required: true
    secret: true             # stored encrypted; omit default for secrets
```

### Variable name rules

- Must match `^[A-Z][A-Z0-9_]*$` — uppercase letters, digits, underscores only.
- Must be unique within the skill.
- `secret: true` — omit `default` to avoid shipping credentials in the bundle.
- After import, project owners set values in the platform UI under the **Variables** tab.

### Build commands

**POSIX (recommended)**

```bash
cd skills/my-skill
zip -r ../my-skill.skillpack . \
  -x "scripts/.env" \
  -x "*.pyc" \
  -x "__pycache__/*" \
  -x ".DS_Store" \
  -x ".git/*"
```

**Python (cross-platform)**

```python
import zipfile, pathlib

skill_dir = pathlib.Path("skills/my-skill")
out = pathlib.Path("my-skill.skillpack")
exclude = {".env", ".pyc", ".DS_Store"}

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.name not in exclude and "__pycache__" not in f.parts:
            zf.write(f, f.relative_to(skill_dir))
```

**Verify before upload**

```bash
unzip -l my-skill.skillpack
# ✓ Must contain: skill.yaml, SKILL.md
# ✗ Must NOT contain: scripts/.env or any credential files
```

### Upload to platform

```bash
TOKEN="your-jwt-token"

# Dry-run (validate only — no DB write)
curl -X POST http://localhost:8000/api/v1/skills/import/dry-run \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@my-skill.skillpack"

# Import into a project
curl -X POST http://localhost:8000/api/v1/skills/import \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@my-skill.skillpack" \
  -F "on_conflict=replace" \
  -F "project_id=42"
```

Or use the UI: **Skills → Import** — drop the file, review the dry-run preview (shows declared variables), choose conflict policy, click **Import**.

### Security checklist before zipping

- [ ] No `scripts/.env` in the bundle (add it to the zip exclude list above)
- [ ] No API keys, passwords, or tokens anywhere in YAML, markdown, or scripts
- [ ] `config.env.example` uses placeholder values only (`AUTH_PASSWORD=`)
- [ ] Run `grep -r "password\|secret\|api_key\|token" scripts/` and review every hit

---

## Security

- Your MCPToken is stored at `~/.rkm/config.json` (chmod 600). Never in the project repo.
- Script-runtime skills execute with your user UID, no extra sandbox. Review `scripts/` before running — same trust model as `pip install` from your own org's registry.
- Token can be revoked at any time from the platform UI (Settings → MCP Tokens).
