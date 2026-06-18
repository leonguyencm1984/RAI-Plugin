import * as vscode from 'vscode';
import AdmZip from 'adm-zip';
import { ApiClient, ApiError } from './apiClient';

export interface Repository {
  id: number;
  name: string;
  default_branch: string;
  last_sync_status: string;
  gitnexus_indexed_commit: string | null;
}

export type PullOutcome =
  | { status: 'extracted'; files: number; dir: vscode.Uri }
  | { status: 'sync-triggered' }
  | { status: 'cancelled' };

/**
 * Pulls a project's repositories to disk via the archive endpoint (a server
 * -side, `.git`-less ZIP snapshot). If the repo isn't indexed yet the archive
 * returns 409; we offer to enqueue a sync (POST .../sync, async) and ask the
 * user to retry once it finishes — we deliberately don't block-poll for what
 * can be minutes. Extraction is zip-slip-safe: every entry is confined to the
 * target directory.
 */
export class RepoManager {
  constructor(private readonly api: ApiClient) {}

  async listRepos(projectId: number): Promise<Repository[]> {
    return this.api.get<Repository[]>(`/api/v1/projects/${projectId}/repositories`);
  }

  async pullRepo(repo: Repository, parentDir: vscode.Uri): Promise<PullOutcome> {
    let archive: ArrayBuffer;
    try {
      archive = await this.api.getBinary(`/api/v1/repositories/${repo.id}/archive`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        return this.offerSync(repo);
      }
      throw err;
    }

    const dir = vscode.Uri.joinPath(parentDir, sanitizeDirName(repo.name));
    const files = await extractZip(archive, dir);
    return { status: 'extracted', files, dir };
  }

  private async offerSync(repo: Repository): Promise<PullOutcome> {
    const pick = await vscode.window.showWarningMessage(
      `RAI: "${repo.name}" isn't indexed yet. Trigger a sync now? (You can pull again once it finishes.)`,
      { modal: true },
      'Trigger sync',
    );
    if (pick !== 'Trigger sync') {
      return { status: 'cancelled' };
    }
    await this.api.post(`/api/v1/repositories/${repo.id}/sync`);
    return { status: 'sync-triggered' };
  }
}

/** Extract a ZIP buffer into `dir`, returning the file count. Skips entries that escape `dir`. */
async function extractZip(archive: ArrayBuffer, dir: vscode.Uri): Promise<number> {
  const zip = new AdmZip(Buffer.from(archive));
  let count = 0;
  for (const entry of zip.getEntries()) {
    if (entry.isDirectory) {
      continue;
    }
    const parts = entry.entryName.split('/').filter((p) => p && p !== '.');
    if (parts.length === 0 || parts.includes('..')) {
      continue; // zip-slip guard
    }
    const target = vscode.Uri.joinPath(dir, ...parts);
    await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(target, '..'));
    await vscode.workspace.fs.writeFile(target, new Uint8Array(entry.getData()));
    count++;
  }
  return count;
}

function sanitizeDirName(name: string): string {
  return name.replace(/[/\\:*?"<>|]/g, '-').trim() || 'repo';
}
