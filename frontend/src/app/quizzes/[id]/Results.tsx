"use client";

import Link from "next/link";

import { EvidenceList } from "@/components/EvidenceList";
import {
  isUnscored,
  scoreDisplay,
  understandingBand,
  type Quiz,
  type QuizAnswer,
  type QuizQuestion,
  type QuizResource,
  type UnderstandingEvent,
} from "@/lib/api";

import styles from "../quiz.module.css";

const LETTERS = "ABCDEFGHIJ";

/** Credit threshold. Mirrors `CORRECT_AT` in the backend's grading module. */
const CREDITED_AT = 70;
const PARTIAL_AT = 40;

type Tone = "correct" | "partial" | "wrong" | "pending";

/** The pill's own colours — the tone classes only set the card's left edge. */
const PILL: Record<Tone, string> = {
  correct: styles.toneCorrect,
  partial: styles.tonePartial,
  wrong: styles.toneWrong,
  pending: styles.tonePending,
};

function verdictOf(answer: QuizAnswer | undefined): { label: string; tone: Tone } {
  // Ungraded happens when grading could not finish. It is not a zero, and
  // colouring it like a wrong answer would tell the user they failed
  // something nobody ever marked.
  if (!answer || answer.score === null) return { label: "Not graded", tone: "pending" };
  if (answer.score >= CREDITED_AT) return { label: "Correct", tone: "correct" };
  if (answer.score >= PARTIAL_AT) return { label: "Partly there", tone: "partial" };
  return { label: "Missed", tone: "wrong" };
}

function ResourceChip({ resource }: { resource: QuizResource }) {
  const label =
    resource.title ||
    (resource.kind === "chunk" ? `Chunk #${resource.chunk_id}` : resource.url) ||
    "Source";

  if (resource.kind === "external" && resource.url) {
    return (
      <a
        className={styles.chip}
        href={resource.url}
        target="_blank"
        rel="noopener noreferrer"
        title={resource.url}
      >
        <span className={styles.chipKind}>Web</span>
        {label}
      </a>
    );
  }
  return (
    <span className={styles.chip} title={resource.text ?? undefined}>
      <span className={styles.chipKind}>Notes</span>
      {label}
    </span>
  );
}

