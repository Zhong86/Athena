"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import {
  addMilestone,
  ApiError,
  createSession,
  deleteMilestone,
  getGoal,
  reorderMilestones,
  SOURCE_LABEL,
  updateMilestone,
  type Milestone,
} from "@/lib/api";

import styles from "../goal.module.css";

type Draft = {
  title: string;
  description: string;
  reason: string;
  reason_long: string;
  est_effort: string;
};

function draftOf(milestone: Milestone): Draft {
  return {
    title: milestone.title,
    description: milestone.description ?? "",
    reason: milestone.reason ?? "",
    reason_long: milestone.reason_long ?? "",
    est_effort: milestone.est_effort ?? "",
  };
}

/** "3 chunks tagged "Entropy"" — the mockup's provenance line, from real rows. */
function provenance(milestone: Milestone): string | null {
  if (!milestone.source_chunks.length) return null;
  const names = [...new Set(milestone.source_chunks.map((c) => c.topic_name).filter(Boolean))];
  const count = milestone.source_chunks.length;
  const chunks = `${count} chunk${count === 1 ? "" : "s"}`;
  if (!names.length) return chunks;
  // Each topic keeps its own quotes: “Entropy” and “Heat transfer” are two
  // topics, and “Entropy, Heat transfer” reads as one badly named one.
  const quoted = names.map((name) => `“${name}”`);
  const tagged =
    quoted.length === 1
      ? quoted[0]
      : `${quoted.slice(0, -1).join(", ")} and ${quoted[quoted.length - 1]}`;
  return `${chunks} tagged ${tagged}`;
}

