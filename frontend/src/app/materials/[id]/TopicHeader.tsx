"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { ApiError, deleteTopic, understandingBand, updateTopic, type TopicDetail } from "@/lib/api";

import styles from "../materials.module.css";

/**
 * Renders the topic's name/description read-only by default, with an Edit
 * toggle and a Delete action. Edits go straight through the API rather than a
 * Server Action -- /materials/* isn't behind the Caddy basic-auth gate that
 * forces connections' mutations server-side (see settings/actions.ts).
 */
export function TopicHeader({ topic }: { topic: TopicDetail }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(topic.name);
  const [description, setDescription] = useState(topic.description ?? "");
  // Local display copies, updated from the PATCH response directly rather
  // than merged back into the `TopicDetail` prop -- that type omits fields
  // (source_count) a bare `Topic` carries, and there's nothing else here that
  // needs the merge.
  const [displayName, setDisplayName] = useState(topic.name);
  const [displayDescription, setDisplayDescription] = useState(topic.description);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const band = understandingBand(topic.user_understanding);

  function startEdit() {
    setName(displayName);
    setDescription(displayDescription ?? "");
    setError(null);
    setEditing(true);
  }

  function save(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("A topic needs a name.");
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        const updated = await updateTopic(topic.id, {
          name: name.trim(),
          description,
        });
        setDisplayName(updated.name);
        setDisplayDescription(updated.description);
        setEditing(false);
        // The sibling rail (topic list) and /materials both show the name too.
        router.refresh();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not save that.");
      }
    });
  }

  function onDelete() {
    const warning = topic.chunk_count
      ? `Delete "${displayName}"? Its ${topic.chunk_count} chunk${topic.chunk_count === 1 ? "" : "s"} stay in Materials, just untagged -- nothing uploaded is lost.`
      : `Delete "${displayName}"?`;
    if (!window.confirm(warning)) return;

    setError(null);
    startTransition(async () => {
      try {
        await deleteTopic(topic.id);
        router.push("/materials");
        router.refresh();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not delete that.");
      }
    });
  }

  return (
    <div className={styles.topicHeadRow}>
      <div className={styles.topicHeadMain}>
        {editing ? (
          <form onSubmit={save} className={styles.topicEditForm}>
            <input
              type="text"
              className={styles.topicEditName}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={pending}
              aria-label="Topic name"
              autoFocus
            />
            <textarea
              className={styles.topicEditDesc}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={pending}
              placeholder="What this topic covers…"
              aria-label="Topic description"
              rows={2}
            />
            {error ? <p className={styles.testModeError}>{error}</p> : null}
            <div className={styles.topicEditActions}>
              <button type="submit" className="btn-inline" disabled={pending}>
                {pending ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="btn-inline ghost"
                onClick={() => setEditing(false)}
                disabled={pending}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <>
            <h1 className={styles.topicTitle}>{displayName}</h1>
            {displayDescription ? (
              <p className={styles.topicLede}>{displayDescription}</p>
            ) : null}
            {error ? <p className={styles.testModeError}>{error}</p> : null}
            <div className={styles.topicHeadActions}>
              <button
                type="button"
                className="btn-inline ghost"
                onClick={startEdit}
                disabled={pending}
              >
                Edit
              </button>
              <button
                type="button"
                className={`btn-inline ghost ${styles.testModeDanger}`}
                onClick={onDelete}
                disabled={pending}
              >
                {pending ? "Deleting…" : "Delete"}
              </button>
            </div>
          </>
        )}
      </div>

      <div className={`${styles.scoreBadge} ${styles[band.tone]}`}>
        <span className={styles.scoreNum}>
          {topic.user_understanding < 0 ? "—" : topic.user_understanding}
        </span>
        <span className={styles.scoreLabel}>{band.label}</span>
      </div>
    </div>
  );
}
