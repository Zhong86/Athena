import Link from "next/link";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  isTakeable,
  listQuizzes,
  listUnfinishedQuizCreationRuns,
  QUIZ_STATUS_LABEL,
  scoreDisplay,
  understandingBand,
  type QuizCreationRun,
  type QuizListPage,
  type QuizSummary,
} from "@/lib/api";

import styles from "./quiz.module.css";

export const metadata = { title: "Quizzes · Αθηνα" };

const CREATION_STEP_LABEL: Record<string, string> = {
  choosing_topic: "Waiting on you to pick a topic",
  choosing_format: "Waiting on you to pick a format",
  reviewing: "Waiting on your review",
};

function DraftRow({ run }: { run: QuizCreationRun }) {
  return (
    <li>
      <Link href={`/quizzes/new?thread=${run.thread_id}`} className={styles.quizCard}>
        <div className={styles.quizMeta}>
          <p className={styles.quizTitle}>{run.topic_hint || "New quiz"}</p>
          <p className={styles.quizSub}>
            {CREATION_STEP_LABEL[run.status] ?? "In progress"} · started{" "}
            <When iso={run.created_at} />
          </p>
        </div>
        <div className={styles.quizAside}>
          <span className={`${styles.statusPill} ${styles.ready}`}>Draft</span>
        </div>
      </Link>
    </li>
  );
}

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
  let drafts: QuizCreationRun[] = [];
  let error: string | null = null;
  try {
    [page, drafts] = await Promise.all([
      listQuizzes({ limit: 100 }),
      listUnfinishedQuizCreationRuns(),
    ]);
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
        <div className={styles.headRow}>
          <div className="greeting">
            <h1>Every check-in you&rsquo;ve taken.</h1>
            <p>
              Multiple choice is marked against the key; open-ended answers are read by
              Αθηνα against the material the quiz was written from. Each one moves the
              confidence score on the topics it covered.
            </p>
          </div>
          <Link href="/quizzes/new" className="btn">
            + New quiz
          </Link>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        {drafts.length ? (
          <div className="section">
            <div className="section-head">
              <h2>In progress</h2>
              <span className={styles.subtle}>Not generated yet</span>
            </div>
            <ul className={styles.quizList}>
              {drafts.map((run) => (
                <DraftRow key={run.thread_id} run={run} />
              ))}
            </ul>
          </div>
        ) : null}

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
          ) : open.length === 0 && drafts.length === 0 && !error ? (
            <Link href="/quizzes/new" className={styles.newQuizCard}>
              <div className={styles.plusIcon} aria-hidden="true">
                +
              </div>
              <div>
                <strong>Build your first quiz</strong>
                <p>
                  Pick a topic from your Materials and Αθηνα will write the questions,
                  grounded in what you uploaded.
                </p>
              </div>
            </Link>
          ) : null}
        </div>

        <p className="footnote">Feeds Materials confidence scores</p>
      </div>

      <ChatLauncher />
    </>
  );
}
