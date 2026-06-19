// RAI Antigravity P0 feasibility spike.
//
// Self-contained probe (no deps, no backend). On activation it exercises the four
// host APIs the real RAI extension depends on and writes a machine-readable report
// to ~/.rai-antigravity-spike/report.json so the result can be inspected from a
// terminal without eyeballing the IDE UI.
//
//   1. Tree view          — registerTreeDataProvider + activity-bar container
//   2. SecretStorage       — store / get / delete round-trip (token storage)
//   3. Webview + messaging — host -> webview -> host postMessage round-trip + CSP nonce
//   4. Environment         — version, appName, appHost, uiKind, machineId, remoteName

const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPORT_DIR = path.join(os.homedir(), ".rai-antigravity-spike");
const REPORT_FILE = path.join(REPORT_DIR, "report.json");

let lastReport = null;
let treeProvider = null;

function nonce() {
  // Date.now/Math.random are fine inside the extension host (not the workflow sandbox).
  let t = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 24; i++) t += chars.charAt(Math.floor(Math.random() * chars.length));
  return t;
}

function captureEnv() {
  return {
    vscodeVersion: vscode.version,
    appName: vscode.env.appName,
    appHost: vscode.env.appHost,
    uiKind: vscode.env.uiKind === vscode.UIKind.Desktop ? "Desktop" : String(vscode.env.uiKind),
    language: vscode.env.language,
    machineId: vscode.env.machineId ? vscode.env.machineId.slice(0, 8) + "…" : null,
    sessionIdPresent: Boolean(vscode.env.sessionId),
    remoteName: vscode.env.remoteName || null,
    openExternalAvailable: typeof vscode.env.openExternal === "function",
  };
}

async function probeSecretStorage(context) {
  const key = "raiSpike.secretProbe";
  const value = "secret-" + nonce();
  try {
    await context.secrets.store(key, value);
    const readBack = await context.secrets.get(key);
    const roundTrip = readBack === value;
    await context.secrets.delete(key);
    const afterDelete = await context.secrets.get(key);
    return {
      ok: roundTrip && afterDelete === undefined,
      roundTrip,
      deletedCleanly: afterDelete === undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

function probeWebview(context) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result, panel) => {
      if (settled) return;
      settled = true;
      try { if (panel) panel.dispose(); } catch (_) {}
      resolve(result);
    };
    try {
      const panel = vscode.window.createWebviewPanel(
        "raiSpikeWebview",
        "RAI Spike — webview probe",
        vscode.ViewColumn.Active,
        { enableScripts: true, retainContextWhenHidden: false }
      );
      const n = nonce();
      panel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; style-src 'nonce-${n}'; script-src 'nonce-${n}';" />
<style nonce="${n}">body{font-family:sans-serif;padding:1rem;color:var(--vscode-foreground)}</style>
</head>
<body>
<h3>RAI P0 webview probe</h3>
<p id="status">initialising…</p>
<script nonce="${n}">
  const vscodeApi = acquireVsCodeApi();
  window.addEventListener('message', (event) => {
    const msg = event.data;
    if (msg && msg.type === 'ping') {
      document.getElementById('status').textContent = 'received ping, replying pong';
      vscodeApi.postMessage({ type: 'pong', echo: msg.token });
    }
  });
  vscodeApi.postMessage({ type: 'ready' });
</script>
</body>
</html>`;

      const token = nonce();
      let gotReady = false;
      const sub = panel.webview.onDidReceiveMessage((msg) => {
        if (msg && msg.type === "ready") {
          gotReady = true;
          panel.webview.postMessage({ type: "ping", token });
        } else if (msg && msg.type === "pong") {
          sub.dispose();
          finish(
            { ok: msg.echo === token, gotReady, echoMatched: msg.echo === token, cspNonceUsed: true },
            panel
          );
        }
      });

      // Fallback so a non-responding webview can't hang the probe.
      setTimeout(() => {
        finish(
          { ok: false, gotReady, error: "timeout waiting for webview pong (5s)" },
          panel
        );
      }, 5000);
    } catch (e) {
      finish({ ok: false, error: String(e && e.message ? e.message : e) });
    }
  });
}

function probeTreeView(context) {
  try {
    treeProvider = new SpikeTreeProvider();
    const disposable = vscode.window.registerTreeDataProvider("raiSpike.results", treeProvider);
    context.subscriptions.push(disposable);
    return { ok: true, registered: true };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

class SpikeTreeProvider {
  constructor() {
    this._emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._emitter.event;
  }
  refresh() { this._emitter.fire(); }
  getTreeItem(item) { return item; }
  getChildren() {
    const r = lastReport;
    if (!r) {
      return [new vscode.TreeItem("Run probes (beaker icon) to populate…")];
    }
    const row = (label, res) => {
      const it = new vscode.TreeItem(`${res && res.ok ? "✓" : "✗"} ${label}`);
      it.description = res && res.ok ? "PASS" : (res && res.error ? res.error : "FAIL");
      return it;
    };
    const items = [
      row("Tree view", r.checks.treeView),
      row("SecretStorage", r.checks.secretStorage),
      row("Webview + messaging", r.checks.webview),
    ];
    const env = r.env || {};
    const envItem = new vscode.TreeItem(`Host: ${env.appName} (VS Code ${env.vscodeVersion})`);
    envItem.description = `${env.appHost || "?"} · ${env.uiKind || "?"}`;
    items.push(envItem);
    const verdict = new vscode.TreeItem(r.verdict === "GO" ? "VERDICT: GO ✅" : "VERDICT: NO-GO ❌");
    items.push(verdict);
    return items;
  }
}

async function runProbes(context) {
  const env = captureEnv();
  const treeView = probeTreeView(context);
  const secretStorage = await probeSecretStorage(context);
  const webview = await probeWebview(context);

  const checks = { treeView, secretStorage, webview };
  const allOk = treeView.ok && secretStorage.ok && webview.ok;

  const report = {
    spike: "RAI IDE extension — P0 Antigravity feasibility",
    timestampMs: Date.now(),
    timestamp: new Date().toISOString(),
    env,
    checks,
    verdict: allOk ? "GO" : "NO-GO",
  };
  lastReport = report;
  if (treeProvider) treeProvider.refresh();

  try {
    fs.mkdirSync(REPORT_DIR, { recursive: true });
    fs.writeFileSync(REPORT_FILE, JSON.stringify(report, null, 2), "utf8");
  } catch (e) {
    // Non-fatal: report still available via the command/tree.
  }
  return report;
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("raiSpike.run", async () => {
      const r = await runProbes(context);
      vscode.window.showInformationMessage(
        `RAI P0 spike: ${r.verdict} — tree:${r.checks.treeView.ok ? "✓" : "✗"} ` +
          `secrets:${r.checks.secretStorage.ok ? "✓" : "✗"} webview:${r.checks.webview.ok ? "✓" : "✗"} ` +
          `(${r.env.appName} ${r.env.vscodeVersion})`
      );
    }),
    vscode.commands.registerCommand("raiSpike.showReport", async () => {
      const doc = await vscode.workspace.openTextDocument({
        language: "json",
        content: JSON.stringify(lastReport || { note: "no report yet — run raiSpike.run" }, null, 2),
      });
      await vscode.window.showTextDocument(doc);
    })
  );

  // Auto-run once on startup so the report file exists without manual interaction.
  runProbes(context).catch(() => {});
}

function deactivate() {}

module.exports = { activate, deactivate };
