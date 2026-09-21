import Link from "next/link";
import { notFound } from "next/navigation";

import { Nav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  getSession,
  messagesOf,
  titleOf,
  TYPE_LABEL,
  type Session,
} from "@/lib/api";

import { Transcript } from "./Transcript";
import styles from "./transcript.module.css";

export const metadata = { title: "Session · Αθηνα" };

export default async function SessionDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId < 1) notFound();

  let session: Session | null = null;
  let error: string | null = null;
  try {
    session = await getSession(sessionId);
  } catch (err) {
    if (err instanceof ApiError && err.message.includes("not found")) notFound();
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  if (!session) {
    return (
      <>
        <Nav active="Sessions" />
        <div className="shell">
          <div className="banner-error">{error}</div>
          <Link href="/sessions" className="btn-inline ghost">
            ← Back to sessions
          </Link>
        </div>
      </>
    );
  }

  const messages = messagesOf(session);

  return (
    <>
      {/* Desktop D2 / mobile M1: a back link replaces the nav on drill-in. */}
      <Nav />

      <div className="shell">
        <div className={styles.backRow}>
          <Link href="/sessions" className={styles.backLink}>
            ← Sessions
          </Link>
        </div>

        <div className="greeting">
          <h1>{titleOf(session)}</h1>
          <p>
            {TYPE_LABEL[session.type]} ·{" "}
            <When
              iso={session.started_at}
              suffix={`${messages.length} message${messages.length === 1 ? "" : "s"}`}
            />
          </p>
        </div>

        {session.type === "chat" ? (
          <Transcript sessionId={session.id} initialMessages={messages} />
        ) : (
          <div className="empty-state">
            <strong>Not a chat session</strong>
            {TYPE_LABEL[session.type]} sessions are read-only here.
          </div>
        )}
      </div>
    </>
  );
}
