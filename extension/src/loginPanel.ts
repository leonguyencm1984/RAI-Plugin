import * as vscode from 'vscode';
import { Session } from './session';
import { ApiError } from './apiClient';

/** Message posted from the webview form to the extension host. */
interface SubmitMessage {
  type: 'submit';
  email: string;
  password: string;
}

/**
 * A single-instance webview panel hosting the sign-in form. Credentials are
 * collected in the webview and posted to the extension host, where {@link
 * Session.signIn} performs the actual auth (login → mint/reuse MCP token →
 * bind project). The password never touches globalState — it lives only for
 * the duration of the postMessage. On success the panel disposes and the
 * supplied onSignedIn callback refreshes the rest of the UI.
 */
export class LoginPanel {
  private static current: LoginPanel | undefined;
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];

  static show(
    extensionUri: vscode.Uri,
    session: Session,
    onSignedIn: () => void,
  ): void {
    if (LoginPanel.current) {
      LoginPanel.current.panel.reveal(vscode.ViewColumn.Active);
      return;
    }
    LoginPanel.current = new LoginPanel(extensionUri, session, onSignedIn);
  }

  private constructor(
    extensionUri: vscode.Uri,
    private readonly session: Session,
    private readonly onSignedIn: () => void,
  ) {
    this.panel = vscode.window.createWebviewPanel(
      'rai.login',
      'Sign in to RAI',
      vscode.ViewColumn.Active,
      { enableScripts: true, localResourceRoots: [extensionUri], retainContextWhenHidden: true },
    );
    this.panel.webview.html = this.html();
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (msg: SubmitMessage) => this.onMessage(msg),
      null,
      this.disposables,
    );
  }

  private async onMessage(msg: SubmitMessage): Promise<void> {
    if (msg.type !== 'submit') {
      return;
    }
    if (!msg.email || !msg.password) {
      this.post({ type: 'error', message: 'Email and password are required.' });
      return;
    }
    this.post({ type: 'busy' });
    try {
      await this.session.signIn(msg.email, msg.password);
      this.onSignedIn();
      void vscode.window.showInformationMessage('RAI: signed in.');
      this.panel.dispose();
    } catch (err) {
      const message = err instanceof ApiError
        ? err.message
        : `Unexpected error: ${(err as Error).message}`;
      this.post({ type: 'error', message });
    }
  }

  private post(message: unknown): void {
    void this.panel.webview.postMessage(message);
  }

  private dispose(): void {
    LoginPanel.current = undefined;
    this.panel.dispose();
    while (this.disposables.length) {
      this.disposables.pop()?.dispose();
    }
  }

  private html(): string {
    const nonce = makeNonce();
    const csp = [
      `default-src 'none'`,
      `style-src 'nonce-${nonce}'`,
      `script-src 'nonce-${nonce}'`,
    ].join('; ');

    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sign in to RAI</title>
  <style nonce="${nonce}">
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 1.5rem; }
    form { display: flex; flex-direction: column; gap: 0.75rem; max-width: 22rem; }
    label { font-size: 0.85rem; }
    input {
      padding: 0.5rem; border: 1px solid var(--vscode-input-border, transparent);
      background: var(--vscode-input-background); color: var(--vscode-input-foreground);
      border-radius: 2px;
    }
    button {
      padding: 0.5rem; border: none; cursor: pointer; border-radius: 2px;
      background: var(--vscode-button-background); color: var(--vscode-button-foreground);
    }
    button:hover { background: var(--vscode-button-hoverBackground); }
    button:disabled { opacity: 0.6; cursor: default; }
    .error { color: var(--vscode-errorForeground); font-size: 0.85rem; min-height: 1rem; }
  </style>
</head>
<body>
  <h2>Sign in to RAI</h2>
  <form id="login">
    <label for="email">Email</label>
    <input id="email" type="email" autocomplete="username" required />
    <label for="password">Password</label>
    <input id="password" type="password" autocomplete="current-password" required />
    <button id="submit" type="submit">Sign in</button>
    <div class="error" id="error" role="alert"></div>
  </form>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const form = document.getElementById('login');
    const submit = document.getElementById('submit');
    const errorEl = document.getElementById('error');

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      errorEl.textContent = '';
      vscode.postMessage({
        type: 'submit',
        email: document.getElementById('email').value.trim(),
        password: document.getElementById('password').value,
      });
    });

    window.addEventListener('message', (event) => {
      const msg = event.data;
      if (msg.type === 'busy') {
        submit.disabled = true;
        submit.textContent = 'Signing in…';
      } else if (msg.type === 'error') {
        submit.disabled = false;
        submit.textContent = 'Sign in';
        errorEl.textContent = msg.message;
      }
    });
  </script>
</body>
</html>`;
  }
}

function makeNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let text = '';
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}
