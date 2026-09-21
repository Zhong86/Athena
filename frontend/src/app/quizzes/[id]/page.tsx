import Link from "next/link";
import { notFound } from "next/navigation";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav, type DrillNav } from "@/components/Nav";
import {
  ApiError,
  getQuiz,
  listQuizzes,
  listUnderstandingEvents,
  QUIZ_STATUS_LABEL,
  scoreDisplay,
  type Quiz,
  type QuizSummary,
  type UnderstandingEvent,
} from "@/lib/api";

import { QuizView } from "./QuizView";
import styles from "../quiz.module.css";

export const metadata = { title: "Quiz · Αθηνα" };

export default async function QuizPage(props: { params: Promise<{ id: string }> }) {
  const { id } = await props.params;
  const quizId = Number(id);
  if (!Number.isInteger(quizId) || quizId < 1) notFound();

  let quiz: Quiz | null = null;
  let error: string | null = null;
  try {
    quiz = await getQuiz(quizId);
  } catch (err) {
    if (err instanceof ApiError && err.message.includes("not found")) notFound();
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  if (!quiz) {
    return (
      <>
        <Nav active="Quizzes" />
        <div className="shell">
          <div className="banner-error">{error}</div>
          <Link href="/quizzes" className="btn-inline ghost">
            ← Back to quizzes
          </Link>
        </div>
      </>
    );
  }

  // Both are nice-to-have. The rail and the evidence trail are context around
  // the quiz, so losing either should cost that block and not the page.
  const [siblings, events] = await Promise.all([
    listQuizzes({ limit: 50 }).catch(() => null),
    // Only meaningful once something has been scored; asking earlier just
    // returns an empty list, so this is gated to keep the request honest.
    quiz.status === "graded"
      ? listUnderstandingEvents(quiz.topic_id, 10).catch((): UnderstandingEvent[] => [])
      : Promise.resolve<UnderstandingEvent[]>([]),
  ]);

  const rows: QuizSummary[] = siblings?.items ?? [];
  const drill: DrillNav = {
    back: { href: "/quizzes", label: "Quizzes" },
    title: "QUIZZES",
    items: (rows.length
      ? rows
      : [
          {
            id: quiz.id,
            title: quiz.title,
            status: quiz.status,
            score: quiz.score,
          } as QuizSummary,
        ]
    ).map((row) => ({
      href: `/quizzes/${row.id}`,
      label: row.title,
      meta:
        row.status === "graded"
          ? row.score === null
            ? "Not scored"
            : scoreDisplay(row.score)
          : QUIZ_STATUS_LABEL[row.status],
      active: row.id === quiz.id,
    })),
  };

  return (
    <>
      <Nav active="Quizzes" drill={drill} />

      <div className="shell">
        <div className={styles.quizHead}>
          <p className={styles.eyebrow}>
            {quiz.topic_name ? (
              <Link href={`/materials/${quiz.topic_id}`} className={styles.eyebrowLink}>
                {quiz.topic_name}
              </Link>
            ) : (
              "Check-in"
            )}
          </p>
          <h1 className={styles.title}>{quiz.title}</h1>
        </div>

        {/* Everything past the heading is stateful — answering, the grading
            poll, and the result all live on the same screen. */}
        <QuizView initial={quiz} initialEvents={events} />
      </div>

      <ChatLauncher />
    </>
  );
}
