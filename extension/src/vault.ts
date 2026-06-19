import * as vscode from 'vscode';

/** Resource scope on the platform. Workflows are always project-scoped. */
export type Scope = 'org' | 'project';
export type Kind = 'skill' | 'workflow' | 'source';

const ENC = new TextEncoder();
const DEC = new TextDecoder();

/**
 * Filesystem layer for the local `.rkm/` vault, kept byte-compatible with the
 * RKMemoria plugin's layout so the plugin and this extension can share one
 * checkout. All I/O goes through vscode.workspace.fs (works in remote/virtual
 * workspaces). Drift is tracked via a single `.etag-cache.json` keyed by
 * `<kind>:<scope>:<slug>` → remote version marker.
 */
export class Vault {
  constructor(
    private readonly root: vscode.Uri,
    private readonly projectSlug: string | undefined,
  ) {}

  /**
   * Resolve the vault root (`<workspace>/<rai.vaultPath>`), or undefined if no
   * folder is open. The project slug (from the active session) selects the
   * `projects/<slug>/` subtree for project-scoped resources.
   */
  static resolve(projectSlug?: string): Vault | undefined {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      return undefined;
    }
    const sub = vscode.workspace.getConfiguration('rai').get<string>('vaultPath', '.rkm');
    const root = vscode.Uri.joinPath(folder.uri, sub);
    return new Vault(root, projectSlug);
  }

  get rootUri(): vscode.Uri {
    return this.root;
  }

  /** Directory for a resource kind under a scope, e.g. `.rkm/org/skills` or `.rkm/projects/<slug>/workflows`. */
  dir(kind: Kind, scope: Scope): vscode.Uri {
    const base = scope === 'org'
      ? vscode.Uri.joinPath(this.root, 'org')
      : vscode.Uri.joinPath(this.root, 'projects', this.projectSlug ?? '_project');
    const leaf = kind === 'source' ? ['kb', 'sources'] : kind === 'skill' ? ['skills'] : ['workflows'];
    return vscode.Uri.joinPath(base, ...leaf);
  }

  async writeFile(uri: vscode.Uri, content: string): Promise<void> {
    await vscode.workspace.fs.createDirectory(parentOf(uri));
    await vscode.workspace.fs.writeFile(uri, ENC.encode(content));
  }

  async readFile(uri: vscode.Uri): Promise<string | undefined> {
    try {
      return DEC.decode(await vscode.workspace.fs.readFile(uri));
    } catch {
      return undefined;
    }
  }

  async exists(uri: vscode.Uri): Promise<boolean> {
    try {
      await vscode.workspace.fs.stat(uri);
      return true;
    } catch {
      return false;
    }
  }

  // --- etag cache -----------------------------------------------------------

  private get etagCacheUri(): vscode.Uri {
    return vscode.Uri.joinPath(this.root, '.etag-cache.json');
  }

  async loadEtags(): Promise<Record<string, string>> {
    const raw = await this.readFile(this.etagCacheUri);
    if (!raw) {
      return {};
    }
    try {
      return JSON.parse(raw) as Record<string, string>;
    } catch {
      return {};
    }
  }

  async saveEtags(etags: Record<string, string>): Promise<void> {
    await this.writeFile(this.etagCacheUri, JSON.stringify(etags, null, 2));
  }

  static etagKey(kind: Kind, scope: Scope, slug: string): string {
    return `${kind}:${scope}:${slug}`;
  }
}

function parentOf(uri: vscode.Uri): vscode.Uri {
  return vscode.Uri.joinPath(uri, '..');
}
