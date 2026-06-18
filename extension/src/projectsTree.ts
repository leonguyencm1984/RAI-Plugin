import * as vscode from 'vscode';
import { Session } from './session';

/**
 * A flat tree that mirrors the current {@link Session} state. Signed out, it
 * offers a single actionable "Sign in" row; signed in, it shows the bound
 * account and project plus an "Open in web app" deep-link. The view is pure
 * presentation — every mutation goes through commands wired in extension.ts,
 * which call {@link refresh} afterwards.
 */
export class ProjectsTreeProvider implements vscode.TreeDataProvider<RaiTreeItem> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(private readonly session: Session) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: RaiTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(): RaiTreeItem[] {
    const { signedIn, email, projectSlug } = this.session.state;

    if (!signedIn) {
      return [
        new RaiTreeItem('Sign in to RAI', {
          icon: 'sign-in',
          command: { command: 'rai.login', title: 'RAI: Sign in' },
        }),
      ];
    }

    const rows: RaiTreeItem[] = [
      new RaiTreeItem(email ?? 'Signed in', { icon: 'account', description: 'account' }),
    ];
    if (projectSlug) {
      rows.push(
        new RaiTreeItem(projectSlug, {
          icon: 'repo',
          description: 'project',
          command: { command: 'rai.openInWebApp', title: 'RAI: Open in web app' },
        }),
      );
    } else {
      rows.push(new RaiTreeItem('No project bound', { icon: 'warning' }));
    }
    return rows;
  }
}

interface RaiItemOptions {
  icon?: string;
  description?: string;
  command?: vscode.Command;
}

class RaiTreeItem extends vscode.TreeItem {
  constructor(label: string, opts: RaiItemOptions = {}) {
    super(label, vscode.TreeItemCollapsibleState.None);
    if (opts.icon) {
      this.iconPath = new vscode.ThemeIcon(opts.icon);
    }
    this.description = opts.description;
    this.command = opts.command;
  }
}
