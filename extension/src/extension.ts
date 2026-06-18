import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import { Session } from './session';
import { ProjectsTreeProvider } from './projectsTree';
import { StatusTreeProvider } from './statusTree';
import { SyncManager } from './sync';
import { Vault } from './vault';
import { RepoManager, Repository } from './repos';
import { OnboardingApplier } from './onboarding';
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
  const repoMgr = new RepoManager(api);
  const onboarding = new OnboardingApplier(api);
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
    vscode.commands.registerCommand('rai.pullRepos', () => pullRepos(session, repoMgr, onboarding)),
    vscode.commands.registerCommand('rai.applyOnboarding', () => applyOnboarding(session, repoMgr, onboarding)),
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

/** Require a signed-in session bound to a project; returns the projectId or undefined (with a warning). */
function requireProject(session: Session): number | undefined {
  if (!session.state.signedIn) {
    void vscode.window.showWarningMessage('RAI: sign in first.');
    return undefined;
  }
  const id = session.state.projectId;
  if (id === undefined) {
    void vscode.window.showWarningMessage('RAI: this device is not bound to a project.');
    return undefined;
  }
  return id;
}

async function pickRepo(session: Session, repoMgr: RepoManager): Promise<Repository | undefined> {
  const projectId = requireProject(session);
  if (projectId === undefined) {
    return undefined;
  }
  const repos = await repoMgr.listRepos(projectId);
  if (repos.length === 0) {
    void vscode.window.showInformationMessage('RAI: this project has no repositories.');
    return undefined;
  }
  const pick = await vscode.window.showQuickPick(
    repos.map((r) => ({
      label: r.name,
      description: r.gitnexus_indexed_commit ? 'indexed' : `not indexed · ${r.last_sync_status}`,
      repo: r,
    })),
    { placeHolder: 'Select a repository' },
  );
  return pick?.repo;
}

async function pullRepos(session: Session, repoMgr: RepoManager, onboarding: OnboardingApplier): Promise<void> {
  const repo = await pickRepo(session, repoMgr).catch((e) => {
    void vscode.window.showErrorMessage(`RAI: ${(e as Error).message}`);
    return undefined;
  });
  if (!repo) {
    return;
  }
  const parent = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!parent) {
    void vscode.window.showWarningMessage('RAI: open a folder to pull into.');
    return;
  }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `RAI: pulling "${repo.name}"…` },
    async () => {
      try {
        const outcome = await repoMgr.pullRepo(repo, parent);
        if (outcome.status === 'sync-triggered') {
          void vscode.window.showInformationMessage('RAI: sync enqueued. Pull again once it succeeds.');
          return;
        }
        if (outcome.status === 'cancelled') {
          return;
        }
        const apply = await vscode.window.showInformationMessage(
          `RAI: extracted ${outcome.files} files. Apply onboarding now?`,
          'Apply onboarding',
        );
        if (apply === 'Apply onboarding') {
          await runApply(onboarding, repo.id, outcome.dir);
        }
      } catch (err) {
        void vscode.window.showErrorMessage(`RAI: pull failed — ${(err as Error).message}`);
      }
    },
  );
}

async function applyOnboarding(session: Session, repoMgr: RepoManager, onboarding: OnboardingApplier): Promise<void> {
  const repo = await pickRepo(session, repoMgr).catch((e) => {
    void vscode.window.showErrorMessage(`RAI: ${(e as Error).message}`);
    return undefined;
  });
  if (!repo) {
    return;
  }
  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!root) {
    void vscode.window.showWarningMessage('RAI: open a folder to apply onboarding into.');
    return;
  }
  await runApply(onboarding, repo.id, root);
}

async function runApply(onboarding: OnboardingApplier, repoId: number, dir: vscode.Uri): Promise<void> {
  try {
    const r = await onboarding.apply(repoId, dir);
    if (r) {
      void vscode.window.showInformationMessage(
        `RAI: onboarding applied — ${r.written} new, ${r.overwritten} overwritten, ${r.skipped} skipped, ${r.unchanged} unchanged.`,
      );
    }
  } catch (err) {
    void vscode.window.showErrorMessage(`RAI: onboarding failed — ${(err as Error).message}`);
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