function QuestionResult({
  question,
  answer,
  index,
}: {
  question: QuizQuestion;
  answer: QuizAnswer | undefined;
  index: number;
}) {
  const verdict = verdictOf(answer);
  const mcq = question.kind === "multiple_choice";

  return (
    <li className={`${styles.resultCard} ${styles[verdict.tone]}`}>
      <div className={styles.resultHead}>
        <div>
          <p className={styles.resultIndex}>
            Question {index + 1} · {mcq ? "Multiple choice" : "Open ended"}
          </p>
          <p className={styles.resultPrompt}>{question.prompt}</p>
        </div>
        <div className={styles.verdict}>
          <span className={`${styles.verdictLabel} ${PILL[verdict.tone]}`}>
            {verdict.label}
          </span>
          {/* A partial credit only means something as a number. */}
          {!mcq && answer?.score !== null && answer?.score !== undefined ? (
            <span className={styles.verdictScore}>{answer.score}/100</span>
          ) : null}
        </div>
      </div>

      {mcq ? (
        <div className={styles.resultOptions}>
          {question.options.map((option, optionIndex) => {
            const right = question.correct_option === optionIndex;
            const picked = answer?.selected_option === optionIndex;
            return (
              <div
                key={optionIndex}
                className={`${styles.resultOption} ${
                  right ? styles.optionRight : picked ? styles.optionPicked : ""
                }`}
              >
                <span className={styles.optionMark} aria-hidden="true">
                  {right ? "✓" : picked ? "✗" : LETTERS[optionIndex]}
                </span>
                <span>{option}</span>
                {picked ? (
                  <span className={styles.optionNote}>Your answer</span>
                ) : null}
              </div>
            );
          })}
          {answer?.selected_option === null || answer?.selected_option === undefined ? (
            <p className={styles.noAnswer}>You left this blank.</p>
          ) : null}
        </div>
      ) : (
        <div className={styles.yourAnswer}>
          <p className={styles.answerLabel}>Your answer</p>
          {answer?.answer && answer.answer.trim() ? (
            <p className={styles.answerText}>{answer.answer}</p>
          ) : (
            <p className={styles.noAnswer}>You left this blank.</p>
          )}
        </div>
      )}

      {/* Only Hermes's commentary. A key-graded answer's `feedback` is built
          from the question's own explanation, so rendering it here as well
          would print the same sentence twice — the ✓/✗ above already says
          which option was right. */}
      {answer?.graded_by === "hermes" && answer.feedback ? (
        <div className={styles.feedback}>
          <p className={styles.feedbackLabel}>Αθηνα&rsquo;s marking</p>
          <p className={styles.feedbackText}>{answer.feedback}</p>
        </div>
      ) : null}

      {question.explanation ? (
        <p className={styles.explanation}>
          <strong>Why:</strong> {question.explanation}
        </p>
      ) : null}

      {!mcq && question.rubric ? (
        <p className={styles.explanation}>
          <strong>What was looked for:</strong> {question.rubric}
        </p>
      ) : null}

      {/* Which sources actually backed this grade — not the whole quiz's
          reading list, only the ones the marker cited for this answer. */}
      {answer?.grading_resources?.length ? (
        <div className={styles.citedWrap}>
          <p className={styles.citedLabel}>Marked against</p>
          <div className={styles.chips}>
            {answer.grading_resources.map((resource, i) => (
              <ResourceChip key={i} resource={resource} />
            ))}
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function Results({
  quiz,
  events,
  onRegrade,
  busy,
}: {
  quiz: Quiz;
  events: UnderstandingEvent[];
  onRegrade: () => void;
  busy: boolean;
}) {
  const answers = new Map(quiz.answers.map((a) => [a.question_id, a]));
  const graded = quiz.questions.filter(
    (q) => (answers.get(q.id)?.score ?? -1) >= CREDITED_AT,
  ).length;
  const band = understandingBand(quiz.score ?? -1);
  const unscored = isUnscored(quiz);
  // Only the moves this quiz caused. The topic's older history belongs on the
  // topic page, not stapled to one check-in's result.
  const mine = events.filter((e) => e.quiz_id === quiz.id);

  return (
    <>
      {unscored ? (
        /* Nothing was lost and nothing failed permanently: the answers are
           stored and re-running grading is the fix. This deliberately does
           not read as an error, and deliberately shows no number — the
           multiple-choice half alone would flatter the result. */
        <div className={styles.unscoredBanner}>
          <p className={styles.unscoredTitle}>This quiz has not been scored</p>
          <p className={styles.unscoredText}>
            {quiz.grading_error ??
              "Grading did not finish, so no score was recorded."}{" "}
            Your answers are saved and no topic score was changed. Scoring the
            multiple-choice half on its own would have flattered the result, so
            Αθηνα recorded nothing instead.
          </p>
          <button type="button" className="btn" onClick={onRegrade} disabled={busy}>
            {busy ? "Starting…" : "Try grading again"}
          </button>
        </div>
      ) : (
        <div className={styles.hero}>
          <div
            className={styles.ring}
            style={
              {
                "--pct": quiz.score ?? 0,
                "--ring": `var(--${
                  band.tone === "strong"
                    ? "sage"
                    : band.tone === "fair"
                      ? "amber"
                      : "coral"
                })`,
              } as React.CSSProperties
            }
            role="img"
            aria-label={`Scored ${scoreDisplay(quiz.score)} out of 100`}
          >
            <div className={styles.ringInner}>
              <span className={styles.ringNum}>{scoreDisplay(quiz.score)}</span>
              <span className={styles.ringLabel}>{band.label}</span>
            </div>
          </div>

          <div className={styles.heroBody}>
            <h2 className={styles.heroHeadline}>
              {graded} of {quiz.questions.length} answered well
            </h2>
            <p className={styles.heroSub}>
              Multiple choice was marked against the key. Open-ended answers were read
              by Αθηνα against the material this quiz was written from, and scored out
              of 100 each.
            </p>
            <div className={styles.heroActions}>
              <Link href={`/materials/${quiz.topic_id}`} className="btn-inline">
                {quiz.topic_name ? `Open ${quiz.topic_name}` : "Open the topic"}
              </Link>
              <Link href="/quizzes" className="btn-inline ghost">
                All quizzes
              </Link>
            </div>
          </div>
        </div>
      )}

      {mine.length > 0 ? (
        <div className="section">
          <div className="section-head">
            <h2>What this changed</h2>
          </div>
          <EvidenceList events={mine} linkToQuiz={false} />
        </div>
      ) : null}

      <div className="section">
        <div className="section-head">
          <h2>Your answers</h2>
          <span className={styles.subtle}>
            {quiz.questions.length} question{quiz.questions.length === 1 ? "" : "s"}
          </span>
        </div>
        <ul className={styles.resultList}>
          {quiz.questions.map((question, index) => (
            <QuestionResult
              key={question.id}
              question={question}
              answer={answers.get(question.id)}
              index={index}
            />
          ))}
        </ul>
      </div>
    </>
  );
}
