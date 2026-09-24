"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  abandonQuizCreation,
  ApiError,
  getQuizCreationRun,
  resumeQuizCreation,
  startQuizCreation,
  type ChooseFormatInterrupt,
  type ChooseTopicInterrupt,
  type QuestionFormat,
  type QuizCreationEnvelope,
  type QuizCreationResumeAction,
  type ReviewInterrupt,
} from "@/lib/api";

import styles from "../quiz.module.css";

const FORMAT_OPTIONS: { value: QuestionFormat; label: string; hint: string }[] = [
  { value: "multiple_choice", label: "Multiple choice", hint: "Graded instantly against a key" },
  { value: "open_ended", label: "Open-ended", hint: "Read and scored by Αθηνα" },
  { value: "mixed", label: "Both", hint: "A mix of the two" },
];

const COUNT_OPTIONS = [3, 5, 8, 10];

// router.replace() below is not guaranteed to land before this component
// remounts (its RSC fetch can abort and take the segment down with it), so
// the thread id a fresh start produces is mirrored here -- a durable place a
// remounted instance can recover it from when the URL still shows no
// `?thread=`, instead of mistaking the remount for a brand new visit and
// opening a second graph thread.
const PENDING_THREAD_KEY = "athena:quiz-creation:pending-thread";

export function QuizWizard({ threadId }: { threadId: string | null }) {
  const router = useRouter();
  const [envelope, setEnvelope] = useState<QuizCreationEnvelope | null>(null);
  // Reopening a saved draft or starting a fresh run both begin mid-fetch, so
  // the flow is busy on first paint either way.
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pickedTopic, setPickedTopic] = useState<number | null>(null);
  const [format, setFormat] = useState<QuestionFormat>("multiple_choice");
  const [count, setCount] = useState(5);

  const interrupt = envelope?.interrupt ?? null;
  const chooseTopic = interrupt?.kind === "choose_topic" ? (interrupt as ChooseTopicInterrupt) : null;
  const chooseFormat = interrupt?.kind === "choose_format" ? (interrupt as ChooseFormatInterrupt) : null;
  const review = interrupt?.kind === "review" ? (interrupt as ReviewInterrupt) : null;

  // Guards the fresh-start branch below against React StrictMode's dev-only
  // double effect invocation: startQuizCreation() is not idempotent (each
  // call opens a new graph thread and writes a quiz_creation_runs row), so
  // firing it twice on one mount would park two drafts for one visit.
  const started = useRef(false);

  // A resumed run re-reads its parked interrupt rather than advancing the
  // graph, so reopening the "Save and exit" link lands exactly where the
  // student left. A fresh visit (no thread in the URL) starts the graph
  // itself -- there is no free-text step before it, so there is nothing to
  // wait on the student for. `pendingThreadId` covers the case where a
  // previous mount already started one but the URL never caught up with it.
  useEffect(() => {
    let cancelled = false;
    const pendingThreadId = threadId ?? sessionStorage.getItem(PENDING_THREAD_KEY);

    if (pendingThreadId) {
      if (!threadId) {
        // Recovering from a remount that lost the URL update -- put the
        // thread id back so a reload or another remount finds it too.
        router.replace(`/quizzes/new?thread=${pendingThreadId}`);
      }
      getQuizCreationRun(pendingThreadId)
        .then((fresh) => {
          if (!cancelled) setEnvelope(fresh);
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof ApiError ? err.message : "Could not reopen that draft.");
          }
        })
        .finally(() => {
          if (!cancelled) setBusy(false);
        });
    } else if (!started.current) {
      started.current = true;
      // Not guarded by `cancelled`: React Strict Mode fires this effect
      // twice in dev, cleaning up the first invocation immediately. The
      // `started` ref survives both invocations, so it already guarantees
      // only one request is ever sent -- whichever invocation's closure
      // that request belongs to, its result is the only one coming and has
      // to be applied here, or the page is stuck on "Looking at your
      // materials..." forever with a draft already sitting on the server.
      startQuizCreation()
        .then((fresh) => {
          sessionStorage.setItem(PENDING_THREAD_KEY, fresh.thread_id);
          router.replace(`/quizzes/new?thread=${fresh.thread_id}`);
          land(fresh);
        })
        .catch((err) => {
          setError(err instanceof ApiError ? err.message : "Could not start a new quiz.");
        })
        .finally(() => {
          setBusy(false);
        });
    }

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  function land(fresh: QuizCreationEnvelope) {
    if (fresh.quiz_id && !fresh.interrupt) {
      sessionStorage.removeItem(PENDING_THREAD_KEY);
      router.push(`/quizzes/${fresh.quiz_id}`);
      return;
    }
    setEnvelope(fresh);
  }

  async function resume(payload: QuizCreationResumeAction) {
    const id = envelope?.thread_id ?? threadId;
    if (!id || busy) return;
    setBusy(true);
    setError(null);
    try {
      land(await resumeQuizCreation(id, payload));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send that.");
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    const id = envelope?.thread_id ?? threadId;
    if (!id) {
      router.push("/quizzes");
      return;
    }
    if (!window.confirm("Discard this draft quiz? Nothing has been saved yet.")) return;
    try {
      await abandonQuizCreation(id);
    } catch {
      /* an unreachable backend should not trap the student in the flow */
    }
    sessionStorage.removeItem(PENDING_THREAD_KEY);
    router.push("/quizzes");
  }

  const step = review ? 3 : chooseFormat ? 2 : 1;
  const stepper = (
    <div className={styles.stepper}>
      {[
        { n: 1, label: "Pick a topic" },
        { n: 2, label: "Format & length" },
        { n: 3, label: "Review" },
      ].map(({ n, label }, index) => (
        <div key={n} style={{ display: "flex", alignItems: "center", flex: 1, gap: 8 }}>
          <div
            className={`${styles.step} ${
              n < step ? styles.stepDone : n === step ? styles.stepCurrent : ""
            }`}
          >
            <div className={styles.stepNum}>{n < step ? "✓" : n}</div>
            <span className={styles.stepLabel}>{label}</span>
          </div>
          {index < 2 ? (
            <div className={`${styles.stepLine} ${n < step ? styles.stepLineDone : ""}`} />
          ) : null}
        </div>
      ))}
    </div>
  );

  return (
    <>
      <div className={styles.exitBar}>
        <div className={styles.exitInner}>
          <span className="wordmark">Αθηνα</span>
          <Link href="/quizzes" className={styles.exitLink}>
            Save and exit
          </Link>
        </div>
      </div>

      <div className={styles.flow}>
        {stepper}

        {error ? <div className="banner-error">{error}</div> : null}

        {/* ---------------- step 1: choose a topic --------------------------- */}
        {chooseTopic ? (
          <div className={styles.stage}>
            <div className={styles.stageHead}>
              <h1>What should this quiz cover?</h1>
              <p>
                Pick a topic from your uploaded material — every question is written
                from it, not from general knowledge.
              </p>
            </div>

            {chooseTopic.error ? <div className="banner-error">{chooseTopic.error}</div> : null}

            <ul className={styles.topicPickList}>
              {chooseTopic.topics.map((topic) => (
                <li key={topic.id}>
                  <button
                    type="button"
                    className={`${styles.topicPick} ${
                      pickedTopic === topic.id ? styles.topicPickChosen : ""
                    }`}
                    onClick={() => setPickedTopic(topic.id)}
                  >
                    <span className={styles.topicPickName}>{topic.name}</span>
                    <span className={styles.topicPickMeta}>
                      {topic.chunk_count} chunk{topic.chunk_count === 1 ? "" : "s"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>

            <div className={styles.stageFooter}>
              <button type="button" className="btn-inline ghost" onClick={discard}>
                Discard draft
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy || pickedTopic === null}
                onClick={() => resume({ topic_id: pickedTopic! })}
              >
                {busy ? "Working…" : "Continue"}
              </button>
            </div>
          </div>
        ) : null}

        {/* ---------------- step 2: format + length --------------------------- */}
        {chooseFormat ? (
          <div className={styles.stage}>
            <div className={styles.stageHead}>
              <h1>Multiple choice, open-ended, or both?</h1>
              <p>
                {chooseFormat.topic_name ? (
                  <>
                    Quizzing you on <strong>{chooseFormat.topic_name}</strong>.
                  </>
                ) : null}
              </p>
            </div>

            {chooseFormat.error ? <div className="banner-error">{chooseFormat.error}</div> : null}

            <div className={styles.formatGrid}>
              {FORMAT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`${styles.formatOption} ${
                    format === opt.value ? styles.formatOptionChosen : ""
                  }`}
                  onClick={() => setFormat(opt.value)}
                >
                  <span className={styles.formatLabel}>{opt.label}</span>
                  <span className={styles.formatHint}>{opt.hint}</span>
                </button>
              ))}
            </div>

            <div className={styles.countRow}>
              <span className={styles.fieldLabel}>How many questions?</span>
              <div className={styles.countChips}>
                {COUNT_OPTIONS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`${styles.countChip} ${count === n ? styles.countChipChosen : ""}`}
                    onClick={() => setCount(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            <div className={styles.stageFooter}>
              <button type="button" className="btn-inline ghost" onClick={discard}>
                Discard draft
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => resume({ format, count })}
              >
                {busy ? "Writing questions…" : "Continue"}
              </button>
            </div>

            {busy ? (
              <div className={styles.working}>
                <span className={styles.miniSpinner} aria-hidden="true" />
                Writing questions grounded in your material…
              </div>
            ) : null}
          </div>
        ) : null}

        {/* ---------------- step 3: review before it goes live ----------------- */}
        {review ? (
          <div className={styles.stage}>
            <div className={styles.stageHead}>
              <h1>{review.quiz_title || "Your quiz"}</h1>
              <p>
                {review.questions.length} question{review.questions.length === 1 ? "" : "s"}
                {review.topic_name ? ` on ${review.topic_name}` : ""}. Take a look before it
                goes live — regenerate if this isn&rsquo;t the quiz you wanted.
              </p>
            </div>

            {review.error ? <div className="banner-error">{review.error}</div> : null}

            <ul className={styles.reviewList}>
              {review.questions.map((question, index) => (
                <li key={index} className={styles.reviewItem}>
                  <div className={styles.reviewNum} aria-hidden="true">
                    {index + 1}
                  </div>
                  <div className={styles.reviewBody}>
                    <p className={styles.reviewTitle}>{question.prompt}</p>
                    {question.kind === "multiple_choice" ? (
                      <ul className={styles.draftOptions}>
                        {question.options.map((option, oi) => (
                          <li
                            key={oi}
                            className={
                              oi === question.correct_option ? styles.draftOptionCorrect : ""
                            }
                          >
                            {option}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className={styles.reviewReason}>
                        {question.rubric || "Graded by Αθηνα against your material."}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>

            {busy ? (
              <div className={styles.working}>
                <span className={styles.miniSpinner} aria-hidden="true" />
                Working…
              </div>
            ) : null}

            <div className={styles.stageFooter}>
              <button type="button" className="btn-inline ghost" onClick={discard}>
                Discard draft
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="btn-inline"
                  disabled={busy}
                  onClick={() => resume("regenerate")}
                >
                  Regenerate
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => resume("start")}>
                  Start quiz
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {/* A run that is neither interrupted nor committed: the graph died
            mid-node, usually with the gateway down. There is nothing to
            resume into, so say so instead of rendering an empty step. */}
        {envelope && !interrupt && !envelope.quiz_id && !busy ? (
          <>
            <div className="empty-state">
              <strong>This draft stopped partway</strong>
              Αθηνα could not finish building the quiz — most often because the gateway
              was unreachable. Starting over is the fix; nothing was saved.
            </div>
            <div className={styles.stageFooter}>
              <button type="button" className="btn-inline ghost" onClick={discard}>
                Discard draft
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setEnvelope(null);
                  setError(null);
                  router.replace("/quizzes/new");
                }}
              >
                Start over
              </button>
            </div>
          </>
        ) : null}

        {/* The very first call (no material at all, or the gateway down before
            anything was ever parked) never produces an envelope to key off. */}
        {!envelope && error && !busy ? (
          <div className={styles.stageFooter}>
            <Link href="/quizzes" className="btn-inline ghost">
              Back to quizzes
            </Link>
          </div>
        ) : null}

        {busy && !envelope ? (
          <div className={styles.working}>
            <span className={styles.miniSpinner} aria-hidden="true" />
            Looking at your materials…
          </div>
        ) : null}
      </div>
    </>
  );
}
