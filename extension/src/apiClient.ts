import * as vscode from 'vscode';

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export const MCP_TOKEN_KEY = 'rai.mcpToken';

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Explicit bearer token. `undefined` → use the stored MCP token; `null` → no auth. */
  token?: string | null;
}

/**
 * Typed REST client that runs in the extension host (Node), never a webview, so
 * there is no CORS. The MCP token lives in SecretStorage; JWT-authed calls during
 * login pass an explicit token. Central error mapping: 0 = network, plus HTTP codes.
 */
export class ApiClient {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  get baseUrl(): string {
    const raw = vscode.workspace.getConfiguration('rai').get<string>('apiBaseUrl', 'http://localhost:8000');
    return raw.replace(/\/+$/, '');
  }

  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const token = opts.token === undefined ? await this.secrets.get(MCP_TOKEN_KEY) : opts.token;
    const res = await this.doFetch(path, token ?? undefined, opts);

    if (res.status === 204) {
      return undefined as T;
    }
    const text = await res.text();
    if (res.status >= 200 && res.status < 300) {
      return text ? (JSON.parse(text) as T) : (undefined as T);
    }
    throw new ApiError(res.status, parseDetail(text, `HTTP ${res.status}`));
  }

  /** Fetch a binary body (e.g. a repo archive zip) using the stored MCP token. */
  async getBinary(path: string): Promise<ArrayBuffer> {
    const token = await this.secrets.get(MCP_TOKEN_KEY);
    const res = await this.doFetch(path, token ?? undefined, {});
    if (res.status >= 200 && res.status < 300) {
      return res.arrayBuffer();
    }
    throw new ApiError(res.status, parseDetail(await res.text(), `HTTP ${res.status}`));
  }

  private async doFetch(path: string, token: string | undefined, opts: RequestOptions) {
    try {
      return await fetch(`${this.baseUrl}${path}`, {
        method: opts.method ?? 'GET',
        headers: {
          ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
    } catch (err) {
      throw new ApiError(0, `Network error — is the RAI API reachable at ${this.baseUrl}? (${(err as Error).message})`);
    }
  }

  get<T>(path: string, token?: string | null): Promise<T> {
    return this.request<T>(path, { token });
  }
  post<T>(path: string, body?: unknown, token?: string | null): Promise<T> {
    return this.request<T>(path, { method: 'POST', body, token });
  }
  del(path: string, token?: string | null): Promise<void> {
    return this.request<void>(path, { method: 'DELETE', token });
  }
}

function parseDetail(text: string, fallback: string): string {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (Array.isArray(body.detail)) {
      return body.detail.map((e: { msg?: string }) => e.msg ?? '').filter(Boolean).join('; ');
    }
    return typeof body.detail === 'string' ? body.detail : fallback;
  } catch {
    return fallback;
  }
}
