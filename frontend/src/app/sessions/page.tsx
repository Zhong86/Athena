import { GetStartedGuide } from "@/components/GetStartedGuide";
import { Nav } from "@/components/Nav";
import { ApiError, listSessions, type SessionPage } from "@/lib/api";

import styles from "@/styles/log.module.css";

import { startChat } from "./actions";
import { ChatRow } from "./ChatRow";

export const metadata = { title: "Sessions · Αθηνα" };

/**
 * Chats only. Quizzes live at /quizzes and the agent's own runs at
 * /knowledge-sync, so this page no longer needs a type filter or the mixed
 * timeline framing it used to carry.
 */

export default async function SessionsPage() {
  let page: SessionPage | null = null;
  let error: string | null = null;
  try {
    page = await listSessions({ type: "chat", limit: 50 });
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  return (
    <>
      <Nav active="Sessions" />

      <div className="shell">
        <div className="greeting">
          <h1>Your conversations with Αθηνα.</h1>
          <p>
            Every chat you have had, in order. What Αθηνα picks up from them feeds your
            topic confidence scores.
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
          <div className="section-head">
            <h2>Chats</h2>
            {page ? (
              <span className={styles.when}>
                {page.total} {page.total === 1 ? "chat" : "chats"}
              </span>
            ) : null}
          </div>

          <form action={startChat} className={styles.newSessionRow}>
            <button type="submit" className="btn">
              New chat
            </button>
          </form>

          {page && page.items.length > 0 ? (
            <ul className={styles.logList}>
              {page.items.map((session) => (
                <ChatRow key={session.id} session={session} />
              ))}
            </ul>
          ) : !error ? (
            <div className="empty-state">
              <strong>No chats yet</strong>
              Start one and it will show up here.
            </div>
          ) : null}
        </div>

        <p className="footnote">Feeds Materials confidence scores and Goal roadmap</p>
      </div>

      <GetStartedGuide />
    </>
  );
}
