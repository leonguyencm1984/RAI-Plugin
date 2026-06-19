import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import { Session } from './session';
import { Vault, Scope, Kind } from './vault';

// --- remote DTOs (subset of the platform's response shapes) -----------------

interface Paginated<T> { items: T[]; total: number; page: number; limit: number; }
interface SkillSummary { slug: string; name: string; description: string | null; project_id: number | null; runtime: string; tags: string[]; latest_version_no: number | null; }
interface SkillDetail extends SkillSummary { content_md: string; }
interface WorkflowSummary { id: number; name: string; description: string | null; project_id: number; created_at: string; }
interface SourceSummary { id: number; title: string; project_id: number | null; version: number; }

export type DriftState = 'synced' | 'remote-newer' | 'remote-only';

export interface ResourceStatus {
  kind: Kind;
  scope: Scope;
  /** Stable identifier used for the on-disk filename and etag key. */
  key: string;
  label: string;
  drift: DriftState;
}

export interface PullSummary { pulled: number; skipped: number; errors: number; }

/**
 * Drives pull / push / drift between the platform REST API and the local
 * `.rkm/` vault. The status model is intentionally remote-driven: drift is
 * computed by comparing each remote resource's version marker against the
 * locally cached etag, which is the same signal the plugin uses. Direct push
 * is skills-only by design — KB and workflow push route through the E8
 * change-request flow (v2), so this surfaces a deep-link instead.
 */
export class SyncManager {
  constructor(
    private readonly api: ApiClient,
    private readonly session: Session,
  ) {}

  // --- status ---------------------------------------------------------------

  async computeStatus(vault: Vault): Promise<ResourceStatus[]> {
    const [remote, etags] = await Promise.all([this.fetchRemote(), vault.loadEtags()]);
    const out: ResourceStatus[] = [];
    for (const r of remote) {
      const key = Vault.etagKey(r.kind, r.scope, r.key);
      const present = await vault.exists(this.fileUri(vault, r));
      const drift: DriftState = !present ? 'remote-only' : etags[key] === r.etag ? 'synced' : 'remote-newer';
      out.push({ kind: r.kind, scope: r.scope, key: r.key, label: r.label, drift });
    }
    return out;
  }

  // --- pull -----------------------------------------------------------------

  async pullAll(vault: Vault, force = false): Promise<PullSummary> {
    const remote = await this.fetchRemote();
    const etags = await vault.loadEtags();
    const summary: PullSummary = { pulled: 0, skipped: 0, errors: 0 };

    for (const r of remote) {
      const key = Vault.etagKey(r.kind, r.scope, r.key);
      if (!force && etags[key] === r.etag && (await vault.exists(this.fileUri(vault, r)))) {
        summary.skipped++;
        continue;
      }
      try {
        await this.pullOne(vault, r);
        etags[key] = r.etag;
        summary.pulled++;
      } catch {
        summary.errors++;
      }
    }
    await vault.saveEtags(etags);
    return summary;
  }

  private async pullOne(vault: Vault, r: Remote): Promise<void> {
    if (r.kind === 'skill') {
      const detail = await this.api.get<SkillDetail>(`/api/v1/skills/${r.key}`);
      const skillDir = vscode.Uri.joinPath(vault.dir('skill', r.scope), r.key);
      await vault.writeFile(vscode.Uri.joinPath(skillDir, 'SKILL.md'), detail.content_md ?? '');
      await vault.writeFile(vscode.Uri.joinPath(skillDir, 'skill.yaml'), skillManifest(detail));
      await vault.writeFile(vscode.Uri.joinPath(skillDir, '.rkm-etag'), r.etag);
    } else if (r.kind === 'workflow') {
      const detail = await this.api.get<unknown>(`/api/v1/workflows/${r.key}`);
      await vault.writeFile(this.fileUri(vault, r), JSON.stringify(detail, null, 2));
    } else {
      await vault.writeFile(this.fileUri(vault, r), JSON.stringify(r.raw, null, 2));
    }
  }

  // --- push (skills only; KB/workflow push is E8/v2) ------------------------

