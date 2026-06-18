import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import { Session } from './session';
import { ProjectsTreeProvider } from './projectsTree';
import { LoginPanel } from './loginPanel';

/**
 * Composition root. Builds the ApiClient → Session → tree/status-bar graph,
 * registers the four contributed commands, and keeps the `rai.signedIn`
 * context key (which drives menu visibility in package.json) in sync with the
 * session. All UI refreshes funnel through {@link sync} so a single call after
 * any state change updates the tree, status bar, and context key together.
 */
export function activate(context: vscode.ExtensionContext): void {
  const api = new ApiClient(context.secrets);
  const session = new Session(context, api);
  const tree = new ProjectsTreeProvider(session);

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = 'rai.openInWebApp';

  const sync = () => {
    const { signedIn, email, projectSlug } = session.state;
    void vscode.commands.executeCommand('setContext', 'rai.signedIn', signedIn);
    if (signedIn) {
      statusBar.text = `$(rocket) RAI: ${projectSlug ?? email ?? 'signed in'}`;
      statusBar.tooltip = `RAI — ${email ?? ''}${projectSlug ? ` · ${projectSlug}` : ''}`;
    } else {
      statusBar.text = '$(sign-in) RAI: sign in';
      statusBar.tooltip = 'Sign in to RAI';
      statusBar.command = 'rai.login';
    }
    statusBar.show();
    tree.refresh();
  };

  context.subscriptions.push(
    statusBar,
    vscode.window.registerTreeDataProvider('rai.projects', tree),
    vscode.commands.registerCommand('rai.login', () => {
      LoginPanel.show(context.extensionUri, session, sync);
    }),
    vscode.commands.registerCommand('rai.logout', async () => {
      await session.signOut();
      sync();
      void vscode.window.showInformationMessage('RAI: signed out.');
    }),
    vscode.commands.registerCommand('rai.refresh', () => sync()),
    vscode.commands.registerCommand('rai.openInWebApp', () => openInWebApp(session)),
  );

  sync();
}

export function deactivate(): void {
  // Nothing to tear down — all disposables live on context.subscriptions.
}

function openInWebApp(session: Session): void {
  const { projectSlug } = session.state;
  const base = vscode.workspace
    .getConfiguration('rai')
    .get<string>('webBaseUrl', 'http://localhost:4000')
    .replace(/\/+$/, '');
  const url = projectSlug ? `${base}/projects/${projectSlug}` : base;
  void vscode.env.openExternal(vscode.Uri.parse(url));
}
