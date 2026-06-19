import * as vscode from 'vscode';
import { ApiClient, MCP_TOKEN_KEY } from './apiClient';

const REFRESH_KEY = 'rai.refreshToken';
const TOKEN_ID_KEY = 'rai.tokenId';
const PROJECT_ID_KEY = 'rai.projectId';
const PROJECT_SLUG_KEY = 'rai.projectSlug';
const EMAIL_KEY = 'rai.email';

export interface SessionState {
  signedIn: boolean;
  email?: string;
  projectId?: number;
  projectSlug?: string;
}

interface LoginResponse { access_token: string; refresh_token: string; expires_in: number; }
interface TokenCreated { id: number; raw_token: string; name: string; }
interface Project { id: number; slug: string; name: string; my_role?: string | null; }

/**
 * Owns the device's auth lifecycle: password login → reuse-or-mint a single
 * per-device MCP token → bind it to a project. The MCP token (used as Bearer for
 * all REST) and the JWT refresh token (used only to revoke on logout) live in
 * SecretStorage; non-secret state lives in globalState.
 */
export class Session {
  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly api: ApiClient,
  ) {}

  get state(): SessionState {
    const g = this.context.globalState;
    return {
      signedIn: g.get<number>(TOKEN_ID_KEY) !== undefined,
      email: g.get<string>(EMAIL_KEY),
      projectId: g.get<number>(PROJECT_ID_KEY),
      projectSlug: g.get<string>(PROJECT_SLUG_KEY),
    };
  }

  async signIn(email: string, password: string): Promise<void> {
    // 1. password login → JWT
    const login = await this.api.request<LoginResponse>('/api/v1/auth/login', {
      method: 'POST', body: { email, password }, token: null,
    });
    const jwt = login.access_token;

    // 2. reuse the stored MCP token if it still validates, else mint a per-device one
    let mcpToken = await this.context.secrets.get(MCP_TOKEN_KEY);
    let tokenId = this.context.globalState.get<number>(TOKEN_ID_KEY);
    if (!mcpToken || !(await this.tokenValid(mcpToken))) {
      const name = `rai-ide-${vscode.env.machineId.slice(0, 8)}`;
      const created = await this.api.request<TokenCreated>('/api/v1/mcp/tokens', {
        method: 'POST', body: { name }, token: jwt,
      });
      mcpToken = created.raw_token;
      tokenId = created.id;
      await this.context.secrets.store(MCP_TOKEN_KEY, mcpToken);
    }

    // 3. choose a project and bind the token to it (project-scoped MCP token)
    const projects = await this.api.request<Project[]>('/api/v1/projects/', { token: jwt });
    const project = await pickProject(projects);
    if (project) {
      await this.api.request('/api/v1/mcp/bind-project', {
        method: 'POST', body: { project_id: project.id }, token: mcpToken,
      });
      await this.context.globalState.update(PROJECT_ID_KEY, project.id);
      await this.context.globalState.update(PROJECT_SLUG_KEY, project.slug);
    }

    // 4. persist non-secret state + the refresh token (for logout revoke)
    await this.context.secrets.store(REFRESH_KEY, login.refresh_token);
    await this.context.globalState.update(TOKEN_ID_KEY, tokenId);
    await this.context.globalState.update(EMAIL_KEY, email);
  }

  async signOut(): Promise<void> {
    // Best-effort server-side revoke: revoke needs a JWT, so mint one from the
    // stored refresh token. Always clear local state regardless of the outcome.
    const tokenId = this.context.globalState.get<number>(TOKEN_ID_KEY);
    const refresh = await this.context.secrets.get(REFRESH_KEY);
    if (tokenId !== undefined && refresh) {
      try {
        const r = await this.api.request<{ access_token: string }>('/api/v1/auth/refresh', {
          method: 'POST', body: { refresh_token: refresh }, token: null,
        });
        await this.api.del(`/api/v1/mcp/tokens/${tokenId}`, r.access_token);
      } catch {
        // token may already be invalid/expired — local clear below still applies
      }
    }
    await this.context.secrets.delete(MCP_TOKEN_KEY);
    await this.context.secrets.delete(REFRESH_KEY);
    for (const key of [TOKEN_ID_KEY, PROJECT_ID_KEY, PROJECT_SLUG_KEY, EMAIL_KEY]) {
      await this.context.globalState.update(key, undefined);
    }
  }

  private async tokenValid(token: string): Promise<boolean> {
    try {
      await this.api.request('/api/v1/mcp/whoami', { token });
      return true;
    } catch {
      return false;
    }
  }
}

async function pickProject(projects: Project[]): Promise<Project | undefined> {
  if (projects.length === 0) {
    void vscode.window.showWarningMessage('RAI: signed in, but you are not a member of any project yet.');
    return undefined;
  }
  if (projects.length === 1) {
    return projects[0];
  }
  const pick = await vscode.window.showQuickPick(
    projects.map((p) => ({ label: p.name, description: p.slug, project: p })),
    { placeHolder: 'Bind this device to which project?' },
  );
  return pick?.project;
}
