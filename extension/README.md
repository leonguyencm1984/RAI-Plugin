# RAI — IDE companion (VS Code & Antigravity)

Thin companion extension for the **RAI Platform**. It signs you in, binds your
device to a project, and handles local-only operations — pull/push of
KB·skills·workflows, pulling repositories, and applying per-repo onboarding so a
pulled repo "just works". All authoring and browsing stays in the RAI web app;
this extension deep-links to it rather than rebuilding it.

> Private distribution only — the signed `.vsix` is hosted by the RAI Platform,
> not the public Marketplace / Open VSX.

## Features

- **Sign in** with your RAI account; a single per-device MCP token is minted (or
  reused) and bound to a project. Credentials never leave the sign-in webview.
- **Sync Status tree** — see Skills, Workflows, and Knowledge Base across your
  org and project scopes, each marked *in sync* / *update available* / *not
  pulled*.
- **Pull all** KB·skills·workflows into a local `.rkm/` vault (layout shared with
  the RKMemoria Claude plugin).
- **Push skills** back to the platform (select which).
- **Pull a repository** as a `.git`-less snapshot, then optionally apply its
  onboarding.
- **Apply onboarding** — write `CLAUDE.md`, `AGENTS.md`, `.claude/` and
  `.vscode/` config resolved by the platform. Existing files are never
  clobbered silently: conflicts prompt with a default of *Skip*, and any
  overwrite keeps a `.rai-bak` backup.

## Commands

| Command | What it does |
| --- | --- |
| `RAI: Sign in` / `RAI: Sign out` | Authenticate / revoke the device token |
| `RAI: Pull all (KB, skills, workflows)` | Pull every resource into the vault |
| `RAI: Push skills` | Push selected local skills |
| `RAI: Pull repository` | Download a repo snapshot (offers to apply onboarding) |
| `RAI: Apply repository onboarding` | Apply a repo's onboarding into the workspace |
| `RAI: Open in web app` | Deep-link to the current project |
| `RAI: Refresh` | Re-read session + sync status |

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `rai.apiBaseUrl` | `http://localhost:8000` | RAI Platform REST API base URL |
| `rai.webBaseUrl` | `http://localhost:4000` | RAI web app base URL (deep-links) |
| `rai.vaultPath` | `.rkm` | Workspace-relative local vault directory |

## Development

```bash
npm install
npm run compile      # tsc --noEmit (type check)
npm run build        # esbuild bundle -> dist/extension.js
npm run watch        # rebuild on change
npm run package      # produce a .vsix (vsce package --no-dependencies)
```

Press `F5` in VS Code to launch an Extension Development Host. The extension
host runs in Node, so REST calls are made server-side (no CORS); the MCP token
and refresh token live in `SecretStorage`.

## Requirements

- VS Code `^1.85.0` (or Antigravity).
- A reachable RAI Platform instance and a valid account.
