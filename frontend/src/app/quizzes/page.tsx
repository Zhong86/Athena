import Link from "next/link";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  isTakeable,
  listQuizzes,
  QUIZ_STATUS_LABEL,
  scoreDisplay,
  understandingBand,
  type QuizListPage,
  type QuizSummary,
} from "@/lib/api";

import styles from "./quiz.module.css";

export const metadata = { title: "Quizzes · Αθηνα" };

function QuizRow({ quiz }: { quiz: QuizSummary }) {
  const open = isTakeable(quiz.status);
  const band = understandingBand(quiz.score ?? -1);
  const unscored = quiz.status === "graded" && quiz.score === null;

  const parts = [
    quiz.topic_name ?? "Untagged topic",
    `${quiz.question_count} question${quiz.question_count === 1 ? "" : "s"}`,
  ];

  return (
    <li>
      <Link
        href={`/quizzes/${quiz.id}`}
        className={`${styles.quizCard} ${open ? styles.quizCardOpen : ""}`}
      >
        <div className={styles.quizMeta}>
          <p className={styles.quizTitle}>{quiz.title}</p>
          <p className={styles.quizSub}>
            {parts.join(" · ")} ·{" "}
            <When iso={quiz.graded_at ?? quiz.created_at} />
          </p>
        </div>

        <div className={styles.quizAside}>
          {quiz.status === "graded" ? null : (
            <span className={`${styles.statusPill} ${styles[quiz.status]}`}>
              {QUIZ_STATUS_LABEL[quiz.status]}
            </span>
          )}
          {quiz.status === "graded" ? (
            unscored ? (
              /* Not a zero. The backend withholds the score when grading only
                 partly succeeded, and a "0" here would be a lie. */
              <span className={`${styles.scoreChip} ${styles.unscored}`}>
                Not scored
              </span>
            ) : (
              <span className={`${styles.scoreChip} ${styles[band.tone]}`}>
                {scoreDisplay(quiz.score)}
              </span>
            )
          ) : null}
        </div>
      </Link>
    </li>
  );
}

export default async function QuizzesPage() {
  let page: QuizListPage | null = null;
  let error: string | null = null;
  try {
    page = await listQuizzes({ limit: 100 });
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  const open = page?.items.filter((q) => isTakeable(q.status)) ?? [];
  // `grading` belongs with the finished ones: there is nothing left to answer.
  const done = page?.items.filter((q) => !isTakeable(q.status)) ?? [];

  return (
    <>
      <Nav active="Quizzes" />

      <div className="shell">
        <div className="greeting">
          <h1>Every check-in you&rsquo;ve taken.</h1>
          <p>
            Multiple choice is marked against the key; open-ended answers are read by
            Αθηνα against the material the quiz was written from. Each one moves the
            confidence score on the topics it covered.
          </p>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        {open.length > 0 ? (
          <div className="section">
            <div className="section-head">
              <h2>Waiting for you</h2>
              <span className={styles.subtle}>
                {open.length} to take
              </span>
            </div>
            <ul className={styles.quizList}>
              {open.map((quiz) => (
                <QuizRow key={quiz.id} quiz={quiz} />
              ))}
            </ul>
          </div>
        ) : null}

        <div className="section">
          <div className="section-head">
            <h2>{open.length > 0 ? "Completed" : "Quizzes"}</h2>
            {page ? (
              <span className={styles.subtle}>
                {page.total} {page.total === 1 ? "quiz" : "quizzes"}
              </span>
            ) : null}
          </div>

          {done.length > 0 ? (
            <ul className={styles.quizList}>
              {done.map((quiz) => (
                <QuizRow key={quiz.id} quiz={quiz} />
              ))}
            </ul>
          ) : open.length === 0 && !error ? (
            /* Generation is still Step 4, so this does not offer a "new quiz"
               button that would have nothing to call. */
            <div className="empty-state">
              <strong>No quizzes yet</strong>
              Once Αθηνα can generate check-ins from your material, every one you take
              will be listed here with its score and what it changed.
            </div>
          ) : null}
        </div>

        <p className="footnote">Feeds Materials confidence scores</p>
      </div>

      <ChatLauncher />
    </>
  );
}
