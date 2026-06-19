# P0 — Antigravity feasibility gate (RAI IDE extension)

**Status: ✅ GO** — run 2026-06-19 on this machine (the one-way-door gate from the approved plan,
`/Users/mac/.claude/plans/i-want-to-upgrade-snug-sky.md`, step P0).

The gate question: *does Antigravity expose the VS Code extension APIs the thin RAI companion is
built on, before we commit to "one `.vsix`, both IDEs"?* It does.

## Host environment

| Property | Value |
|---|---|
| `appName` | **Antigravity** |
| `vscode.version` (API baseline) | **1.107.0** (extension requires `^1.85.0` — satisfied) |
| `appHost` / `uiKind` | `desktop` / `Desktop` |
| `env.machineId` | present → per-device MCP token naming (`rai-ide-<machineId8>`) works |
| `env.openExternal` | available → deep-links to the web app work |
| Extensions dir | `~/.antigravity/extensions` |
| Sideload CLI | `/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity --install-extension <vsix> --force` |

Antigravity is a VS Code 1.107 fork (`dataFolderName .antigravity`, MIT cli wrapper, full
Extension Marketplace-style host; already runs `anthropic.claude-code` + 66 other extensions).

## API probes (self-contained `rai-antigravity-spike` .vsix, no backend)

Auto-run on activation, results written to `~/.rai-antigravity-spike/report.json`:

| Required API | Why the RAI extension needs it | Result |
|---|---|---|
| **Tree view** (`registerTreeDataProvider` + activity-bar container) | Projects + Sync/Drift views | ✅ registered |
| **SecretStorage** (`store`/`get`/`delete`) | MCP token + refresh token at rest | ✅ round-trip + clean delete |
| **Webview + messaging** (CSP nonce, host↔webview `postMessage`) | Login form + onboarding consent/diff | ✅ ready + nonce echo matched |

**Verdict in report: `GO`.**

## Real-extension activation proof

Beyond the probe, the **actual** `rikkeisoft.rai-ide@0.1.0` `.vsix` was sideloaded and activated
cleanly in the same Antigravity host:

```
22:06:58  ExtensionService#_doActivateExtension rikkeisoft.rai-antigravity-spike  onStartupFinished
22:08:07  ExtensionService#_doActivateExtension rikkeisoft.rai-ide               onStartupFinished
```

No `[error]`/`[warning]`/unhandled-rejection lines reference either extension in the extension-host log.

## On "repo-source + indexing reach the client"

This sub-question of P0 was a **design** conclusion, not a client-API test, and was already resolved
in the plan: gitnexus is **not `npx`-able**, so indexing stays **server-side** and the client pulls
**archive snapshots** via `GET /api/v1/repositories/{id}/archive` (zip, `.git`-less). Nothing about
that contract is Antigravity-specific — it is plain HTTPS from the extension host (Node `fetch`),
which the probe's environment confirms is the standard extension-host runtime. No blocker.

## Conclusion

All load-bearing host APIs are present at parity in Antigravity. **One `.vsix` serves both VS Code
and Antigravity** as planned — no fork, no fallback path needed. P0 gate cleared; v1 (P1–P5 + P7,
already implemented) is unblocked for release.

Per the eng review (T1), each release still runs **automated `@vscode/test-electron` on VS Code +
the manual Antigravity smoke checklist** below.

---

## Manual Antigravity smoke checklist (run each release)

Reinstall: `antigravity --install-extension rai-ide-<ver>.vsix --force`, then reload window.

1. [ ] RAI activity-bar icon appears; **Projects** + **Sync Status** views render.
2. [ ] `RAI: Sign in` opens the login webview; submit reaches the host (network call fires).
3. [ ] After sign-in, MCP token is persisted (survives reload; visible only via SecretStorage).
4. [ ] Project bind → Projects view shows the bound project; `RAI: Open in web app` deep-links.
5. [ ] `RAI: Pull all` writes `.rkm/` KB·skills·workflows; Sync Status shows drift states.
6. [ ] `RAI: Pull repository` extracts an archive into the workspace.
7. [ ] `RAI: Apply repository onboarding` shows the diff/consent prompt; default SKIP; `.rai-bak` on overwrite.
8. [ ] `RAI: Sign out` clears credentials (reload → signed-out).
