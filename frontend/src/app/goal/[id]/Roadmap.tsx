"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  addMilestone,
  ApiError,
  createSession,
  deleteMilestone,
  getGoal,
  reorderMilestones,
  sendChat,
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

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= milestones.length) return;
    const ids = milestones.map((m) => m.id);
    [ids[index], ids[target]] = [ids[target], ids[index]];
    // The backend rejects a partial order, so this always sends every id.
    void run(() => reorderMilestones(goalId, ids), "Could not reorder that.");
  }

  function saveEdit(id: number) {
    if (!draft) return;
    void run(async () => {
      await updateMilestone(goalId, id, {
        title: draft.title.trim() || undefined,
        description: draft.description,
        reason: draft.reason,
        reason_long: draft.reason_long,
        est_effort: draft.est_effort,
      });
      setEditing(null);
      setDraft(null);
    }, "Could not save that.");
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

  /** Same handoff the chat FAB uses: one real session, then the transcript page. */
  function review(milestone: Milestone) {
    void run(async () => {
      const session = await createSession("chat");
      await sendChat(
        session.id,
        `Help me work through “${milestone.title}” — it is the current stage of my roadmap.`,
      );
      router.push(`/sessions/${session.id}`);
    }, "Could not start that chat.");
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
        <ul className={styles.roadmap}>
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
              <li key={milestone.id} className={`${styles.item} ${state}`}>
                <div className={styles.num} aria-hidden="true">
                  {milestone.progress_status === "done" ? "✓" : milestone.order}
                </div>

                <div className={styles.itemBody}>
                  {isEditing && draft ? (
                    <div className={styles.editFields}>
                      <input
                        value={draft.title}
                        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                        aria-label="Milestone title"
                      />
                      <textarea
                        value={draft.description}
                        onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                        rows={2}
                        placeholder="What this stage covers"
                        aria-label="Description"
                      />
                      <input
                        value={draft.reason}
                        onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
                        placeholder="Short reason (the collapsed line)"
                        aria-label="Short reason"
                      />
                      <textarea
                        value={draft.reason_long}
                        onChange={(e) => setDraft({ ...draft, reason_long: e.target.value })}
                        rows={3}
                        placeholder="Longer reasoning (shown when expanded)"
                        aria-label="Long reason"
                      />
                      <input
                        value={draft.est_effort}
                        onChange={(e) => setDraft({ ...draft, est_effort: e.target.value })}
                        placeholder="Estimated time, e.g. 45–60 min"
                        aria-label="Estimated time"
                      />
                      <div className={styles.editActions}>
                        <button
                          type="button"
                          className="btn-inline"
                          onClick={() => saveEdit(milestone.id)}
                          disabled={busy}
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
                                disabled={busy}
                              >
                                Review with Αθηνα
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
                                  run(
                                    () =>
                                      updateMilestone(goalId, milestone.id, {
                                        progress_status: "upcoming",
                                      }),
                                    "Could not reopen that stage.",
                                  )
                                }
                                disabled={busy}
                              >
                                Reopen
                              </button>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  className="btn-inline ghost"
                                  onClick={() =>
                                    run(
                                      () =>
                                        updateMilestone(goalId, milestone.id, {
                                          progress_status: "done",
                                        }),
                                      "Could not mark that done.",
                                    )
                                  }
                                  disabled={busy}
                                >
                                  Mark done
                                </button>
                                {milestone.progress_status !== "current" ? (
                                  <button
                                    type="button"
                                    className="btn-inline ghost"
                                    onClick={() =>
                                      run(
                                        () =>
                                          updateMilestone(goalId, milestone.id, {
                                            progress_status: "current",
                                          }),
                                        "Could not move the focus.",
                                      )
                                    }
                                    disabled={busy}
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
                    <div className={styles.moveColumn}>
                      <button
                        type="button"
                        className={styles.iconBtn}
                        onClick={() => move(index, -1)}
                        disabled={busy || index === 0}
                        aria-label={`Move “${milestone.title}” earlier`}
                        title="Move earlier"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className={styles.iconBtn}
                        onClick={() => move(index, 1)}
                        disabled={busy || index === milestones.length - 1}
                        aria-label={`Move “${milestone.title}” later`}
                        title="Move later"
                      >
                        ↓
                      </button>
                    </div>
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
