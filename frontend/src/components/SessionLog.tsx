import Link from "next/link";

import { SessionTrace } from "@/components/SessionTrace";
import { When } from "@/components/When";
import {
  messagesOf,
  titleOf,
  traceOf,
  TYPE_GLYPH,
  TYPE_LABEL,
  TYPE_TONE,
  type Session,
} from "@/lib/api";
import styles from "@/styles/log.module.css";

/**
 * A read-only list of sessions, used by /quizzes and /knowledge-sync.
 * /sessions has its own row because a chat is the one type you can reopen.
 *
 * `showType` is off where the page already is the type (quizzes) and on where
 * one page covers several (knowledge-sync holds both cron findings and agent
 * actions).
 *
 * `showTrace` is on only for Knowledge-Sync: an unprompted run has to justify
 * itself, so its working is worth the room. A quiz you started does not.
 *
 * `hrefBase` turns the title into a link to `${hrefBase}/${id}`. Left unset,
 * rows stay inert -- /quizzes routes through its own row component.
 */
export function SessionLog({
  sessions,
  showType = false,
  showTrace = false,
  hrefBase,
}: {
  sessions: Session[];
  showType?: boolean;
  showTrace?: boolean;
  hrefBase?: string;
}) {
  return (
    <ul className={styles.logList}>
      {sessions.map((session) => {
        const tone = TYPE_TONE[session.type];
        return (
          <li key={session.id} className={styles.logItem}>
            <div className={`${styles.logMarker} ${styles[tone]}`}>
              {TYPE_GLYPH[session.type]}
            </div>
            <div className={styles.meta}>
              <div className={styles.titleLine}>
                {hrefBase ? (
                  <Link href={`${hrefBase}/${session.id}`} className={styles.rowLink}>
                    {titleOf(session)}
                  </Link>
                ) : (
                  <p className={styles.title}>{titleOf(session)}</p>
                )}
                {showType ? (
                  <span className={`${styles.typePill} ${styles[tone]}`}>
                    {TYPE_LABEL[session.type]}
                  </span>
                ) : null}
              </div>
              {session.summary ? (
                <p className={styles.summary}>{session.summary}</p>
              ) : null}
              {showTrace ? <SessionTrace trace={traceOf(session)} /> : null}
              <span className={styles.when}>
                <When
                  iso={session.started_at}
                  suffix={
                    messagesOf(session).length
                      ? `${messagesOf(session).length} messages`
                      : undefined
                  }
                />
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
