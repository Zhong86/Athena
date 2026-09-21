"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiError,
  getQuiz,
  isAnswered,
  listUnderstandingEvents,
  saveQuizAnswers,
  submitQuiz,
  type AnswerDraft,
  type Quiz,
  type QuizQuestion,
  type UnderstandingEvent,
} from "@/lib/api";

import { Results } from "./Results";
import styles from "../quiz.module.css";

const POLL_MS = 1500;

/** One question's answer as the form holds it. */
type Draft = { answer: string; selected: number | null };

const LETTERS = "ABCDEFGHIJ";

function draftsFrom(quiz: Quiz): Record<number, Draft> {
  const drafts: Record<number, Draft> = {};
  for (const question of quiz.questions) {
    const saved = quiz.answers.find((a) => a.question_id === question.id);
    drafts[question.id] = {
      answer: saved?.answer ?? "",
      selected: saved?.selected_option ?? null,
    };
  }
  return drafts;
}

function same(a: Draft, b: Draft): boolean {
  return a.answer === b.answer && a.selected === b.selected;
}

/** Only the field the question's kind actually uses is sent. */
function toPayload(question: QuizQuestion, draft: Draft): AnswerDraft {
  return question.kind === "multiple_choice"
    ? { question_id: question.id, selected_option: draft.selected }
    : { question_id: question.id, answer: draft.answer };
}

