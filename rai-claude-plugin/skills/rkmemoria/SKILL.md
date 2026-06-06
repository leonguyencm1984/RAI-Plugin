# RAI Platform Plugin Guide

You are working in a project directory that has the RAI Platform Claude Code plugin installed.

## What the plugin provides

- **`/rai:login`** — Connect to your RAI Platform instance (one-time setup per machine)
- **`/rai:status`** — See what's pulled locally vs available on the platform
- **`/rai:pull`** — Interactive picker: pull workflows, skills, or knowledge base
- **`/rai:pull-skills`**, **`/rai:pull-workflows`**, **`/rai:pull-kb`**, **`/rai:pull-repos`** — Scriptable per-category pull
- **`/rai:push-skill`** — Push a locally edited skill back to the platform
- **`/rai:run-skill`** — Execute a script or hybrid skill locally
- **`/rai:list-workflows`**, **`/rai:run-workflow`** — Browse and trigger workflows

## MCP tools available in every conversation

Once logged in, the following tools are active:
- `search_knowledge(query)` — semantic search over the project knowledge base
- `get_wiki_page(slug)` — retrieve a specific wiki page by slug
- `rkm_status()` — current pull status
- `rkm_pull_*()` — pull any category programmatically
- `rkm_push_skill()`, `rkm_run_workflow()`, `rkm_run_status()`

## Local layout (after pulling)

```
.rkm/
├── workflows/       # JSON per workflow
├── skills/          # skill bundles (skill.yaml + SKILL.md + scripts/)
├── kb/
│   ├── wiki/        # wiki pages as markdown
│   └── sources/     # source metadata JSON
└── repos/           # git clones (if /rai:pull-repos was run)
```

## Typical workflows

**Writing docs today:**
`/rai:pull-kb --wiki-only` → wiki pages available as markdown in `.rkm/kb/wiki/`

**Code review:**
`/rai:pull-repos --name <repo> --shallow` → checkout + `search_knowledge("AuthMiddleware")`

**Running a skill:**
`/rai:pull-skills --slug summarize-meeting` → `/rai:run-skill summarize-meeting --input '{"transcript": "..."}'`

**Editing and publishing a skill:**
Edit `.rkm/skills/<slug>/SKILL.md` → `/rai:push-skill <slug>`

## Getting started

If you haven't logged in yet:
```
/rai:login
```

You'll need an MCPToken from your platform profile (Settings → MCP Tokens).