  async pushSkills(vault: Vault): Promise<{ pushed: number; errors: number } | undefined> {
    const local = await this.listLocalSkills(vault);
    if (local.length === 0) {
      void vscode.window.showInformationMessage('RAI: no local skills found in the vault to push.');
      return undefined;
    }
    const picked = await vscode.window.showQuickPick(
      local.map((s) => ({ label: s.slug, description: s.scope, picked: false, skill: s })),
      { canPickMany: true, placeHolder: 'Select skills to push (overwrites platform content)' },
    );
    if (!picked || picked.length === 0) {
      return undefined;
    }
    let pushed = 0;
    let errors = 0;
    for (const p of picked) {
      try {
        const content = await vault.readFile(vscode.Uri.joinPath(p.skill.uri, 'SKILL.md'));
        await this.api.request(`/api/v1/skills/${p.skill.slug}`, { method: 'PATCH', body: { content_md: content ?? '' } });
        pushed++;
      } catch {
        errors++;
      }
    }
    return { pushed, errors };
  }

  // --- remote fetch ---------------------------------------------------------

  private async fetchRemote(): Promise<Remote[]> {
    const projectId = this.session.state.projectId;
    const out: Remote[] = [];

    for (const s of await this.fetchPaginated<SkillSummary>('/api/v1/skills/', projectId)) {
      const scope: Scope = s.project_id ? 'project' : 'org';
      out.push({ kind: 'skill', scope, key: s.slug, label: s.name, etag: String(s.latest_version_no ?? 0) });
    }

    if (projectId !== undefined) {
      const wfs = await this.api.get<WorkflowSummary[]>(`/api/v1/workflows/?project_id=${projectId}`);
      for (const w of wfs) {
        out.push({ kind: 'workflow', scope: 'project', key: String(w.id), label: w.name, etag: w.created_at });
      }
    }

    for (const src of await this.fetchPaginated<SourceSummary>('/api/v1/sources/', projectId)) {
      const scope: Scope = src.project_id ? 'project' : 'org';
      out.push({ kind: 'source', scope, key: String(src.id), label: src.title, etag: String(src.version), raw: src });
    }
    return out;
  }

  private async fetchPaginated<T>(path: string, projectId: number | undefined): Promise<T[]> {
    const items: T[] = [];
    const limit = 100;
    for (let page = 1; ; page++) {
      const q = new URLSearchParams({ page: String(page), limit: String(limit) });
      if (projectId !== undefined) {
        q.set('project_id', String(projectId));
      }
      const res = await this.api.get<Paginated<T>>(`${path}?${q.toString()}`);
      items.push(...res.items);
      if (res.items.length < limit || items.length >= res.total) {
        break;
      }
    }
    return items;
  }

  private async listLocalSkills(vault: Vault): Promise<Array<{ slug: string; scope: Scope; uri: vscode.Uri }>> {
    const found: Array<{ slug: string; scope: Scope; uri: vscode.Uri }> = [];
    for (const scope of ['org', 'project'] as Scope[]) {
      const dir = vault.dir('skill', scope);
      let entries: [string, vscode.FileType][];
      try {
        entries = await vscode.workspace.fs.readDirectory(dir);
      } catch {
        continue;
      }
      for (const [name, type] of entries) {
        if (type === vscode.FileType.Directory) {
          found.push({ slug: name, scope, uri: vscode.Uri.joinPath(dir, name) });
        }
      }
    }
    return found;
  }

  private fileUri(vault: Vault, r: Remote): vscode.Uri {
    if (r.kind === 'skill') {
      return vscode.Uri.joinPath(vault.dir('skill', r.scope), r.key, 'SKILL.md');
    }
    if (r.kind === 'workflow') {
      return vscode.Uri.joinPath(vault.dir('workflow', r.scope), `${r.key}.json`);
    }
    return vscode.Uri.joinPath(vault.dir('source', r.scope), `${r.key}.json`);
  }
}

interface Remote {
  kind: Kind;
  scope: Scope;
  key: string;
  label: string;
  /** Remote version marker compared against the cached etag for drift. */
  etag: string;
  raw?: unknown;
}

function skillManifest(s: SkillDetail): string {
  const tags = s.tags.length ? `[${s.tags.map((t) => JSON.stringify(t)).join(', ')}]` : '[]';
  return [
    'schema_version: 1',
    `slug: ${JSON.stringify(s.slug)}`,
    `name: ${JSON.stringify(s.name)}`,
    `description: ${JSON.stringify(s.description ?? '')}`,
    `runtime: ${JSON.stringify(s.runtime)}`,
    `tags: ${tags}`,
    '',
  ].join('\n');
}
