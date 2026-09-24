"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { ApiError, deleteTopic, understandingBand, type Topic } from "@/lib/api";

import styles from "./materials.module.css";

export function TopicCard({ topic }: { topic: Topic }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const band = understandingBand(topic.user_understanding);

  function onDelete(event: React.MouseEvent) {
    // Not the card's own Link -- this button is a sibling positioned over
    // it, but a click still has to be told not to also navigate.
    event.preventDefault();
    event.stopPropagation();

    const warning = topic.chunk_count
      ? `Delete "${topic.name}"? Its ${topic.chunk_count} chunk${topic.chunk_count === 1 ? "" : "s"} stay in Materials, just untagged -- nothing uploaded is lost.`
      : `Delete "${topic.name}"?`;
    if (!window.confirm(warning)) return;

    setError(null);
    startTransition(async () => {
      try {
        await deleteTopic(topic.id);
        router.refresh();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not delete that.");
      }
    });
  }

  return (
    <li className={styles.topicCardWrap}>
      <Link href={`/materials/${topic.id}`} className={styles.topicCard}>
        <div className={styles.topicHead}>
          <p className={styles.topicName}>{topic.name}</p>
          <span className={`${styles.scorePill} ${styles[band.tone]}`}>
            {topic.user_understanding < 0 ? "—" : topic.user_understanding}
          </span>
        </div>
        {topic.description ? (
          <p className={styles.topicDesc}>{topic.description}</p>
        ) : null}
        <p className={styles.topicMeta}>
          {topic.chunk_count} chunk{topic.chunk_count === 1 ? "" : "s"} ·{" "}
          {topic.source_count} source{topic.source_count === 1 ? "" : "s"} · {band.label}
        </p>
      </Link>

      <button
        type="button"
        className={styles.topicDeleteBtn}
        onClick={onDelete}
        disabled={pending}
        aria-label={`Delete topic ${topic.name}`}
        title={error ?? `Delete ${topic.name}`}
      >
        {pending ? "…" : "×"}
      </button>
    </li>
  );
}
