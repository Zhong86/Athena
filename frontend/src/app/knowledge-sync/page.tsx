import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import { SessionLog } from "@/components/SessionLog";
import { ApiError, listSessions, type Session } from "@/lib/api";
import styles from "@/styles/log.module.css";

export const metadata = { title: "Knowledge-Sync · Αθηνα" };

export default async function KnowledgeSyncPage() {
  let sessions: Session[] = [];
  let error: string | null = null;

  try {
    // Findings and agent actions are one idea -- work Αθηνα did without being
    // asked -- but two rows in the type column, so they are fetched separately
    // and merged. The backend filter takes a single type.
    const [findings, actions] = await Promise.all([
      listSessions({ type: "cron", limit: 50 }),
      listSessions({ type: "agent_action", limit: 50 }),
    ]);
    sessions = [...findings.items, ...actions.items].sort((a, b) =>
      b.started_at.localeCompare(a.started_at),
    );
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  return (
    <>
      <Nav active="Knowledge-Sync" />

      <div className="shell">
        <div className="greeting">
          <h1>What Αθηνα did on its own.</h1>
          <p>
            Scheduled runs across your material and goals — and the actions it
            took off the back of them. Nothing here was started by you.
          </p>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        <div className="section">
          <div className="section-head">
            <h2>Runs</h2>
            {sessions.length ? (
              <span className={styles.when}>
                {sessions.length} {sessions.length === 1 ? "run" : "runs"}
              </span>
            ) : null}
          </div>

          {sessions.length ? (
            <SessionLog
              sessions={sessions}
              showType
              showTrace
              hrefBase="/knowledge-sync"
            />
          ) : !error ? (
            <div className="empty-state">
              <strong>Nothing synced yet</strong>
              When the scheduled jobs land, every run and every action Αθηνα takes will
              be recorded here.
            </div>
          ) : null}
        </div>

        <p className="footnote">Feeds your Dashboard priorities and Goal roadmap</p>
      </div>

      <ChatLauncher />
    </>
  );
}
