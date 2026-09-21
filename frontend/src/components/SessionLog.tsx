import { When } from "@/components/When";
import {
  messagesOf,
  titleOf,
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
 */
export function SessionLog({
  sessions,
  showType = false,
}: {
  sessions: Session[];
  showType?: boolean;
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
                <p className={styles.title}>{titleOf(session)}</p>
                {showType ? (
                  <span className={`${styles.typePill} ${styles[tone]}`}>
                    {TYPE_LABEL[session.type]}
                  </span>
                ) : null}
              </div>
              {session.summary ? (
                <p className={styles.summary}>{session.summary}</p>
              ) : null}
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
