/**
 * Server and browser need different addresses for the same backend. In Docker
 * the browser reaches it through the reverse proxy (`https://host/api`) while
 * server components reach it directly on the compose network
 * (`http://backend:8000`) -- routing SSR back out through the public hostname
 * relies on hairpin NAT, which plenty of VPS hosts do not do.
 *
 * `INTERNAL_API_BASE_URL` is deliberately not `NEXT_PUBLIC_`: it must stay a
 * runtime server-only value, never inlined into the client bundle.
 */
export const API_BASE =
  (typeof window === "undefined" ? process.env.INTERNAL_API_BASE_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

/** Matches the CHECK constraint on sessions.type. */
export type SessionType = "chat" | "quiz" | "cron" | "agent_action";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  at?: string;
  /** Present on an assistant turn that went through the Runs API. */
  trace?: TraceEntry[];
};

/**
 * One step of a Hermes run, as folded by `agent/hermes.py`. Persisted with the
 * session rather than streamed: the gateway's SSE stream is live-only, and
 * Knowledge-Sync renders on the server from what we stored.
 *
 * `note` is Hermes' own mid-run commentary (`message.interim`). Token-level
 * deltas are dropped on the backend -- they only repeat the final summary.
 */
export type TraceEntry =
  | { kind: "note"; at: string; text: string }
  | {
      kind: "tool";
      at: string;
      tool: string;
      /** Argument gist. The gateway truncates these to 500 chars. */
      input?: string | null;
      output?: string | null;
      status?: "running" | "ok" | "error";
      duration?: number | null;
    }
  | { kind: "subagent"; at: string; status?: string; summary?: string; duration?: number | null }
  | { kind: "error"; at: string; text: string };

export type Session = {
  id: number;
  type: SessionType;
  started_at: string;
  /** `kind` discriminates payload shapes sharing a `type` -- e.g. a gather
      run (`"materials_gather"`) carries file ids, not a Hermes trace, so the
      run detail page fetches GatherRunMaterials instead of rendering `trace`. */
  payload: { messages?: ChatMessage[]; trace?: TraceEntry[]; kind?: string } | null;
  summary: string | null;
  /** User-set override; null means fall back to the derived title. */
  title: string | null;
  archived_at: string | null;
};

export type SessionPage = {
  items: Session[];
  total: number;
  limit: number;
  offset: number;
};

export class ApiError extends Error {
  /**
   * The HTTP status, when there was one. Carried because some callers have to
   * tell "you need to fix something" from "the upstream broke" -- the Drive
   * picker turns a 409 into a link to Settings and a 502 into a retry.
   */
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData must set its own Content-Type so the multipart boundary survives.
  const isForm = init?.body instanceof FormData;
  // Merged rather than spread after `init`: a caller passing its own
  // `headers` (e.g. a Server Action attaching X-Gather-Token) would otherwise
  // silently drop Content-Type instead of adding to it.
  const { headers: extraHeaders, ...restInit } = init ?? {};

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...extraHeaders },
      ...restInit,
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
    throw new ApiError(detail, res.status);
  }

  // DELETE returns 204 with no body; res.json() would throw on it.
  if (res.status === 204) return undefined as T;

  return res.json() as Promise<T>;
}