export function Roadmap({ goalId, initial }: { goalId: number; initial: Milestone[] }) {
  const router = useRouter();
  const [milestones, setMilestones] = useState(initial);
  // The focus milestone arrives expanded: it is the one stage the student is
  // meant to act on, so making them open it to see how would be a wasted click.
  const [open, setOpen] = useState<number[]>(
    initial.filter((m) => m.progress_status === "current").map((m) => m.id),
  );
  const [adjusting, setAdjusting] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [reviewingId, setReviewingId] = useState<number | null>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const dragOrigin = useRef<number[] | null>(null);

  async function run(work: () => Promise<unknown>, failure: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await work();
      // Milestones come back from this component's own calls, but the progress
      // ring, stage count and topic strengths are server-rendered above it.
      setMilestones((await getGoal(goalId)).milestones);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure);
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: number) {
    setOpen((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function reorderByIds(list: Milestone[], ids: number[]) {
    const byId = new Map(list.map((m) => [m.id, m]));
    return ids.map((id) => byId.get(id)).filter((m): m is Milestone => Boolean(m));
  }

  /** The order swap is applied to local state immediately; the PUT and the
      server-rendered stats above catch up in the background, so dragging
      never waits on the network. */
  function commitReorder(nextIds: number[], previousIds: number[]) {
    if (nextIds.join(",") === previousIds.join(",")) return;
    setError(null);
    reorderMilestones(goalId, nextIds)
      .then(() => router.refresh())
      .catch((err) => {
        setMilestones((prev) => reorderByIds(prev, previousIds));
        setError(err instanceof ApiError ? err.message : "Could not reorder that.");
      });
  }

  /** Keyboard fallback for the drag handle: Arrow Up/Down nudge one spot. */
  function nudge(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= milestones.length) return;
    const previousIds = milestones.map((m) => m.id);
    const nextIds = [...previousIds];
    [nextIds[index], nextIds[target]] = [nextIds[target], nextIds[index]];
    setMilestones((prev) => reorderByIds(prev, nextIds));
    commitReorder(nextIds, previousIds);
  }

  function pointerToIndex(clientY: number) {
    const items = listRef.current?.querySelectorAll<HTMLElement>("[data-mid]");
    if (!items || !items.length) return 0;
    for (let i = 0; i < items.length; i++) {
      const rect = items[i].getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return i;
    }
    return items.length - 1;
  }

  function startDrag(event: React.PointerEvent<HTMLButtonElement>, id: number) {
    if (busy) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragOrigin.current = milestones.map((m) => m.id);
    setDragId(id);
  }

  function dragMove(event: React.PointerEvent<HTMLButtonElement>) {
    if (dragOrigin.current === null || dragId === null) return;
    const from = milestones.findIndex((m) => m.id === dragId);
    const to = pointerToIndex(event.clientY);
    if (from === -1 || from === to) return;
    setMilestones((prev) => {
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }

  function endDrag() {
    if (dragOrigin.current === null) return;
    const previousIds = dragOrigin.current;
    dragOrigin.current = null;
    setDragId(null);
    commitReorder(
      milestones.map((m) => m.id),
      previousIds,
    );
  }

  function handleKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      nudge(index, -1);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      nudge(index, 1);
    }
  }

  /** Local-first: the field, edit-panel close and status flip all happen
      instantly, and the PATCH plays out behind them. A failure rolls the row
      back and surfaces the banner instead of blocking the click on a round trip. */
  function patchMilestone(
    id: number,
    fields: Parameters<typeof updateMilestone>[2],
    apply: (m: Milestone) => Milestone,
    failure: string,
  ) {
    const previous = milestones;
    setMilestones((prev) => prev.map((m) => (m.id === id ? apply(m) : m)));
    setError(null);
    updateMilestone(goalId, id, fields)
      .then(() => router.refresh())
      .catch((err) => {
        setMilestones(previous);
        setError(err instanceof ApiError ? err.message : failure);
      });
  }

  function setStatus(milestone: Milestone, status: Milestone["progress_status"], failure: string) {
    patchMilestone(
      milestone.id,
      { progress_status: status },
      (m) => {
        if (m.id === milestone.id) return { ...m, progress_status: status };
        // Only one stage is ever "current"; mirror that invariant locally so
        // the UI doesn't show two focuses until the refresh comes back.
        if (status === "current" && m.progress_status === "current") {
          return { ...m, progress_status: "upcoming" };
        }
        return m;
      },
      failure,
    );
  }

  function saveEdit(id: number) {
    if (!draft) return;
    const title = draft.title.trim();
    if (!title) return;
    const fields = {
      title,
      description: draft.description,
      reason: draft.reason,
      reason_long: draft.reason_long,
      est_effort: draft.est_effort,
    };
    setEditing(null);
    setDraft(null);
    patchMilestone(id, fields, (m) => ({ ...m, ...fields }), "Could not save that.");
  }

  function remove(milestone: Milestone) {
    if (!window.confirm(`Remove “${milestone.title}” from this roadmap?`)) return;
    void run(
      () => deleteMilestone(goalId, milestone.id),
      "Could not remove that milestone.",
    );
  }

  function add() {
    const title = newTitle.trim();
    if (!title) return;
    void run(async () => {
      await addMilestone(goalId, { title, description: newDescription.trim() || undefined });
      setNewTitle("");
      setNewDescription("");
      setAdding(false);
    }, "Could not add that milestone.");
  }

  /** Same handoff the chat FAB uses, but the session opens right away — the
      transcript page sends the opening line itself and shows its own
      thinking state instead of the student staring at a disabled page. */
  function review(milestone: Milestone) {
    if (reviewingId !== null) return;
    setReviewingId(milestone.id);
    setError(null);
    createSession("chat")
      .then((session) => {
        const prompt = `Help me work through “${milestone.title}” — it is the current stage of my roadmap.`;
        router.push(`/sessions/${session.id}?prompt=${encodeURIComponent(prompt)}`);
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Could not start that chat.");
        setReviewingId(null);
      });
  }

  return (
    <div className="section">
      <div className="section-head">
        <h2>Roadmap</h2>
        <button
          type="button"
          className="btn-inline ghost"
          onClick={() => {
            setAdjusting((prev) => !prev);
            setEditing(null);
            setAdding(false);
          }}
        >
          {adjusting ? "Done adjusting" : "Adjust roadmap"}
        </button>
      </div>

      {error ? <div className="banner-error">{error}</div> : null}

      {milestones.length === 0 ? (
        <div className="empty-state">
          <strong>No stages left</strong>
          Every milestone has been removed. Add one below, or start a new goal to have
          Αθηνα build a fresh roadmap.
        </div>
      ) : (
        <ul className={styles.roadmap} ref={listRef}>
          {milestones.map((milestone, index) => {
            const isOpen = open.includes(milestone.id);
            const isEditing = editing === milestone.id;
            const state =
              milestone.progress_status === "done"
                ? styles.isDone
                : milestone.progress_status === "current"
                  ? styles.isCurrent
                  : "";
            const chunks = provenance(milestone);
            const topicId = milestone.related_topic_ids[0];

            return (
              <li
                key={milestone.id}
                data-mid={milestone.id}
                className={`${styles.item} ${state} ${dragId === milestone.id ? styles.dragging : ""}`}
              >
                <div className={styles.num} aria-hidden="true">
                  {/* The array position, not the stored order: a mid-drag or
                      not-yet-confirmed reorder must never show a stale number. */}
                  {milestone.progress_status === "done" ? "✓" : index + 1}
                </div>

                <div className={styles.itemBody}>
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
                          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
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
                          onChange={(e) => setDraft({ ...draft, reason_long: e.target.value })}
                          rows={3}
                          placeholder="Shown when expanded"
                        />
                      </label>
                      <label className={styles.fieldGroup}>
                        <span className={styles.fieldLabel}>Estimated time</span>
                        <input
                          value={draft.est_effort}
                          onChange={(e) => setDraft({ ...draft, est_effort: e.target.value })}
                          placeholder="e.g. 45–60 min"
                        />
                      </label>
                      <div className={styles.editActions}>
                        <button
                          type="button"
                          className="btn-inline"
                          onClick={() => saveEdit(milestone.id)}
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
                      <button
                        type="button"
                        className={styles.accordionHead}
                        onClick={() => toggle(milestone.id)}
                        aria-expanded={isOpen}
                      >
                        <span className={styles.headText}>
                          <span className={styles.mTitleRow}>
                            <span className={styles.mTitle}>{milestone.title}</span>
                            {milestone.progress_status === "current" ? (
                              <span className={styles.tagCurrent}>Focus now</span>
                            ) : null}
                            {milestone.source === "research" ? (
                              <span className={styles.sourceTag}>Not in your materials</span>
                            ) : null}
                          </span>
                          {/* Collapsed: the short reason only. Everything else is
                              behind the accordion. */}
                          {milestone.reason ? (
                            <span className={styles.reason}>{milestone.reason}</span>
                          ) : null}
                        </span>
                        <span
                          className={`${styles.caret} ${isOpen ? styles.caretOpen : ""}`}
                          aria-hidden="true"
                        >
                          ▸
                        </span>
                      </button>

                      {isOpen ? (
                        <div className={styles.panel}>
                          {milestone.description ? (
                            <p className={styles.panelBody}>{milestone.description}</p>
                          ) : null}
                          {milestone.reason_long ? (
                            <p className={styles.panelReason}>{milestone.reason_long}</p>
                          ) : null}

                          <div className={styles.detailRows}>
                            {milestone.est_effort ? (
                              <div className={styles.detailRow}>
                                <span className={styles.label}>Estimated time</span>
                                <span className={styles.value}>{milestone.est_effort}</span>
                              </div>
                            ) : null}
                            <div className={styles.detailRow}>
                              <span className={styles.label}>Source materials</span>
                              <span className={styles.value}>
                                {chunks ?? SOURCE_LABEL[milestone.source]}
                              </span>
                            </div>
                            {milestone.unlocks_after_title ? (
                              <div className={styles.detailRow}>
                                <span className={styles.label}>Unlocks after</span>
                                <span className={styles.value}>
                                  {milestone.unlocks_after_title}
                                </span>
                              </div>
                            ) : null}
                          </div>

                          <div className={styles.actions}>
                            {milestone.progress_status !== "done" ? (
                              <button
                                type="button"
                                className="btn-inline"
                                onClick={() => review(milestone)}
                                disabled={reviewingId === milestone.id}
                              >
                                {reviewingId === milestone.id ? "Starting…" : "Review with Αθηνα"}
                              </button>
                            ) : null}
                            {topicId ? (
                              <Link href={`/materials/${topicId}`} className="btn-inline ghost">
                                See related materials
                              </Link>
                            ) : null}
                            {milestone.progress_status === "done" ? (
                              <button
                                type="button"
                                className="btn-inline ghost"
                                onClick={() =>
                                  setStatus(milestone, "upcoming", "Could not reopen that stage.")
                                }
                              >
                                Reopen
                              </button>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  className="btn-inline ghost"
                                  onClick={() =>
                                    setStatus(milestone, "done", "Could not mark that done.")
                                  }
                                >
                                  Mark done
                                </button>
                                {milestone.progress_status !== "current" ? (
                                  <button
                                    type="button"
                                    className="btn-inline ghost"
                                    onClick={() =>
                                      setStatus(milestone, "current", "Could not move the focus.")
                                    }
                                  >
                                    Make this the focus
                                  </button>
                                ) : null}
                              </>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </>
                  )}
                </div>

                {adjusting && !isEditing ? (
                  <div className={styles.reviewActions}>
                    <button
                      type="button"
                      className={`${styles.iconBtn} ${styles.dragHandle}`}
                      onPointerDown={(e) => startDrag(e, milestone.id)}
                      onPointerMove={dragMove}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                      onKeyDown={(e) => handleKeyDown(e, index)}
                      aria-label={`Reorder “${milestone.title}”. Drag, or use Arrow Up and Arrow Down.`}
                      title="Drag to reorder"
                    >
                      ⠿
                    </button>
                    <button
                      type="button"
                      className={styles.iconBtn}
                      onClick={() => {
                        setEditing(milestone.id);
                        setDraft(draftOf(milestone));
                      }}
                      aria-label={`Edit “${milestone.title}”`}
                      title="Edit"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className={`${styles.iconBtn} ${styles.iconDanger}`}
                      onClick={() => remove(milestone)}
                      disabled={busy}
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
      )}

      {adjusting ? (
        adding ? (
          <div className={`${styles.panel} ${styles.editFields}`} style={{ marginTop: 16 }}>
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="What is this stage?"
              aria-label="New milestone title"
              autoFocus
            />
            <textarea
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              rows={2}
              placeholder="What it covers (optional)"
              aria-label="New milestone description"
            />
            <div className={styles.editActions}>
              <button
                type="button"
                className="btn-inline"
                onClick={add}
                disabled={busy || !newTitle.trim()}
              >
                Add to the end
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
        )
      ) : null}
    </div>
  );
}

