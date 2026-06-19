import * as vscode from 'vscode';
import { ApiClient } from './apiClient';

interface ManifestEntry {
  path: string;
  content: string;
  content_hash: string;
  mode: 'skip' | 'overwrite';
}
interface OnboardingManifest {
  repository_id: number;
  resolved_scopes: string[];
  entries: ManifestEntry[];
}

export interface ApplySummary { written: number; overwritten: number; skipped: number; unchanged: number; }

const ENC = new TextEncoder();
const DEC = new TextDecoder();

/**
 * Applies a repository's resolved onboarding manifest (CLAUDE.md / AGENTS.md /
 * .claude + .vscode config) into a local directory so a pulled repo "just
 * works". The backend owns all resolution — this is a dumb writer. Safety
 * model: missing files are created; identical files are left alone; conflicting
 * files prompt per-file with a **default of Skip**, and any overwrite first
 * backs the existing file up to `<path>.rai-bak`.
 */
export class OnboardingApplier {
  constructor(private readonly api: ApiClient) {}

  async apply(repositoryId: number, repoRoot: vscode.Uri): Promise<ApplySummary | undefined> {
    const manifest = await this.api.get<OnboardingManifest>(
      `/api/v1/repositories/${repositoryId}/onboarding/manifest`,
    );
    if (manifest.entries.length === 0) {
      void vscode.window.showInformationMessage('RAI: no onboarding configured for this repository.');
      return undefined;
    }

    const summary: ApplySummary = { written: 0, overwritten: 0, skipped: 0, unchanged: 0 };
    let applyToRest: 'overwrite' | 'skip' | undefined;

    for (const entry of manifest.entries) {
      if (unsafePath(entry.path)) {
        summary.skipped++;
        continue;
      }
      const target = vscode.Uri.joinPath(repoRoot, ...entry.path.split('/'));
      const existing = await readText(target);

      if (existing === undefined) {
        await writeText(target, entry.content);
        summary.written++;
        continue;
      }
      if ((await sha256Hex(existing)) === entry.content_hash) {
        summary.unchanged++;
        continue;
      }

      // Conflict: existing local file differs from the manifest.
      let action: 'overwrite' | 'skip';
      if (applyToRest) {
        action = applyToRest;
      } else {
        const choice = await promptConflict(entry.path);
        if (choice === undefined) {
          summary.skipped++;
          continue; // dialog dismissed → treat as skip
        }
        action = choice.action;
        if (choice.scope === 'all') {
          applyToRest = choice.action;
        }
      }
      if (action === 'overwrite') {
        await writeText(target.with({ path: `${target.path}.rai-bak` }), existing);
        await writeText(target, entry.content);
        summary.overwritten++;
      } else {
        summary.skipped++;
      }
    }
    return summary;
  }
}

interface ConflictChoice { action: 'overwrite' | 'skip'; scope: 'one' | 'all'; }

async function promptConflict(path: string): Promise<ConflictChoice | undefined> {
  const pick = await vscode.window.showWarningMessage(
    `RAI: "${path}" exists and differs from the configured version. Overwrite it? A backup (.rai-bak) will be kept.`,
    { modal: true },
    'Overwrite',
    'Overwrite all',
    'Skip all',
  );
  switch (pick) {
    case 'Overwrite':
      return { action: 'overwrite', scope: 'one' };
    case 'Overwrite all':
      return { action: 'overwrite', scope: 'all' };
    case 'Skip all':
      return { action: 'skip', scope: 'all' };
    default:
      return undefined; // "Skip" implicit / dialog dismissed
  }
}

function unsafePath(p: string): boolean {
  return p.startsWith('/') || p.split('/').includes('..') || p.includes('\\');
}

async function readText(uri: vscode.Uri): Promise<string | undefined> {
  try {
    return DEC.decode(await vscode.workspace.fs.readFile(uri));
  } catch {
    return undefined;
  }
}

async function writeText(uri: vscode.Uri, content: string): Promise<void> {
  await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(uri, '..'));
  await vscode.workspace.fs.writeFile(uri, ENC.encode(content));
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', ENC.encode(text));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}
