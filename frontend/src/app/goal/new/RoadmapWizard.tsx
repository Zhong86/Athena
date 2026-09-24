"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";

import {
  abandonRoadmap,
  ApiError,
  getRoadmapRun,
  resumeRoadmap,
  startRoadmap,
  type ApprovalInterrupt,
  type ClarifyInterrupt,
  type DraftMilestone,
  type ResumeAction,
  type RoadmapEnvelope,
} from "@/lib/api";
import { useRoadmapCreation } from "@/lib/roadmapCreation";

import styles from "../goal.module.css";

const EXAMPLES = [
  "Be ready for the Thermo midterm",
  "Land a data analytics internship",
  "Actually understand recursion",
];

const SOURCE_COPY: Record<string, string> = {
  materials: "split using what is already in Materials, then reordered against your topic scores",
  research: "built without your Materials — nothing you have uploaded covers this yet",
  mixed: "grounded in Materials where it could be, researched where it could not",
};

type EditDraft = {
  title: string;
  description: string;
  reason: string;
  reason_long: string;
  est_effort: string;
};

export function RoadmapWizard({ threadId }: { threadId: string | null }) {
  const router = useRouter();
  const creation = useRoadmapCreation();
  const [envelope, setEnvelope] = useState<RoadmapEnvelope | null>(null);
  const [raw, setRaw] = useState("");
  // Keyed by question text, not index: a new clarifying round brings new keys,
  // so there is nothing to reset between rounds.
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // Reopening a saved draft starts mid-fetch, so the flow is busy on first paint.
  const [busy, setBusy] = useState(threadId !== null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const reviewListRef = useRef<HTMLUListElement>(null);
  const dragOrigin = useRef<string[] | null>(null);
  // The graph thread only accepts one resume at a time -- unlike the goal
  // page's independent REST calls, two in-flight resumes here race against
  // the same interrupted checkpoint. Optimistic actions (reorder, edit) still
  // update the screen instantly; this just keeps their network calls, and any
  // `busy`-gated one that follows, from overlapping on the wire.
  const syncQueue = useRef<Promise<unknown>>(Promise.resolve());
  // The tracked calls below outlive this component on purpose (that's the
  // point — the banner keeps going if the student leaves). This just stops
  // their continuations from acting on a page the student already left,
  // e.g. yanking them back with a stale `router.replace`.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const interrupt = envelope?.interrupt ?? null;
  const clarify = interrupt?.kind === "clarify" ? (interrupt as ClarifyInterrupt) : null;
  const approval = interrupt?.kind === "approval" ? (interrupt as ApprovalInterrupt) : null;

  // A resumed run re-reads its parked interrupt rather than advancing the graph,
  // so reopening the "Save and exit" link lands exactly where the student left.
  useEffect(() => {
    if (!threadId) return;
    let cancelled = false;
    getRoadmapRun(threadId)
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
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  function land(fresh: RoadmapEnvelope) {
    if (fresh.goal_id && !fresh.interrupt) {
      router.push(`/goal/${fresh.goal_id}`);
      return;
    }
    setEnvelope(fresh);
  }

  /** Every resume call for this thread funnels through here, one at a time,
      whichever order they were queued in. */
  function sync(payload: ResumeAction): Promise<RoadmapEnvelope> {
    const id = envelope?.thread_id ?? threadId;
    const run = syncQueue.current.then(() => {
      if (!id) throw new Error("No active draft to resume.");
      return resumeRoadmap(id, payload);
    });
    // Swallowed here so one failed resume doesn't wedge every later one --
    // each call's own .catch still sees and reports the rejection.
    syncQueue.current = run.catch(() => undefined);
    return run;
  }

  /** Patches the parked interrupt's milestone list in place -- the only piece
      of `envelope` that reorder/edit touch, and the piece the screen needs to
      update before the network round trip even starts. */
  function updateApprovalMilestones(updater: (list: DraftMilestone[]) => DraftMilestone[]) {
    setEnvelope((prev) => {
      if (!prev || prev.interrupt?.kind !== "approval") return prev;
      return { ...prev, interrupt: { ...prev.interrupt, milestones: updater(prev.interrupt.milestones) } };
    });
  }

  function reorderDraftsByIds(list: DraftMilestone[], ids: string[]) {
    const byId = new Map(list.map((m) => [m.id, m]));
    return ids.map((id) => byId.get(id)).filter((m): m is DraftMilestone => Boolean(m));
  }

  async function begin() {
    const input = raw.trim();
    if (!input || busy) return;
    setBusy(true);
    setError(null);
    try {
      // Tracked at the layout level: if the student leaves this page before
      // it resolves, the global banner still picks up the result.
      const fresh = await creation.track("Reading your goal…", () => startRoadmap(input));
      // They may have exited already — the banner has it from here, so this
      // component must not navigate or update state out from under them.
      if (!mounted.current) return;
      // The thread id goes in the URL so a reload or an exit can find the run.
      router.replace(`/goal/new?thread=${fresh.thread_id}`);
      land(fresh);
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof ApiError ? err.message : "Could not start that goal.");
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function resume(payload: ResumeAction) {
    const id = envelope?.thread_id ?? threadId;
    if (!id || busy) return;
    setBusy(true);
    setError(null);
    try {
      land(await sync(payload));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send that.");
    } finally {
      setBusy(false);
    }
  }

  /** The clarify round is the other slow step — it runs the decompose node —
      so it gets the same cross-page tracking as `begin`. */
  async function sendClarifyAnswers() {
    const id = envelope?.thread_id ?? threadId;
    if (!id || busy || !clarify) return;
    setBusy(true);
    setError(null);
    try {
      const fresh = await creation.track(
        "Breaking this into milestones and grounding them in your materials…",
        () => sync({ answers: clarify.questions.map((q) => answers[q] ?? "") }),
      );
      if (!mounted.current) return;
      land(fresh);
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof ApiError ? err.message : "Could not send that.");
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function discard() {
    const id = envelope?.thread_id ?? threadId;
    if (!id) {
      router.push("/goal");
      return;
    }
    if (!window.confirm("Discard this draft roadmap? Nothing has been saved to your goals yet."))
      return;
    try {
      await abandonRoadmap(id);
    } catch {
      /* an unreachable backend should not trap the student in the flow */
    }
    router.push("/goal");
  }

  /** Reorders locally first, then syncs in the background -- same convention
      as the goal page's roadmap. A failed sync rolls the list back to the
      order the graph still has and surfaces the banner. */
  function commitReorder(nextIds: string[], previousIds: string[]) {
    if (nextIds.join(",") === previousIds.join(",")) return;
    setError(null);
    sync({ action: "reorder", ids_in_order: nextIds })
      .then((fresh) => land(fresh))
      .catch((err) => {
        updateApprovalMilestones((list) => reorderDraftsByIds(list, previousIds));
        setError(err instanceof ApiError ? err.message : "Could not reorder that.");
      });
  }

  function pointerToIndex(clientY: number) {
    const items = reviewListRef.current?.querySelectorAll<HTMLElement>("[data-mid]");
    if (!items || !items.length) return 0;
    for (let i = 0; i < items.length; i++) {
      const rect = items[i].getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return i;
    }
    return items.length - 1;
  }

  function startDrag(event: React.PointerEvent<HTMLButtonElement>, id: string) {
    if (!approval) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragOrigin.current = approval.milestones.map((m) => m.id);
    setDragId(id);
  }

  function dragMove(event: React.PointerEvent<HTMLButtonElement>) {
    if (dragOrigin.current === null || dragId === null || !approval) return;
    const from = approval.milestones.findIndex((m) => m.id === dragId);
    const to = pointerToIndex(event.clientY);
    if (from === -1 || from === to) return;
    updateApprovalMilestones((list) => {
      const next = [...list];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }

  function endDrag() {
    if (dragOrigin.current === null || !approval) return;
    const previousIds = dragOrigin.current;
    dragOrigin.current = null;
    setDragId(null);
    commitReorder(
      approval.milestones.map((m) => m.id),
      previousIds,
    );
  }

  /** Keyboard fallback for the drag handle. */
  function nudge(index: number, delta: number) {
    if (!approval) return;
    const target = index + delta;
    if (target < 0 || target >= approval.milestones.length) return;
    const previousIds = approval.milestones.map((m) => m.id);
    const nextIds = [...previousIds];
    [nextIds[index], nextIds[target]] = [nextIds[target], nextIds[index]];
    updateApprovalMilestones((list) => reorderDraftsByIds(list, nextIds));
    commitReorder(nextIds, previousIds);
  }

  function handleReorderKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      nudge(index, -1);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      nudge(index, 1);
    }
  }

  /** Same local-first convention as the goal page: the field and the closed
      edit panel land instantly, and the sync plays out behind them. */
  function saveApprovalEdit(milestone: DraftMilestone, fields: Partial<DraftMilestone>) {
    updateApprovalMilestones((list) =>
      list.map((m) => (m.id === milestone.id ? { ...m, ...fields } : m)),
    );
    setError(null);
    sync({ action: "edit", milestone_id: milestone.id, fields })
      .then((fresh) => land(fresh))
      .catch((err) => {
        updateApprovalMilestones((list) =>
          list.map((m) => (m.id === milestone.id ? milestone : m)),
        );
        setError(err instanceof ApiError ? err.message : "Could not save that.");
      });
  }

  // ---- stepper ----------------------------------------------------------
  const step = approval ? 3 : envelope ? 2 : 1;
  const stepper = (
    <div className={styles.stepper}>
      {[
        { n: 1, label: "Tell Αθηνα" },
        { n: 2, label: "Roadmap draft" },
        { n: 3, label: "Approve milestones" },
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

  // ---- the clarify transcript, shown live in step 1 and dimmed later ----
  const turns = clarify?.clarification_turns ?? approval?.clarification_turns ?? [];
  const opening = envelope?.raw_goal_input || raw;
  const transcript = (
    <div className={styles.clarifyThread}>
      {opening ? (
        <div className={`${styles.bubble} ${styles.bubbleUser}`}>{opening}</div>
      ) : null}
      {turns.map((turn, index) => (
        <Fragment key={index}>
          <div className={`${styles.bubble} ${styles.bubbleAgent}`}>{turn.question}</div>
          {turn.answer ? (
            <div className={`${styles.bubble} ${styles.bubbleUser}`}>{turn.answer}</div>
          ) : null}
        </Fragment>
      ))}
    </div>
  );

  return (
    <>
      <div className={styles.exitBar}>
        <div className={styles.exitInner}>
          <span className="wordmark">Αθηνα</span>
          <Link href="/goal" className={styles.exitLink}>
            Save and exit
          </Link>
        </div>
      </div>

      <div className={styles.flow}>
        {stepper}

        {error ? <div className="banner-error">{error}</div> : null}

        {/* ---------------- step 1: the goal, and any clarifying round -------- */}
        {!approval ? (
          <div className={styles.stage}>
            <div className={styles.stageHead}>
              <h1>What are you working toward?</h1>
              <p>
                A sentence is enough. Αθηνα will ask if it needs a due date or a scope,
                then build the roadmap from your Materials.
              </p>
            </div>

            {!envelope ? (
              <>
                <div className={styles.inputCard}>
                  <textarea
                    ref={textarea}
                    value={raw}
                    onChange={(e) => setRaw(e.target.value)}
                    placeholder="Be ready for the Thermo midterm on Oct 3…"
                    rows={3}
                  />
                  <div className={styles.examples}>
                    {EXAMPLES.map((example) => (
                      <button
                        key={example}
                        type="button"
                        className={styles.exampleChip}
                        onClick={() => {
                          setRaw(example);
                          textarea.current?.focus();
                        }}
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </div>

                <div className={styles.stageFooter}>
                  <Link href="/goal" className="btn-inline ghost">
                    Back
                  </Link>
                  <button
                    type="button"
                    className="btn"
                    onClick={begin}
                    disabled={busy || !raw.trim()}
                  >
                    {busy ? "Thinking…" : "Continue"}
                  </button>
                </div>
              </>
            ) : null}

            {/* On the first clarifying round there are no turns yet, but the
                student's own sentence still has to stay on screen. */}
            {envelope && (opening || turns.length) ? transcript : null}

            {clarify ? (
              <div className={styles.clarifyThread}>
                {clarify.questions.map((question, index) => (
                  <Fragment key={question}>
                    <div className={`${styles.bubble} ${styles.bubbleAgent}`}>{question}</div>
                    {(clarify.suggested_answers[index] ?? []).length ? (
                      <div className={styles.clarifyOptions}>
                        {clarify.suggested_answers[index].map((option) => (
                          <button
                            key={option}
                            type="button"
                            className={styles.clarifyOption}
                            aria-pressed={answers[question] === option}
                            onClick={() =>
                              setAnswers((prev) => ({ ...prev, [question]: option }))
                            }
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    <div className={styles.answerRow}>
                      <input
                        value={answers[question] ?? ""}
                        onChange={(e) =>
                          setAnswers((prev) => ({ ...prev, [question]: e.target.value }))
                        }
                        placeholder="Type your answer…"
                        aria-label={question}
                      />
                    </div>
                  </Fragment>
                ))}

                <div className={styles.stageFooter}>
                  <button type="button" className="btn-inline ghost" onClick={discard}>
                    Discard draft
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={sendClarifyAnswers}
                    disabled={busy || clarify.questions.every((q) => !answers[q]?.trim())}
                  >
                    {busy ? "Building…" : "Send"}
                  </button>
                </div>
              </div>
            ) : null}

            {/* A run that is neither interrupted nor committed: the graph died
                mid-node, usually with the gateway down. There is nothing to
                resume into, so say so instead of rendering an empty step. */}
            {envelope && !interrupt && !envelope.goal_id && !busy ? (
              <>
                <div className="empty-state">
                  <strong>This draft stopped partway</strong>
                  Αθηνα could not finish building the roadmap — most often because the
                  gateway was unreachable. Starting over is the fix; nothing was saved to
                  your goals.
                </div>
                <div className={styles.stageFooter}>
                  <button type="button" className="btn-inline ghost" onClick={discard}>
                    Discard draft
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      // Reset in place: navigating to /goal/new keeps this
                      // component mounted, so the dead envelope has to be
                      // cleared explicitly or the same message renders again.
                      setEnvelope(null);
                      setRaw("");
                      router.replace("/goal/new");
                    }}
                  >
                    Start over
                  </button>
                </div>
              </>
            ) : null}

            {busy && !clarify ? (
              <div className={styles.working}>
                <span className={styles.spinner} aria-hidden="true" />
                {envelope
                  ? "Breaking this into milestones and grounding them in your materials…"
                  : "Reading your goal…"}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* ---------------- step 3: show-all approval ------------------------- */}
        {approval ? (
          <>
            {turns.length ? (
              <div className={styles.stagePast}>
                <p className={styles.stagePastLabel}>Step 1 · Completed</p>
                {transcript}
              </div>
            ) : null}

            <div className={styles.stagePast}>
              <p className={styles.stagePastLabel}>Step 2 · Completed</p>
              <div className={styles.deriveAlert}>
                <span className={styles.deriveIcon} aria-hidden="true">
                  ◆
                </span>
                <div>
                  <p className={styles.deriveTitle}>
                    Decomposed into {approval.milestones.filter((m) => m.status !== "rejected").length}{" "}
                    milestones
                  </p>
                  <p className={styles.deriveBody}>
                    {approval.clarified_goal ? <strong>{approval.clarified_goal}</strong> : null}
                    {approval.clarified_goal ? " — " : ""}
                    {SOURCE_COPY[approval.decomposition_source ?? "materials"]}.
                  </p>
                </div>
              </div>
            </div>

            <div className={styles.stage}>
              <div className={styles.stageHead}>
                <h1>Review the roadmap</h1>
                <p>
                  Reorder, edit, or remove any milestone before Αθηνα locks this in. You can
                  always adjust it later from the goal page.
                </p>
              </div>

              <ul className={styles.reviewList} ref={reviewListRef}>
                {approval.milestones.map((milestone, index) => {
                  const rejected = milestone.status === "rejected";
                  const isEditing = editing === milestone.id;

                  return (
                    <li
                      key={milestone.id}
                      data-mid={milestone.id}
                      className={`${styles.reviewItem} ${rejected ? styles.reviewRejected : ""} ${
                        dragId === milestone.id ? styles.dragging : ""
                      }`}
                    >
                      <button
                        type="button"
                        className={`${styles.iconBtn} ${styles.dragHandle}`}
                        onPointerDown={(e) => startDrag(e, milestone.id)}
                        onPointerMove={dragMove}
                        onPointerUp={endDrag}
                        onPointerCancel={endDrag}
                        onKeyDown={(e) => handleReorderKeyDown(e, index)}
                        aria-label={`Reorder “${milestone.title}”. Drag, or use Arrow Up and Arrow Down.`}
                        title="Drag to reorder"
                      >
                        ⠿
                      </button>

                      <div className={styles.reviewNum} aria-hidden="true">
                        {rejected ? "✕" : index + 1}
                      </div>

                      <div className={styles.reviewBody}>
                        {isEditing && draft ? (
                          <div className={styles.editFields}>
                            <label className={styles.fieldGroup}>
                              <span className={styles.fieldLabel}>Title</span>
                              <input
                                value={draft.title}
                                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                              />
                            </label>
                            <label className={styles.fieldGroup}>
                              <span className={styles.fieldLabel}>Description</span>
                              <textarea
                                value={draft.description}
                                onChange={(e) =>
                                  setDraft({ ...draft, description: e.target.value })
                                }
                                rows={2}
                                placeholder="What this stage covers"
                              />
                            </label>
                            <label className={styles.fieldGroup}>
                              <span className={styles.fieldLabel}>Short reason</span>
                              <input
                                value={draft.reason}
                                onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
                                placeholder="Shown on the collapsed row"
                              />
                            </label>
                            <label className={styles.fieldGroup}>
                              <span className={styles.fieldLabel}>Long reason</span>
                              <textarea
                                value={draft.reason_long}
                                onChange={(e) =>
                                  setDraft({ ...draft, reason_long: e.target.value })
                                }
                                rows={3}
                                placeholder="Shown when expanded"
                              />
                            </label>
                            <label className={styles.fieldGroup}>
                              <span className={styles.fieldLabel}>Estimated time</span>
                              <input
                                value={draft.est_effort}
                                onChange={(e) =>
                                  setDraft({ ...draft, est_effort: e.target.value })
                                }
                                placeholder="e.g. 45–60 min"
                              />
                            </label>
                            <div className={styles.editActions}>
                              <button
                                type="button"
                                className="btn-inline"
                                onClick={() => {
                                  const fields = { ...draft };
                                  setEditing(null);
                                  setDraft(null);
                                  saveApprovalEdit(milestone, fields);
                                }}
                              >
                                Save
                              </button>
                              <button
                                type="button"
                                className="btn-inline ghost"
                                onClick={() => {
                                  setEditing(null);
                                  setDraft(null);
                                }}
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <p className={styles.reviewTitle}>{milestone.title}</p>
                            {milestone.reason ? (
                              <p className={styles.reviewReason}>{milestone.reason}</p>
                            ) : null}
                            {milestone.source === "research" ? (
                              <p className={styles.reviewReason}>
                                <span className={styles.sourceTag}>Not in your materials</span>
                              </p>
                            ) : null}
                          </>
                        )}
                      </div>

                      {!isEditing && !rejected ? (
                        <div className={styles.reviewActions}>
                          <button
                            type="button"
                            className={styles.iconBtn}
                            onClick={() => {
                              setEditing(milestone.id);
                              setDraft({
                                title: milestone.title,
                                description: milestone.description ?? "",
                                reason: milestone.reason ?? "",
                                reason_long: milestone.reason_long ?? "",
                                est_effort: milestone.est_effort ?? "",
                              });
                            }}
                            aria-label={`Edit “${milestone.title}”`}
                            title="Edit"
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            className={`${styles.iconBtn} ${styles.iconDanger}`}
                            disabled={busy}
                            onClick={() => {
                              // There is no un-reject action in the graph: a
                              // rejected milestone stays struck through until
                              // commit drops it, so ask before doing it.
                              if (!window.confirm(`Drop “${milestone.title}” from this roadmap?`))
                                return;
                              void resume({ action: "reject", milestone_id: milestone.id });
                            }}
                            aria-label={`Remove “${milestone.title}”`}
                            title="Remove"
                          >
                            ✕
                          </button>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>

              {adding ? (
                <div className={`${styles.panel} ${styles.editFields}`} style={{ marginTop: 12 }}>
                  <input
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    placeholder="What is this stage?"
                    aria-label="New milestone title"
                    autoFocus
                  />
                  <div className={styles.editActions}>
                    <button
                      type="button"
                      className="btn-inline"
                      disabled={busy || !newTitle.trim()}
                      onClick={() => {
                        const title = newTitle.trim();
                        setNewTitle("");
                        setAdding(false);
                        void resume({ action: "add_milestone", fields: { title } });
                      }}
                    >
                      Add milestone
                    </button>
                    <button
                      type="button"
                      className="btn-inline ghost"
                      onClick={() => setAdding(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" className={styles.addRow} onClick={() => setAdding(true)}>
                  <span className={styles.plusCircle} aria-hidden="true">
                    +
                  </span>
                  <span>Add a milestone</span>
                </button>
              )}

              {busy ? (
                <div className={styles.working}>
                  <span className={styles.spinner} aria-hidden="true" />
                  Applying that…
                </div>
              ) : null}

              <div className={styles.stageFooter}>
                <button type="button" className="btn-inline ghost" onClick={discard}>
                  Discard draft
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => resume({ action: "approve_all" })}
                  disabled={busy}
                >
                  Confirm roadmap
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}