export function listSessions(params: {
  type?: SessionType;
  archived?: boolean;
  limit?: number;
  offset?: number;
}): Promise<SessionPage> {
  const qs = new URLSearchParams();
  if (params.type) qs.set("type", params.type);
  if (params.archived) qs.set("archived", "true");
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

/** Omitted fields are left alone; `title: null` clears the override. */
export function updateSession(
  id: number,
  patch: { title?: string | null; archived?: boolean },
): Promise<Session> {
  return request<Session>(`/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteSession(id: number): Promise<void> {
  return request<void>(`/sessions/${id}`, { method: "DELETE" });
}

export function sendChat(
  id: number,
  message: string,
): Promise<{ session_id: number; reply: string; trace: TraceEntry[] }> {
  return request(`/sessions/${id}/chat`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

/* ---------- materials ---------- */

/** Mirrors the CHECK constraint on source_files.ingest_status. */
export type IngestStatus =
  | "pending"
  | "extracting"
  | "tagging"
  | "embedding"
  | "ready"
  | "failed";

export type UploadType = "text" | "pdf" | "image";

/** Where the original lives. A `drive` file is never copied to our disk. */
export type SourceOrigin = "local" | "drive";

export type SourceFile = {
  id: number;
  filename: string;
  upload_type: UploadType;
  uploaded_at: string;
  ingest_status: IngestStatus;
  ingest_error: string | null;
  byte_size: number | null;
  chunk_count: number;
  origin: SourceOrigin;
  /** Drive's webViewLink. Only set when `origin` is `drive`. */
  drive_url: string | null;
  drive_modified_at: string | null;
};

export type UploadAccepted = {
  source_file_id: number;
  ingest_status: IngestStatus;
  filename: string;
  upload_type: UploadType;
};

export type Topic = {
  id: number;
  name: string;
  description: string | null;
  /** -1 means no signal yet — not a score of zero. */
  user_understanding: number;
  auto_created: boolean;
  chunk_count: number;
  source_count: number;
};

export type TopicSource = {
  source_file_id: number;
  filename: string;
  upload_type: UploadType;
  uploaded_at: string;
  ingest_status: IngestStatus;
  chunks_in_topic: number;
  chunks_total: number;
  other_topics: string[];
  origin: SourceOrigin;
  drive_url: string | null;
};

export type TopicDetail = Omit<Topic, "source_count"> & { sources: TopicSource[] };

export type Chunk = {
  id: number;
  text: string;
  topic_id: number | null;
  source_file_id: number;
  filename: string;
  order_index: number;
};

export type ChunkPage = {
  items: Chunk[];
  total: number;
  limit: number;
  offset: number;
};

export function listTopics(): Promise<Topic[]> {
  return request<Topic[]>("/materials/topics");
}

export function getTopic(id: number): Promise<TopicDetail> {
  return request<TopicDetail>(`/materials/topics/${id}`);
}

/** Both fields optional and independent -- send only what changed. */
export function updateTopic(
  id: number,
  fields: { name?: string; description?: string },
): Promise<Topic> {
  return request<Topic>(`/materials/topics/${id}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

/** Un-tags rather than deletes anything under it -- the chunks, their source
    files and embeddings stay; only the topic label goes. */
export function deleteTopic(id: number): Promise<void> {
  return request<void>(`/materials/topics/${id}`, { method: "DELETE" });
}

export function getTopicChunks(id: number, limit = 100): Promise<ChunkPage> {
  return request<ChunkPage>(`/materials/topics/${id}/chunks?limit=${limit}`);
}

export function listUploads(): Promise<SourceFile[]> {
  return request<SourceFile[]>("/materials/uploads");
}

export function getUpload(id: number): Promise<SourceFile> {
  return request<SourceFile>(`/materials/uploads/${id}`);
}

export function uploadText(filename: string, text: string): Promise<UploadAccepted> {
  return request<UploadAccepted>("/materials/uploads/text", {
    method: "POST",
    body: JSON.stringify({ filename, text }),
  });
}

/**
 * Posted straight to FastAPI rather than through a Server Action: actions cap
 * request bodies at 1MB by default, and the backend accepts PDFs up to 10MB.
 */
export function uploadFile(file: File): Promise<UploadAccepted> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadAccepted>("/materials/uploads", { method: "POST", body: form });
}

/* ---------- Google Drive ---------- */

/** One row of the Drive picker. Nothing is imported at this point. */
export type DriveFile = {
  drive_file_id: string;
  name: string;
  mime_type: string;
  /** How Αθηνα would ingest it, once imported. */
  upload_type: UploadType;
  modified_at: string | null;
  /** Absent for Docs, Sheets and Slides — they have no bytes of their own. */
  size: number | null;
  web_view_link: string | null;
  /** True for native Google formats: what Αθηνα reads is an exported text
   *  rendering, not the document itself. */
  exported: boolean;
  /** Set when this file is already in Materials, so the picker can offer
   *  "re-import" rather than silently duplicating it. */
  source_file_id: number | null;
};

export type DriveFilePage = {
  items: DriveFile[];
  next_page_token: string | null;
};

export type DriveImportResult = {
  accepted: UploadAccepted[];
  /** `{filename: reason}`. Partial success is normal — one unreadable file
   *  should not cost the user the other nine. */
  rejected: Record<string, string>;
};

export function listDriveFiles(opts: {
  search?: string;
  pageToken?: string;
} = {}): Promise<DriveFilePage> {
  const params = new URLSearchParams();
  if (opts.search) params.set("search", opts.search);
  if (opts.pageToken) params.set("page_token", opts.pageToken);
  const query = params.toString();
  return request<DriveFilePage>(`/materials/drive/files${query ? `?${query}` : ""}`);
}

export function importDriveFiles(fileIds: string[]): Promise<DriveImportResult> {
  return request<DriveImportResult>("/materials/drive/import", {
    method: "POST",
    body: JSON.stringify({ file_ids: fileIds }),
  });
}

export function retryUpload(id: number): Promise<SourceFile> {
  return request<SourceFile>(`/materials/uploads/${id}/retry`, { method: "POST" });
}

export function deleteUpload(id: number): Promise<void> {
  return request<void>(`/materials/uploads/${id}`, { method: "DELETE" });
}

/* ---------- materials gather (auto-sync) ---------- */

/** Both null means gather scans all of connected Drive -- the default. */
export type GatherConfig = {
  folder_id: string | null;
  folder_name: string | null;
};

export type GatherRunResult = {
  /** True when a scheduled (cron) call landed before the configured
      interval had elapsed since the last completed run -- everything below
      is empty/null in that case, since no run actually happened. A manual
      "Sync now" click is never skipped. */
  skipped: boolean;
  reason: string | null;
  run_id: number | null;
  /** The Knowledge-Sync row this run wrote. Null when skipped. */
  session_id: number | null;
  candidates_seen: number;
  imported_file_ids: number[];
  refreshed_file_ids: number[];
  skipped_local: string[];
};

/** How often the VPS's polling cron job is allowed to actually run gather
    (see DEPLOY.md's "Materials gather cron" section) -- a plain "Sync now"
    click always runs regardless of this. */
export type GatherIntervalConfig = {
  interval: "daily" | "weekly" | "biweekly";
};

export function getGatherConfig(): Promise<GatherConfig> {
  return request<GatherConfig>("/materials/gather/config");
}

/** `folder` is a pasted Drive folder link or bare id -- resolved against
    Drive server-side before it's stored. */
export function setGatherFolder(folder: string): Promise<GatherConfig> {
  return request<GatherConfig>("/materials/gather/config", {
    method: "PUT",
    body: JSON.stringify({ folder }),
  });
}

/** Reverts to scanning all of Drive. */
export function clearGatherFolder(): Promise<GatherConfig> {
  return request<GatherConfig>("/materials/gather/config", { method: "DELETE" });
}

/**
 * `token` is `X-Gather-Token` -- the backend refuses this endpoint without
 * it, since it auto-imports and ingests with no one to approve the picks.
 * Only ever called from a Server Action (`knowledge-sync/actions.ts`), which
 * is the one place allowed to hold the token: it's a server-only env var,
 * same posture as `INTERNAL_API_BASE_URL`.
 */
export function runGather(token: string): Promise<GatherRunResult> {
  return request<GatherRunResult>("/materials/gather/run", {
    method: "POST",
    headers: { "X-Gather-Token": token },
  });
}

export function getGatherInterval(): Promise<GatherIntervalConfig> {
  return request<GatherIntervalConfig>("/materials/gather/interval");
}

export function setGatherInterval(
  interval: GatherIntervalConfig["interval"],
): Promise<GatherIntervalConfig> {
  return request<GatherIntervalConfig>("/materials/gather/interval", {
    method: "PUT",
    body: JSON.stringify({ interval }),
  });
}

/** One file's contribution to one topic -- a file can appear under several
    topics, since tagging is per-chunk, not per-file. */
export type GatherTopicFile = {
  source_file_id: number;
  filename: string;
  upload_type: UploadType;
  origin: SourceOrigin;
  drive_url: string | null;
  chunk_count: number;
};

export type GatherTopic = {
  topic_id: number;
  topic_name: string;
  files: GatherTopicFile[];
};

/** A touched file with nothing tagged to any topic yet -- still mid-ingest,
    or tagging degraded. `ingest_status` says which. */
export type GatherPendingFile = {
  source_file_id: number;
  filename: string;
  upload_type: UploadType;
  origin: SourceOrigin;
  drive_url: string | null;
  ingest_status: IngestStatus;
};

/** What a gather run actually added, grouped by topic rather than by file --
    computed fresh from current tagging state, not frozen at run time, so it
    stays correct once ingestion finishes or topics get merged/renamed -- and
    so a file deleted from Materials afterward shows up in `removed` by its
    name at the time, rather than silently vanishing from the run's history. */
export type GatherRunMaterials = {
  session_id: number;
  topics: GatherTopic[];
  pending: GatherPendingFile[];
  removed: string[];
  skipped: string[];
};

export function getGatherRunMaterials(sessionId: number): Promise<GatherRunMaterials> {
  return request<GatherRunMaterials>(`/materials/gather/runs/${sessionId}`);
}

/** Whether the backend has TEST_MODE on -- gates rendering the "Reset all
    materials" button. False (and the reset endpoint 404ing) is the default;
    this is never true against a deployed backend. */
export function getTestMode(): Promise<{ enabled: boolean }> {
  return request<{ enabled: boolean }>("/materials/gather/test-mode");
}

/** Dev-only: wipes every source file, topic, chunk, embedding and gather-run
    record. 404s if the backend isn't in TEST_MODE. */
export function resetMaterials(): Promise<void> {
  return request<void>("/materials/gather/reset", { method: "POST" });
}

/* ---------- goals ---------- */

export type GoalStatus = "draft" | "committed" | "archived";
export type GoalCategory = "academic" | "career";
/** The spec's four milestone states. `rejected` only exists mid-draft. */
export type MilestoneStatus = "proposed" | "approved" | "edited" | "rejected";
export type MilestoneSource = "materials" | "research" | "user";
export type ProgressStatus = "upcoming" | "current" | "done";

export type SourceChunk = {
  chunk_id: number;
  topic_id: number | null;
  topic_name: string | null;
  source_file_id: number;
  source_filename: string;
};

/** A committed milestone row. Everything below `reason` is accordion content. */
export type Milestone = {
  id: number;
  state_id: string | null;
  title: string;
  description: string | null;
  order: number;
  status: MilestoneStatus;
  progress_status: ProgressStatus;
  reason: string | null;
  reason_long: string | null;
  est_effort: string | null;
  unlocks_after_title: string | null;
  source: MilestoneSource;
  related_topic_ids: number[];
  source_chunks: SourceChunk[];
};

export type TopicStrength = {
  topic_id: number;
  name: string;
  user_understanding: number;
  strength: "unknown" | "weak" | "fair" | "strong";
};

export type GoalCard = {
  id: number;
  title: string;
  short_name: string | null;
  status: GoalStatus;
  category: GoalCategory;
  course_code: string | null;
  due_at: string | null;
  percent: number;
  done_count: number;
  total_count: number;
  created_at: string;
  focus_title: string | null;
};

export type GoalDetail = {
  id: number;
  title: string;
  short_name: string | null;
  description: string | null;
  status: GoalStatus;
  category: GoalCategory;
  course_code: string | null;
  due_at: string | null;
  derivation: string | null;
  order_rationale: string | null;
  created_at: string;
  updated_at: string | null;
  percent: number;
  done_count: number;
  total_count: number;
  milestones: Milestone[];
  topic_strengths: TopicStrength[];
};

/**
 * A milestone as it exists *inside the graph*, before commit: `id` is the state
 * id (`m3`), not a row id, and `unlocks_after` points at another state id. This
 * is the shape the approval interrupt carries, and the only one the wizard sees.
 */
export type DraftMilestone = {
  id: string;
  title: string;
  description: string;
  order: number;
  status: MilestoneStatus;
  reason: string;
  source: MilestoneSource;
  related_topic_ids: number[];
  est_effort?: string;
  reason_long?: string;
  unlocks_after?: string;
  source_chunk_ids?: number[];
};

export type ClarifyTurn = { question: string; answer: string };

export type ClarifyInterrupt = {
  kind: "clarify";
  questions: string[];
  /** Quick-reply chips, one list per question. May be shorter than `questions`. */
  suggested_answers: string[][];
  clarification_turns: ClarifyTurn[];
};

export type ApprovalInterrupt = {
  kind: "approval";
  milestones: DraftMilestone[];
  decomposition_source: "materials" | "research" | "mixed" | null;
  clarification_turns: ClarifyTurn[];
  clarified_goal: string | null;
};

export type RoadmapInterrupt = ClarifyInterrupt | ApprovalInterrupt;

/**
 * One shape for every graph endpoint. `interrupt: null` with a `goal_id` means
 * the run committed and the wizard is done.
 */
export type RoadmapEnvelope = {
  thread_id: string;
  status: "clarifying" | "decomposing" | "awaiting_approval" | "committed" | "abandoned";
  interrupt: RoadmapInterrupt | null;
  goal_id: number | null;
  /** The student's opening line — the one thing the interrupts do not carry. */
  raw_goal_input: string | null;
};

/** The five resume actions `approve.py` understands, plus the clarify answer. */
export type ResumeAction =
  | { answers: string[] }
  | { action: "approve_all" }
  | { action: "reorder"; ids_in_order: string[] }
  | { action: "edit"; milestone_id: string; fields: Partial<DraftMilestone> }
  | { action: "reject"; milestone_id: string }
  | {
      action: "add_milestone";
      fields: {
        title: string;
        description?: string;
        est_effort?: string;
        related_topic_ids?: number[];
        new_topic_name?: string;
        position?: number;
      };
    };

/** An unfinished graph run, so a parked draft is reachable after a reload. */
export type RoadmapRun = {
  thread_id: string;
  status: string;
  raw_goal_input: string;
  created_at: string;
  updated_at: string | null;
};

export function listUnfinishedRuns(): Promise<RoadmapRun[]> {
  return request<RoadmapRun[]>("/goals/roadmap");
}

export function listGoals(): Promise<GoalCard[]> {
  return request<GoalCard[]>("/goals");
}

export function getGoal(id: number): Promise<GoalDetail> {
  return request<GoalDetail>(`/goals/${id}`);
}

export function updateGoal(
  id: number,
  fields: { title?: string; short_name?: string; status?: GoalStatus; due_at?: string; description?: string },
): Promise<GoalDetail> {
  return request<GoalDetail>(`/goals/${id}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function startRoadmap(raw_goal_input: string): Promise<RoadmapEnvelope> {
  return request<RoadmapEnvelope>("/goals/roadmap", {
    method: "POST",
    body: JSON.stringify({ raw_goal_input }),
  });
}

export function resumeRoadmap(
  threadId: string,
  payload: ResumeAction,
): Promise<RoadmapEnvelope> {
  return request<RoadmapEnvelope>(`/goals/roadmap/${threadId}/resume`, {
    method: "POST",
    body: JSON.stringify({ payload }),
  });
}

/** Reads the parked interrupt without advancing the graph — what "Save and exit" comes back to. */
export function getRoadmapRun(threadId: string): Promise<RoadmapEnvelope> {
  return request<RoadmapEnvelope>(`/goals/roadmap/${threadId}`);
}

export function abandonRoadmap(threadId: string): Promise<void> {
  return request<void>(`/goals/roadmap/${threadId}`, { method: "DELETE" });
}

export function addMilestone(
  goalId: number,
  body: {
    title: string;
    description?: string;
    reason?: string;
    est_effort?: string;
    related_topic_ids?: number[];
    new_topic_name?: string;
    position?: number;
  },
): Promise<Milestone> {
  return request<Milestone>(`/goals/${goalId}/milestones`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateMilestone(
  goalId: number,
  milestoneId: number,
  fields: {
    title?: string;
    description?: string;
    reason?: string;
    reason_long?: string;
    est_effort?: string;
    progress_status?: ProgressStatus;
  },
): Promise<Milestone> {
  return request<Milestone>(`/goals/${goalId}/milestones/${milestoneId}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function deleteMilestone(goalId: number, milestoneId: number): Promise<void> {
  return request<void>(`/goals/${goalId}/milestones/${milestoneId}`, {
    method: "DELETE",
  });
}

/** Must list every milestone of the goal — the backend rejects a partial order. */
export function reorderMilestones(
  goalId: number,
  ids_in_order: number[],
): Promise<Milestone[]> {
  return request<Milestone[]>(`/goals/${goalId}/milestones/order`, {
    method: "PUT",
    body: JSON.stringify({ ids_in_order }),
  });
}

/* ---------- quizzes ---------- */

export type QuestionKind = "multiple_choice" | "open_ended";
/** Mirrors the CHECK constraint on quizzes.status. */
export type QuizStatus = "ready" | "in_progress" | "grading" | "graded";
export type GradedBy = "key" | "hermes";
export type UnderstandingSource = "quiz" | "session" | "manual";

/**
 * One thing a quiz was written from. A `chunk` is a pointer into our own
 * materials; an `external` is a snapshot the backend cannot re-fetch, so it
 * always carries its own `text`.
 */
export type QuizResource = {
  kind: "chunk" | "external";
  chunk_id?: number | null;
  url?: string | null;
  title?: string | null;
  text?: string | null;
};

/**
 * `correct_option`, `rubric` and `explanation` are null until the quiz is
 * graded — the backend withholds them, so the UI cannot leak the key even by
 * accident. Do not treat a null here as "this question has no answer".
 */
export type QuizQuestion = {
  id: number;
  order_index: number;
  kind: QuestionKind;
  prompt: string;
  options: string[];
  topic_id: number | null;
  correct_option: number | null;
  rubric: string | null;
  explanation: string | null;
  resources: QuizResource[];
};

export type QuizAnswer = {
  question_id: number;
  kind: QuestionKind | null;
  answer: string | null;
  selected_option: number | null;
  score: number | null;
  correct: boolean | null;
  feedback: string | null;
  graded_by: GradedBy | null;
  graded_at: string | null;
  grading_resources: QuizResource[];
};

export type Quiz = {
  id: number;
  session_id: number;
  topic_id: number;
  topic_name: string | null;
  title: string;
  status: QuizStatus;
  /** Null until graded — and *stays* null when grading only partly succeeded. */
  score: number | null;
  created_at: string;
  submitted_at: string | null;
  graded_at: string | null;
  grading_error: string | null;
  resources: QuizResource[];
  questions: QuizQuestion[];
  answers: QuizAnswer[];
};

export type QuizSummary = {
  id: number;
  session_id: number;
  topic_id: number;
  topic_name: string | null;
  title: string;
  status: QuizStatus;
  score: number | null;
  question_count: number;
  created_at: string;
  graded_at: string | null;
};

export type QuizListPage = {
  items: QuizSummary[];
  total: number;
  limit: number;
  offset: number;
};

/** One recorded move of a topic's confidence score, with the reason behind it. */
export type UnderstandingEvent = {
  id: number;
  topic_id: number;
  source: UnderstandingSource;
  quiz_id: number | null;
  session_id: number | null;
  previous_understanding: number;
  understanding: number;
  reason: string;
  evidence: Record<string, unknown>;
  created_at: string;
};

/** An unsaved answer. Omitting both fields is how a question stays skipped. */
export type AnswerDraft = {
  question_id: number;
  answer?: string | null;
  selected_option?: number | null;
};

export function listQuizzes(
  params: { topic_id?: number; status?: QuizStatus; limit?: number; offset?: number } = {},
): Promise<QuizListPage> {
  const qs = new URLSearchParams();
  if (params.topic_id) qs.set("topic_id", String(params.topic_id));
  if (params.status) qs.set("status", params.status);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return request<QuizListPage>(`/quizzes${query ? `?${query}` : ""}`);
}

export function getQuiz(id: number): Promise<Quiz> {
  return request<Quiz>(`/quizzes/${id}`);
}

/** Partial and repeatable — send only the questions whose answers changed. */
export function saveQuizAnswers(id: number, answers: AnswerDraft[]): Promise<Quiz> {
  return request<Quiz>(`/quizzes/${id}/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

/**
 * Grades the whole quiz. Resolves as soon as the multiple-choice half is
 * settled; if the quiz has open-ended questions the reply carries
 * `status: "grading"` and the caller has to poll `getQuiz`.
 */
export function submitQuiz(id: number): Promise<Quiz> {
  return request<Quiz>(`/quizzes/${id}/submit`, { method: "POST" });
}

export function deleteQuiz(id: number): Promise<void> {
  return request<void>(`/quizzes/${id}`, { method: "DELETE" });
}

export function listUnderstandingEvents(
  topicId: number,
  limit = 20,
): Promise<UnderstandingEvent[]> {
  return request<UnderstandingEvent[]>(`/quizzes/evidence/${topicId}?limit=${limit}`);
}

/* ---------- quiz creation (the agent flow) ---------- */

export type QuestionFormat = "multiple_choice" | "open_ended" | "mixed";

/** A topic the creation graph offers to quiz on -- only ones with material. */
export type QuizTopicOption = {
  id: number;
  name: string;
  description: string | null;
  chunk_count: number;
};

export type ChooseTopicInterrupt = {
  kind: "choose_topic";
  topics: QuizTopicOption[];
  topic_hint: string | null;
  error: string | null;
};

export type ChooseFormatInterrupt = {
  kind: "choose_format";
  topic_name: string | null;
  error: string | null;
};

/** A question as generated, before it is a real `QuizQuestion` row -- the
    answer key is shown here on purpose: this is the creator reviewing their
    own quiz before it goes live, not a student taking it. */
export type DraftQuestion = {
  kind: QuestionKind;
  prompt: string;
  options: string[];
  correct_option: number | null;
  rubric: string | null;
  explanation: string | null;
  resources: QuizResource[];
};

export type ReviewInterrupt = {
  kind: "review";
  quiz_title: string | null;
  topic_name: string | null;
  questions: DraftQuestion[];
  error: string | null;
};

export type QuizCreationInterrupt = ChooseTopicInterrupt | ChooseFormatInterrupt | ReviewInterrupt;

/**
 * One shape for every graph endpoint. `interrupt: null` with a `quiz_id` means
 * the run committed and the wizard is done.
 */
export type QuizCreationEnvelope = {
  thread_id: string;
  status: "choosing_topic" | "choosing_format" | "generating" | "reviewing" | "committed" | "abandoned";
  interrupt: QuizCreationInterrupt | null;
  quiz_id: number | null;
};

/** The three resume payloads `choose_topic`/`choose_format`/`present_quiz` understand. */
export type QuizCreationResumeAction =
  | { topic_id: number }
  | { topic_name: string }
  | { format: QuestionFormat; count?: number }
  | "start"
  | "regenerate"
  | "cancel";

/** An unfinished creation run, so a parked draft is reachable after a reload. */
export type QuizCreationRun = {
  thread_id: string;
  status: string;
  topic_hint: string | null;
  created_at: string;
  updated_at: string | null;
};

export function listUnfinishedQuizCreationRuns(): Promise<QuizCreationRun[]> {
  return request<QuizCreationRun[]>("/quizzes/create");
}

export function startQuizCreation(topicHint?: string): Promise<QuizCreationEnvelope> {
  return request<QuizCreationEnvelope>("/quizzes/create", {
    method: "POST",
    body: JSON.stringify({ topic_hint: topicHint || null }),
  });
}

export function resumeQuizCreation(
  threadId: string,
  payload: QuizCreationResumeAction,
): Promise<QuizCreationEnvelope> {
  return request<QuizCreationEnvelope>(`/quizzes/create/${threadId}/resume`, {
    method: "POST",
    body: JSON.stringify({ payload }),
  });
}

/** Reads the parked interrupt without advancing the graph — what reloading the
    wizard mid-run comes back to. */
export function getQuizCreationRun(threadId: string): Promise<QuizCreationEnvelope> {
  return request<QuizCreationEnvelope>(`/quizzes/create/${threadId}`);
}

export function abandonQuizCreation(threadId: string): Promise<void> {
  return request<void>(`/quizzes/create/${threadId}`, { method: "DELETE" });
}

/** Live version of `dashboard.html`'s Last check-in card. */
export type DashboardCheckIn = {
  topic_id: number;
  topic_name: string;
  source: "quiz" | "session" | "manual";
  previous_understanding: number;
  understanding: number;
  reason: string;
  evidence: Record<string, unknown>;
  created_at: string;
};

export type DashboardWeakTopic = {
  topic_id: number;
  name: string;
  understanding: number;
};

/** One `app.ranking.TopicSignal`, ordered highest-priority first. */
export type DashboardPriorityItem = {
  topic_id: number;
  name: string;
  understanding: number;
  band: "unknown" | "weak" | "fair" | "strong";
  reason: string;
  score: number;
};

export type Dashboard = {
  cold_start: boolean;
  check_in: DashboardCheckIn | null;
  weak_topics: DashboardWeakTopic[];
  priority_feed: DashboardPriorityItem[];
};

export function getDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/dashboard");
}

/* ---------- connections ---------- */

/** Mirrors the CHECK constraint on connections.provider. */
export type Provider = "google" | "notion" | "web";

/**
 * Mirrors the CHECK constraint on connections.status. `authorizing` is the gap
 * between uploading the client secret and pasting the code back -- persisted so
 * a reload resumes the wizard instead of restarting it.
 */
export type ConnectionStatus =
  | "disconnected"
  | "authorizing"
  | "connected"
  | "expired"
  | "error";

/** Mirrors the CHECK constraint on connection_capabilities.capability. */
export type Capability = "drive.read" | "notion.read" | "web.read";

/**
 * Note the absence of a credential field. The backend drops `secret` in its
 * repository layer, so there is no shape here it could arrive through --
 * `scopes` is what Google granted, not the grant itself.
 */
export type Connection = {
  id: number;
  provider: Provider;
  slug: string;
  display_name: string;
  account_label: string | null;
  base_url: string | null;
  auth_type: "oauth2" | "token" | "none";
  status: ConnectionStatus;
  scopes: string[];
  /** What the user permits, keyed by capability. Granted is not permitted. */
  capabilities: Partial<Record<Capability, boolean>>;
  expires_at: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  connected_at: string | null;
  created_at: string;
};

export type ConnectionsPage = {
  items: Connection[];
  /** False when CONNECTIONS_SECRET_KEY is unset: connecting is refused. */
  secrets_ready: boolean;
  /** Where credential files land, e.g. "local (/app/.hermes)". */
  hermes_destination: string;
};

export type AuthorizeStarted = {
  authorize_url: string;
  redirect_uri: string;
  connection: Connection;
};

export type DisconnectResult = {
  connection: Connection;
  /** Set when the local clear worked but the revoke or file removal did not. */
  warning: string | null;
};

export function getConnections(): Promise<ConnectionsPage> {
  return request<ConnectionsPage>("/connections");
}

export function uploadGoogleClient(file: File): Promise<AuthorizeStarted> {
  const body = new FormData();
  body.append("file", file);
  return request<AuthorizeStarted>("/connections/google/client", {
    method: "POST",
    body,
  });
}

/** `pasted` is the whole redirect URL or just the code -- the backend takes
    either, because people paste both. */
export function exchangeGoogleCode(pasted: string): Promise<Connection> {
  return request<Connection>("/connections/google/exchange", {
    method: "POST",
    body: JSON.stringify({ pasted }),
  });
}

export function setCapability(
  slug: string,
  capability: Capability,
  enabled: boolean,
): Promise<Connection> {
  return request<Connection>(`/connections/${slug}/capabilities/${capability}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export function disconnectGoogle(): Promise<DisconnectResult> {
  return request<DisconnectResult>("/connections/google", { method: "DELETE" });
}

/* ---------- presentation helpers ---------- */

/**
 * Finished, but not cleanly: the backend leaves a message on a `ready` row
 * when a stage degraded (tagging with the gateway down). Retrying is the fix,
 * so this reads differently from both "Ready" and "Failed".
 */
export function isDegraded(upload: SourceFile): boolean {
  return upload.ingest_status === "ready" && Boolean(upload.ingest_error);
}

/** Terminal states: the UI stops polling once an upload reaches one. */
export function isIngesting(status: IngestStatus): boolean {
  return status !== "ready" && status !== "failed";
}

export const INGEST_LABEL: Record<IngestStatus, string> = {
  pending: "Queued",
  extracting: "Reading",
  tagging: "Tagging topics",
  embedding: "Embedding",
  ready: "Ready",
  failed: "Failed",
};

/**
 * The status word for one row.
 *
 * A Drive file is fetched at the start of the `extracting` stage rather than
 * in a `fetching` stage of its own — widening the `ingest_status` CHECK would
 * mean rebuilding `source_files`, and `chunks` cascades on delete (see
 * migrations/009_drive_mime.sql). The distinction the user cares about is
 * still real, so it is derived here from `origin` instead of stored.
 */
export function ingestLabel(upload: SourceFile): string {
  if (isDegraded(upload)) return "Partly done";
  if (upload.origin === "drive" && upload.ingest_status === "extracting") {
    return "Fetching from Drive";
  }
  return INGEST_LABEL[upload.ingest_status];
}

/** The square file badge from topic-detail.html. */
export const UPLOAD_GLYPH: Record<UploadType, string> = {
  text: "TXT",
  pdf: "PDF",
  image: "IMG",
};

/**
 * The score badge's word. -1 is deliberately not folded into "Weak": no signal
 * yet is a different statement from a low score, and the Materials page is
 * where that distinction has to stay visible.
 */
export function understandingBand(score: number): {
  label: string;
  tone: "unknown" | "weak" | "fair" | "strong";
} {
  if (score < 0) return { label: "No signal", tone: "unknown" };
  if (score < 40) return { label: "Weak", tone: "weak" };
  if (score < 70) return { label: "Fair", tone: "fair" };
  return { label: "Strong", tone: "strong" };
}

/**
 * The goal row's status pill. `paused` from the mockup is deliberately absent:
 * SQLite's CHECK on `goals.status` only admits draft|committed|archived, so a
 * paused pill would have no state to render from. A finished goal reads as Done
 * even though it is still `committed` — percent is computed, not stored.
 */
export function goalPill(goal: GoalCard | GoalDetail): {
  label: string;
  tone: "active" | "done" | "archived";
} {
  if (goal.status === "archived") return { label: "Archived", tone: "archived" };
  if (goal.percent >= 100) return { label: "Done", tone: "done" };
  return { label: "Active", tone: "active" };
}

/** "Oct 3" — a due date is a day, and the year is noise within one term. */
export function formatDue(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The line under a goal's title: course, due date, and what is in focus. */
export function goalSubtitle(goal: GoalCard): string[] {
  const parts: string[] = [];
  parts.push(goal.course_code ?? (goal.category === "career" ? "Career goal" : "Academic goal"));
  const due = formatDue(goal.due_at);
  if (due) parts.push(due);
  if (goal.total_count) parts.push(`${goal.done_count} of ${goal.total_count} stages`);
  return parts;
}

export const SOURCE_LABEL: Record<MilestoneSource, string> = {
  materials: "From your materials",
  research: "Researched",
  user: "Added by you",
};

export function formatBytes(bytes: number | null): string | null {
  if (!bytes) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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

/* ---------- quiz presentation helpers ---------- */

export const QUIZ_STATUS_LABEL: Record<QuizStatus, string> = {
  ready: "Not started",
  in_progress: "In progress",
  grading: "Grading",
  graded: "Graded",
};

/** Terminal state: the runner stops polling once a quiz leaves `grading`. */
export function isGrading(status: QuizStatus): boolean {
  return status === "grading";
}

export function isTakeable(status: QuizStatus): boolean {
  return status === "ready" || status === "in_progress";
}

/**
 * Graded, but with no score — the backend refuses to score a quiz whose
 * open-ended half could not be graded, because the multiple-choice half is
 * systematically easier and averaging it alone would quietly inflate the topic.
 * This reads as its own state, not as a zero and not as a failure.
 */
export function isUnscored(quiz: { status: QuizStatus; score: number | null }): boolean {
  return quiz.status === "graded" && quiz.score === null;
}

/** A question counts as answered once it has a selection or non-blank text. */
export function isAnswered(answer: AnswerDraft | QuizAnswer | undefined): boolean {
  if (!answer) return false;
  if (answer.selected_option !== null && answer.selected_option !== undefined) return true;
  return Boolean(answer.answer && answer.answer.trim());
}

/** "72" or "—": an unscored quiz has no number to show. */
export function scoreDisplay(score: number | null): string {
  return score === null ? "—" : String(score);
}

/** The arrow on an evidence row. Equal scores happen and should not read as a rise. */
export function understandingDelta(event: UnderstandingEvent): {
  arrow: string;
  tone: "up" | "down" | "flat";
  /** -1 is "no signal", not a score, so the first check-in has nothing to move from. */
  first: boolean;
} {
  const first = event.previous_understanding < 0;
  if (first) return { arrow: "→", tone: "flat", first };
  const change = event.understanding - event.previous_understanding;
  if (change > 0) return { arrow: "↑", tone: "up", first };
  if (change < 0) return { arrow: "↓", tone: "down", first };
  return { arrow: "→", tone: "flat", first };
}

export function messagesOf(session: Session): ChatMessage[] {
  return session.payload?.messages ?? [];
}

export function traceOf(session: Session): TraceEntry[] {
  return session.payload?.trace ?? [];
}

/** "2.4s" / "310ms" / "" when the gateway sent no duration. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds.toFixed(1)}s`;
}

/**
 * A renamed session carries its own title; otherwise derive one from the
 * opening user message, falling back to the type label.
 */
export function titleOf(session: Session): string {
  if (session.title) return session.title;
  const first = messagesOf(session).find((m) => m.role === "user")?.content;
  if (first) return first.length > 60 ? `${first.slice(0, 60)}…` : first;
  return `${TYPE_LABEL[session.type]} session`;
}

/* ---------- connection presentation helpers ---------- */

export const CONNECTION_LABEL: Record<ConnectionStatus, string> = {
  disconnected: "Not connected",
  authorizing: "Waiting for you",
  connected: "Connected",
  expired: "Needs reconnecting",
  error: "Something went wrong",
};

/** Google's scope URLs are unreadable in a list. Falls back to the last path
    segment, so an unmapped scope still renders as something. */
export function scopeLabel(scope: string): string {
  const known: Record<string, string> = {
    "https://www.googleapis.com/auth/drive.readonly": "Read your Drive files",
    "https://www.googleapis.com/auth/drive.file": "Access files it opens",
    "https://www.googleapis.com/auth/drive": "Full Drive access",
    "https://www.googleapis.com/auth/userinfo.email": "See your email address",
    "https://www.googleapis.com/auth/userinfo.profile": "See your basic profile",
    openid: "Confirm your identity",
  };
  return known[scope] ?? scope.split("/").pop() ?? scope;
}
