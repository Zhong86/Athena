import Link from "next/link";

import { When } from "@/components/When";
import {
  understandingDelta,
  type UnderstandingEvent,
} from "@/lib/api";
import styles from "@/styles/evidence.module.css";

const SOURCE_LABEL: Record<UnderstandingEvent["source"], string> = {
  quiz: "Quiz",
  session: "Session",
  manual: "Manual",
};

/**
 * The recorded moves of a topic's confidence score, newest first.
 *
 * `linkToQuiz` is off on the quiz results screen, where every row already
 * belongs to the quiz you are looking at.
 */
export function EvidenceList({
  events,
  linkToQuiz = true,
}: {
  events: UnderstandingEvent[];
  linkToQuiz?: boolean;
}) {
  return (
    <ul className={styles.eventList}>
      {events.map((event) => {
        const delta = understandingDelta(event);
        return (
          <li key={event.id} className={styles.eventRow}>
            <div className={`${styles.move} ${styles[delta.tone]}`}>
              {/* A first check-in has no "from": -1 is the absence of a score,
                  and rendering it as a number would invent a starting point. */}
              {delta.first ? null : (
                <span className={styles.from}>{event.previous_understanding}</span>
              )}
              <span className={styles.arrow} aria-hidden="true">
                {delta.arrow}
              </span>
              <span className={styles.to}>{event.understanding}</span>
            </div>

            <div className={styles.eventBody}>
              <p className={styles.reason}>{event.reason}</p>
              <div className={styles.eventMeta}>
                <span className={`${styles.sourcePill} ${styles[event.source]}`}>
                  {SOURCE_LABEL[event.source]}
                </span>
                <When iso={event.created_at} />
                {linkToQuiz && event.quiz_id ? (
                  <Link href={`/quizzes/${event.quiz_id}`} className={styles.eventLink}>
                    See the quiz
                  </Link>
                ) : null}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
