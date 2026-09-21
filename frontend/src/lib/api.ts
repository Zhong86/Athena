const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

/** Matches the CHECK constraint on sessions.type. */
export type SessionType = "chat" | "quiz" | "cron" | "agent_action";

export type ChatMessage = { role: "user" | "assistant"; content: string; at?: string };

export type Session = {
  id: number;
  type: SessionType;
  started_at: string;
  payload: { messages?: ChatMessage[] } | null;
  summary: string | null;
};

export type SessionPage = {
  items: Session[];
  total: number;
  limit: number;
  offset: number;
};

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(`Cannot reach the backend at ${API_BASE}.`);
  }

  if (!res.ok) {
    // FastAPI puts the message in `detail`; fall back to the status line.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail);
  }

  return res.json() as Promise<T>;
}

export function listSessions(params: {
  type?: SessionType;
  limit?: number;
  offset?: number;
}): Promise<SessionPage> {
  const qs = new URLSearchParams();
  if (params.type) qs.set("type", params.type);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return request<SessionPage>(`/sessions${query ? `?${query}` : ""}`);
}

export function getSession(id: number): Promise<Session> {
  return request<Session>(`/sessions/${id}`);
}

export function createSession(type: SessionType = "chat"): Promise<Session> {
  return request<Session>("/sessions", {
    method: "POST",
    body: JSON.stringify({ type }),
  });
}

export function sendChat(
  id: number,
  message: string,
): Promise<{ session_id: number; reply: string }> {
  return request(`/sessions/${id}/chat`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

/* ---------- presentation helpers ---------- */

/** The mockup's filter bar labels "cron" sessions as Findings. */
export const TYPE_LABEL: Record<SessionType, string> = {
  chat: "Chat",
  quiz: "Quiz",
  cron: "Finding",
  agent_action: "Agent action",
};

export const TYPE_TONE: Record<SessionType, string> = {
  chat: "chat",
  quiz: "quiz",
  cron: "finding",
  agent_action: "agent",
};

export const TYPE_GLYPH: Record<SessionType, string> = {
  chat: "💬",
  quiz: "✎",
  cron: "◆",
  agent_action: "⤴",
};

export function messagesOf(session: Session): ChatMessage[] {
  return session.payload?.messages ?? [];
}

/**
 * The sessions table has no title column, so derive one: the opening user
 * message for a chat, otherwise the type label.
 */
export function titleOf(session: Session): string {
  const first = messagesOf(session).find((m) => m.role === "user")?.content;
  if (first) return first.length > 60 ? `${first.slice(0, 60)}…` : first;
  return `${TYPE_LABEL[session.type]} session`;
}
