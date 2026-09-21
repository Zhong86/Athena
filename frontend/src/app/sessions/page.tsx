import Link from "next/link";

import { Nav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  listSessions,
  messagesOf,
  titleOf,
  TYPE_GLYPH,
  TYPE_LABEL,
  TYPE_TONE,
  type Session,
  type SessionPage,
  type SessionType,
} from "@/lib/api";

import { startChat } from "./actions";
import styles from "./sessions.module.css";

export const metadata = { title: "Sessions · Αθηνα" };

const FILTERS: { label: string; type?: SessionType; swatch?: string }[] = [
  { label: "All activity" },
  { label: "Chats", type: "chat", swatch: "var(--indigo)" },
  { label: "Quizzes", type: "quiz", swatch: "var(--amber-ink)" },
  // The mockup calls cron results "Findings".
  { label: "Findings", type: "cron", swatch: "var(--sage)" },
  { label: "Agent actions", type: "agent_action", swatch: "var(--coral)" },
];

function isSessionType(value: string | undefined): value is SessionType {
  return (
    value === "chat" || value === "quiz" || value === "cron" || value === "agent_action"
  );
}

function LogRow({ session }: { session: Session }) {
  const tone = TYPE_TONE[session.type];
  const messageCount = messagesOf(session).length;

  return (
    <li className={styles.logItem}>
      <div className={`${styles.logMarker} ${styles[tone]}`}>
        {TYPE_GLYPH[session.type]}
      </div>
      <div className={styles.meta}>
        <div className={styles.titleLine}>
          <p className={styles.title}>{titleOf(session)}</p>
          <span className={`${styles.typePill} ${styles[tone]}`}>
            {TYPE_LABEL[session.type]}
          </span>
        </div>
        {session.summary ? <p className={styles.summary}>{session.summary}</p> : null}
        <div className={styles.rowBottom}>
          <span className={styles.when}>
            <When
              iso={session.started_at}
              suffix={
                messageCount
                  ? `${messageCount} message${messageCount === 1 ? "" : "s"}`
                  : undefined
              }
            />
          </span>
          {session.type === "chat" ? (
            <Link href={`/sessions/${session.id}`} className="btn-inline ghost">
              {messageCount ? "Resume" : "Open"}
            </Link>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export default async function SessionsPage(props: {
  searchParams: Promise<{ type?: string }>;
}) {
  const { type } = await props.searchParams;
  const active = isSessionType(type) ? type : undefined;

  let page: SessionPage | null = null;
  let error: string | null = null;
  try {
    page = await listSessions({ type: active, limit: 50 });
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  return (
    <>
      <Nav active="Sessions" />

      <div className="shell">
        <div className="greeting">
          <h1>Everything Αθηνα has done with you.</h1>
          <p>
            Chats, quizzes, and the findings that came out of them — in one timeline, not
            three separate logs.
          </p>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        {/* The mockup shows behaviour-derived facts about the student here.
            No data source is wired up for those yet, so this stays an honest
            empty state rather than placeholder copy. */}
        <div className="section">
          <div className="section-head">
            <h2>What Αθηνα has noticed about how you learn</h2>
          </div>
          <div className="empty-state">
            <strong>Nothing noticed yet</strong>
            Standing facts about how you learn appear here once there are enough
            sessions to draw from.
          </div>
        </div>

        <div className="section">
          <div className={styles.filterTabs}>
            {FILTERS.map((filter) => {
              const isActive = filter.type === active;
              return (
                <Link
                  key={filter.label}
                  href={filter.type ? `/sessions?type=${filter.type}` : "/sessions"}
                  className={`${styles.filterTab} ${isActive ? styles.filterTabActive : ""}`}
                  aria-current={isActive ? "true" : undefined}
                >
                  {filter.swatch ? (
                    <span
                      className={styles.swatch}
                      style={{ background: filter.swatch }}
                    />
                  ) : null}
                  {filter.label}
                </Link>
              );
            })}
          </div>

          <form action={startChat} className={styles.newSessionRow}>
            <button type="submit" className="btn">
              New chat
            </button>
          </form>
        </div>

        <div className="section">
          <div className="section-head">
            <h2>Timeline</h2>
            {page ? (
              <span className={styles.when}>
                {page.total} {page.total === 1 ? "session" : "sessions"}
              </span>
            ) : null}
          </div>

          {page && page.items.length > 0 ? (
            <ul className={styles.logList}>
              {page.items.map((session) => (
                <LogRow key={session.id} session={session} />
              ))}
            </ul>
          ) : !error ? (
            <div className="empty-state">
              <strong>
                {active ? `No ${TYPE_LABEL[active].toLowerCase()} sessions yet` : "No sessions yet"}
              </strong>
              Start a chat and it will show up here alongside quizzes and agent
              activity.
            </div>
          ) : null}
        </div>

        <p className="footnote">Feeds Materials confidence scores and Goal roadmap</p>
      </div>
    </>
  );
}
