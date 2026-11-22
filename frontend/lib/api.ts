export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "docchat_token";

export const getToken = (): string | null =>
  typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);

export const setToken = (token: string | null) => {
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type Doc = {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  status: "uploaded" | "processing" | "ready" | "failed";
  error: string | null;
  num_chunks: number;
  stage: string | null;
  timings: Record<string, number> | null;
  created_at: string;
};

export type Source = { n: number; filename: string; page: number; doc_id: string; score: number };

export type Considered = { filename: string; page: number; score: number; kept: boolean };

export type Latency = {
  serverTtftMs?: number;
  clientTtftMs?: number;
  retrievalMs?: number;
  prepMs?: number;
  embedMs?: number;
  searchMs?: number;
  rerankMs?: number;
  considered?: Considered[];
  totalMs?: number;
  cached?: boolean;
};

export type Usage = { used_today: number; daily_limit: number; resets_at: string };

export type HistoryTurn = { role: "user" | "assistant"; content: string };

async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, detail);
}

const authHeaders = (): Record<string, string> => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...(init.headers as Record<string, string>), ...authHeaders() },
  });
  if (!res.ok) throw await errorFrom(res);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export async function authenticate(mode: "login" | "register", email: string, password: string) {
  const data = await request<{ access_token: string }>(`/auth/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  setToken(data.access_token);
}

export const getUsage = () => request<Usage>("/usage");

export const listDocs = () => request<Doc[]>("/documents");

export const uploadDoc = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<Doc>("/documents", { method: "POST", body: form });
};

export const deleteDoc = (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" });

export type SseEvent = { event: string; data: any };

/** POST /chat and yield parsed SSE events as they arrive. */
export async function* streamChat(
  question: string,
  history: HistoryTurn[],
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!res.ok || !res.body) throw await errorFrom(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}