import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import { Session } from './session';
import { ProjectsTreeProvider } from './projectsTree';
import { StatusTreeProvider } from './statusTree';
import { SyncManager } from './sync';
import { Vault } from './vault';
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
  const syncMgr = new SyncManager(api, session);
  const tree = new ProjectsTreeProvider(session);
  const statusTree = new StatusTreeProvider(session, syncMgr);

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);

  const sync = () => {
    const { signedIn, email, projectSlug } = session.state;
    void vscode.commands.executeCommand('setContext', 'rai.signedIn', signedIn);
    if (signedIn) {
      const who = projectSlug ?? email ?? 'signed in';
      statusBar.text = `$(rocket) RAI: ${who}`;
      statusBar.tooltip = `RAI — ${email ?? ''}${projectSlug ? ` · ${projectSlug}` : ''}`;
      statusBar.command = 'rai.openInWebApp';
    } else {
      statusBar.text = '$(sign-in) RAI: sign in';
      statusBar.tooltip = 'Sign in to RAI';
      statusBar.command = 'rai.login';
    }
    statusBar.show();
    tree.refresh();
    statusTree.refresh();
  };

  context.subscriptions.push(
    statusBar,
    vscode.window.registerTreeDataProvider('rai.projects', tree),
    vscode.window.registerTreeDataProvider('rai.status', statusTree),
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
    vscode.commands.registerCommand('rai.pullAll', () => pullAll(session, syncMgr, statusTree)),
    vscode.commands.registerCommand('rai.push', () => pushSkills(session, syncMgr)),
  );

  sync();
}

export function deactivate(): void {
  // Nothing to tear down — all disposables live on context.subscriptions.
}

/** Guard shared by pull/push: require a signed-in session and an open-folder vault. */
function requireVault(session: Session): Vault | undefined {
  if (!session.state.signedIn) {
    void vscode.window.showWarningMessage('RAI: sign in first.');
    return undefined;
  }
  const vault = Vault.resolve(session.state.projectSlug);
  if (!vault) {
    void vscode.window.showWarningMessage('RAI: open a folder to use the vault.');
    return undefined;
  }
  return vault;
}

async function pullAll(session: Session, sync: SyncManager, statusTree: StatusTreeProvider): Promise<void> {
  const vault = requireVault(session);
  if (!vault) {
    return;
  }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'RAI: pulling KB, skills, workflows…' },
    async () => {
      try {
        const r = await sync.pullAll(vault);
        statusTree.refresh();
        void vscode.window.showInformationMessage(
          `RAI: pulled ${r.pulled}, skipped ${r.skipped}${r.errors ? `, ${r.errors} error(s)` : ''}.`,
        );
      } catch (err) {
        void vscode.window.showErrorMessage(`RAI: pull failed — ${(err as Error).message}`);
      }
    },
  );
}

async function pushSkills(session: Session, sync: SyncManager): Promise<void> {
  const vault = requireVault(session);
  if (!vault) {
    return;
  }
  try {
    const r = await sync.pushSkills(vault);
    if (r) {
      void vscode.window.showInformationMessage(
        `RAI: pushed ${r.pushed} skill(s)${r.errors ? `, ${r.errors} error(s)` : ''}.`,
      );
    }
  } catch (err) {
    void vscode.window.showErrorMessage(`RAI: push failed — ${(err as Error).message}`);
  }
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
