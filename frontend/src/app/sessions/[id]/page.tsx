import Link from "next/link";
import { notFound } from "next/navigation";

import { Nav, type DrillNav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  getSession,
  listSessions,
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

  // Desktop D2: the sibling sessions take over the rail. Failing to load them
  // only costs the sub-list — the back link and the transcript still work.
  let siblings: Session[] = [];
  try {
    // Chats only, matching /sessions: quizzes and agent runs are their own
    // sections now and are not siblings of this transcript.
    siblings = (await listSessions({ type: "chat", limit: 12 })).items;
  } catch {
    siblings = [session];
  }

  // Only a live chat claims the full viewport; the read-only branch is a short
  // notice and looks wrong stretched over it.
  const isChat = session.type === "chat";

  const drill: DrillNav = {
    back: { href: "/sessions", label: "Sessions" },
    title: "CHATS",
    items: siblings.map((s) => ({
      href: `/sessions/${s.id}`,
      label: titleOf(s),
      active: s.id === session.id,
    })),
  };

  return (
    <>
      <Nav active="Sessions" drill={drill} />

      <div className={isChat ? `shell ${styles.page}` : "shell"}>
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

        {isChat ? (
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
