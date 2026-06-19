import * as vscode from 'vscode';
import { Session } from './session';
import { Vault, Kind } from './vault';
import { SyncManager, ResourceStatus, DriftState } from './sync';

type Node = CategoryNode | ResourceNode | MessageNode;

/**
 * Two-level drift tree: SKILLS / WORKFLOWS / KNOWLEDGE BASE categories, each
 * expanding to its resources annotated with sync state (synced / pull-available
 * / not-pulled). Status is fetched lazily and memoised per refresh so expanding
 * categories doesn't re-hit the API; {@link refresh} clears the cache.
 */
export class StatusTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<Node | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private cache: Promise<ResourceStatus[]> | undefined;

  constructor(
    private readonly session: Session,
    private readonly sync: SyncManager,
  ) {}

  refresh(): void {
    this.cache = undefined;
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(node: Node): vscode.TreeItem {
    return node.toItem();
  }

  async getChildren(node?: Node): Promise<Node[]> {
    if (!this.session.state.signedIn) {
      return node ? [] : [new MessageNode('Sign in to view sync status', 'sign-in')];
    }
    const vault = Vault.resolve(this.session.state.projectSlug);
    if (!vault) {
      return node ? [] : [new MessageNode('Open a folder to use the RAI vault', 'folder')];
    }

    if (!node) {
      return CATEGORIES.map((c) => new CategoryNode(c.kind, c.label));
    }
    if (node instanceof CategoryNode) {
      let statuses: ResourceStatus[];
      try {
        this.cache ??= this.sync.computeStatus(vault);
        statuses = (await this.cache).filter((s) => s.kind === node.kind);
      } catch (err) {
        return [new MessageNode(`Failed to load: ${(err as Error).message}`, 'error')];
      }
      if (statuses.length === 0) {
        return [new MessageNode('Nothing here yet', 'dash')];
      }
      return [...statuses]
        .sort((a, b) => a.label.localeCompare(b.label))
        .map((s) => new ResourceNode(s));
    }
    return [];
  }
}

const CATEGORIES: Array<{ kind: Kind; label: string }> = [
  { kind: 'skill', label: 'Skills' },
  { kind: 'workflow', label: 'Workflows' },
  { kind: 'source', label: 'Knowledge Base' },
];

const DRIFT_ICON: Record<DriftState, { icon: string; hint: string }> = {
  synced: { icon: 'check', hint: 'in sync' },
  'remote-newer': { icon: 'cloud-download', hint: 'update available — pull' },
  'remote-only': { icon: 'cloud', hint: 'not pulled yet' },
};

class CategoryNode {
  constructor(readonly kind: Kind, readonly label: string) {}
  toItem(): vscode.TreeItem {
    const item = new vscode.TreeItem(this.label, vscode.TreeItemCollapsibleState.Collapsed);
    item.iconPath = new vscode.ThemeIcon('folder');
    item.contextValue = 'rai.category';
    return item;
  }
}

class ResourceNode {
  constructor(private readonly status: ResourceStatus) {}
  toItem(): vscode.TreeItem {
    const { icon, hint } = DRIFT_ICON[this.status.drift];
    const item = new vscode.TreeItem(this.status.label, vscode.TreeItemCollapsibleState.None);
    item.description = `${this.status.scope} · ${hint}`;
    item.iconPath = new vscode.ThemeIcon(icon);
    item.contextValue = `rai.resource.${this.status.drift}`;
    item.tooltip = `${this.status.kind} · ${this.status.scope} · ${hint}`;
    return item;
  }
}

class MessageNode {
  constructor(private readonly message: string, private readonly icon: string) {}
  toItem(): vscode.TreeItem {
    const item = new vscode.TreeItem(this.message, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon(this.icon);
    return item;
  }
}