export function QuizView({
  initial,
  initialEvents,
}: {
  initial: Quiz;
  initialEvents: UnderstandingEvent[];
}) {
  const router = useRouter();
  const [quiz, setQuiz] = useState<Quiz>(initial);
  const [events, setEvents] = useState<UnderstandingEvent[]>(initialEvents);
  const [drafts, setDrafts] = useState<Record<number, Draft>>(() => draftsFrom(initial));
  // What the server is known to hold, so only real edits are sent.
  const [saved, setSaved] = useState<Record<number, Draft>>(() => draftsFrom(initial));
  const [started, setStarted] = useState(false);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const headingRef = useRef<HTMLDivElement>(null);

  const questions = quiz.questions;
  const total = questions.length;
  const answeredCount = useMemo(
    () =>
      questions.filter((q) =>
        isAnswered(toPayload(q, drafts[q.id] ?? { answer: "", selected: null })),
      ).length,
    [questions, drafts],
  );

  // The open-ended pass runs as a background task, so the only way to learn it
  // finished is to ask. Polling stops the moment the quiz leaves `grading`.
  useEffect(() => {
    if (quiz.status !== "grading") return;

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const fresh = await getQuiz(quiz.id);
        if (cancelled) return;

        // The trail is fetched *before* `setQuiz`, not after. `quiz` is a
        // dependency of this effect, so storing it re-runs the effect and its
        // cleanup flips `cancelled` -- a fetch awaiting behind that store
        // would resolve into a cancelled closure and be dropped on the floor.
        let trail: UnderstandingEvent[] | null = null;
        if (fresh.status === "graded") {
          try {
            trail = await listUnderstandingEvents(fresh.topic_id, 10);
          } catch {
            /* the trail is context, not the result -- skip it */
          }
          if (cancelled) return;
        }

        if (trail) setEvents(trail);
        setQuiz(fresh);
        if (fresh.status === "graded") {
          // A finished quiz is exactly what moves the topic's score, and that
          // score is server-rendered on Materials and the Dashboard.
          router.refresh();
        }
      } catch {
        /* a dropped poll is harmless: the next tick retries */
      }
    }, POLL_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [quiz, router]);

  /** Push any edited answers for `ids`. Returns false if the save failed. */
  async function flush(ids: number[]): Promise<boolean> {
    const dirty = ids.filter((id) => drafts[id] && saved[id] && !same(drafts[id], saved[id]));
    if (!dirty.length) return true;

    const byId = new Map(questions.map((q) => [q.id, q]));
    const payload = dirty
      .map((id) => {
        const question = byId.get(id);
        return question ? toPayload(question, drafts[id]) : null;
      })
      .filter((a): a is AnswerDraft => a !== null);
    if (!payload.length) return true;

    try {
      const fresh = await saveQuizAnswers(quiz.id, payload);
      setQuiz(fresh);
      setSaved((prev) => {
        const next = { ...prev };
        for (const id of dirty) next[id] = drafts[id];
        return next;
      });
      return true;
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not save that answer.",
      );
      return false;
    }
  }

  async function goTo(next: number) {
    if (busy) return;
    setError(null);
    const current = questions[step];
    setBusy(true);
    // Saving before moving is what makes a half-finished quiz survive a
    // reload; a failed save must not silently scroll past the answer.
    const ok = current ? await flush([current.id]) : true;
    setBusy(false);
    if (!ok) return;
    setStep(next);
    headingRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function onSubmit() {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      if (!(await flush(questions.map((q) => q.id)))) return;
      // An all-multiple-choice quiz comes back already graded, with no poll in
      // between; same ordering as the poll for the same reason.
      const fresh = await submitQuiz(quiz.id);
      if (fresh.status === "graded") {
        try {
          setEvents(await listUnderstandingEvents(fresh.topic_id, 10));
        } catch {
          /* the trail is context, not the result */
        }
      }
      setQuiz(fresh);
      if (fresh.status === "graded") router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not submit the quiz.");
    } finally {
      setBusy(false);
    }
  }

  /** Re-runs grading after it could not finish. Only offered when unscored. */
  async function onRegrade() {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      setQuiz(await submitQuiz(quiz.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start grading again.");
    } finally {
      setBusy(false);
    }
  }

  const banner = error ? <div className="banner-error">{error}</div> : null;

  /* ---------- graded ---------- */
  if (quiz.status === "graded") {
    return (
      <>
        {banner}
        <Results quiz={quiz} events={events} onRegrade={onRegrade} busy={busy} />
      </>
    );
  }

  /* ---------- grading in flight ---------- */
  if (quiz.status === "grading") {
    const openEnded = questions.filter((q) => q.kind === "open_ended").length;
    return (
      <>
        {banner}
        <div className={styles.gradingCard}>
          <div className={styles.spinner} aria-hidden="true" />
          <p className={styles.gradingTitle} role="status">
            Αθηνα is reading your answers
          </p>
          <p className={styles.gradingSub}>
            {openEnded === 1
              ? "One open-ended answer is being marked against the material this quiz was written from."
              : `${openEnded} open-ended answers are being marked against the material this quiz was written from.`}{" "}
            This page updates itself.
          </p>
        </div>
      </>
    );
  }

  /* ---------- not started ---------- */
  if (!started) {
    const mcq = questions.filter((q) => q.kind === "multiple_choice").length;
    const open = total - mcq;
    const resumable = answeredCount > 0;

    return (
      <>
        {banner}
        <div className={styles.startCard}>
          <div className={styles.factRow}>
            <div className={styles.fact}>
              <span className={styles.factNum}>{total}</span>
              <span className={styles.factLabel}>
                question{total === 1 ? "" : "s"}
              </span>
            </div>
            {mcq > 0 ? (
              <div className={styles.fact}>
                <span className={styles.factNum}>{mcq}</span>
                <span className={styles.factLabel}>multiple choice</span>
              </div>
            ) : null}
            {open > 0 ? (
              <div className={styles.fact}>
                <span className={styles.factNum}>{open}</span>
                <span className={styles.factLabel}>open-ended</span>
              </div>
            ) : null}
            {resumable ? (
              <div className={styles.fact}>
                <span className={styles.factNum}>{answeredCount}</span>
                <span className={styles.factLabel}>already answered</span>
              </div>
            ) : null}
          </div>

          <p className={styles.lede} style={{ marginBottom: 20 }}>
            {open > 0
              ? "There is no timer. Your answers save as you go, so you can leave and come back. Open-ended answers are marked by Αθηνα against the material this quiz was written from."
              : "There is no timer. Your answers save as you go, so you can leave and come back."}
          </p>

          <button
            type="button"
            className="btn"
            onClick={() => {
              // Resume at the first unanswered question rather than the top.
              const first = questions.findIndex(
                (q) => !isAnswered(toPayload(q, drafts[q.id])),
              );
              setStep(first === -1 ? total : first);
              setStarted(true);
            }}
          >
            {resumable ? "Resume check-in" : "Start check-in"}
          </button>
        </div>
      </>
    );
  }

  /* ---------- review step ---------- */
  const onReview = step >= total;
  const skipped = questions.filter((q) => !isAnswered(toPayload(q, drafts[q.id])));

  if (onReview) {
    return (
      <>
        <div ref={headingRef} />
        {banner}

        <div className={styles.progressWrap}>
          <div className={styles.progressHead}>
            <span className={styles.progressStep}>Review</span>
            <span>
              {answeredCount} of {total} answered
            </span>
          </div>
          <div className={styles.progressTrack}>
            <div className={styles.progressFill} style={{ width: "100%" }} />
          </div>
        </div>

        {skipped.length > 0 ? (
          /* The backend creates a blank attempt for every unanswered question
             so the score is out of the whole quiz. Saying so here means the
             user does not discover it from the number afterwards. */
          <div className={styles.skipWarning}>
            <strong>
              {skipped.length} question{skipped.length === 1 ? "" : "s"} still blank.
            </strong>{" "}
            A blank answer is marked the same as a wrong one — the score is out of all{" "}
            {total}.
          </div>
        ) : null}

        <ul className={styles.reviewList}>
          {questions.map((question, index) => {
            const draft = drafts[question.id];
            const done = isAnswered(toPayload(question, draft));
            const preview =
              question.kind === "multiple_choice"
                ? draft.selected === null
                  ? "Not answered"
                  : `${LETTERS[draft.selected]} · ${question.options[draft.selected] ?? ""}`
                : draft.answer.trim() || "Not answered";

            return (
              <li key={question.id}>
                <button
                  type="button"
                  className={`${styles.reviewRow} ${done ? styles.reviewDone : ""}`}
                  onClick={() => goTo(index)}
                >
                  <span className={styles.reviewNum}>{done ? "✓" : index + 1}</span>
                  <span className={styles.reviewBody}>
                    <span className={styles.reviewPrompt}>{question.prompt}</span>
                    <span
                      className={`${styles.reviewAnswer} ${done ? "" : styles.skipped}`}
                    >
                      {preview}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className={styles.runnerFoot}>
          <button
            type="button"
            className="btn-inline ghost"
            onClick={() => goTo(total - 1)}
            disabled={busy}
          >
            ← Back to questions
          </button>
          <div className={styles.footRight}>
            <button type="button" className="btn" onClick={onSubmit} disabled={busy}>
              {busy ? "Submitting…" : "Submit for marking"}
            </button>
          </div>
        </div>
      </>
    );
  }

  /* ---------- one question ---------- */
  const question = questions[step];
  const draft = drafts[question.id];
  const promptId = `prompt-${question.id}`;

  function update(next: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [question.id]: { ...prev[question.id], ...next } }));
  }

  return (
    <>
      <div ref={headingRef} />
      {banner}

      <div className={styles.progressWrap}>
        <div className={styles.progressHead}>
          <span className={styles.progressStep}>
            Question {step + 1} of {total}
          </span>
          <span>{answeredCount} answered</span>
        </div>
        <div className={styles.progressTrack}>
          <div
            className={styles.progressFill}
            style={{ width: `${((step + 1) / (total + 1)) * 100}%` }}
          />
        </div>
      </div>

      <div className={styles.questionCard}>
        <span className={styles.kindPill}>
          {question.kind === "multiple_choice" ? "Multiple choice" : "Open ended"}
        </span>
        <p className={styles.prompt} id={promptId}>
          {question.prompt}
        </p>

        {question.kind === "multiple_choice" ? (
          <div className={styles.options} role="radiogroup" aria-labelledby={promptId}>
            {question.options.map((option, index) => (
              <label
                key={index}
                className={`${styles.option} ${
                  draft.selected === index ? styles.optionChosen : ""
                }`}
              >
                <input
                  type="radio"
                  className={styles.optionInput}
                  name={`q-${question.id}`}
                  checked={draft.selected === index}
                  onChange={() => update({ selected: index })}
                />
                <span className={styles.optionKey} aria-hidden="true">
                  {LETTERS[index]}
                </span>
                <span className={styles.optionText}>{option}</span>
              </label>
            ))}
          </div>
        ) : (
          <>
            <textarea
              className={styles.answerBox}
              rows={7}
              value={draft.answer}
              aria-labelledby={promptId}
              placeholder="Answer in your own words…"
              onChange={(e) => update({ answer: e.target.value })}
              // Saving on blur as well as on navigation means a closed tab
              // does not cost the paragraph you just typed.
              onBlur={() => flush([question.id])}
            />
            <p className={styles.answerHint}>
              <span>
                Αθηνα marks this against the material the quiz was written from.
              </span>
              <span>
                {draft.answer.trim() ? `${draft.answer.trim().length} characters` : ""}
              </span>
            </p>
          </>
        )}
      </div>

      <div className={styles.runnerFoot}>
        <button
          type="button"
          className="btn-inline ghost"
          onClick={() => goTo(step - 1)}
          disabled={busy || step === 0}
        >
          ← Previous
        </button>
        <div className={styles.footRight}>
          {busy ? <span className={styles.saveState}>Saving…</span> : null}
          <button
            type="button"
            className="btn"
            onClick={() => goTo(step + 1)}
            disabled={busy}
          >
            {step === total - 1 ? "Review answers" : "Next question"}
          </button>
        </div>
      </div>
    </>
  );
}
