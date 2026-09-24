"use client";

import Link from "next/link";

import { useRoadmapCreation } from "@/lib/roadmapCreation";

import styles from "./roadmapCreationBanner.module.css";

/**
 * Mounted once, at the root layout: the roadmap-build call is tracked at the
 * same level, so this keeps showing progress (and, once done, a link to pick
 * up the result) no matter which page the student wanders off to meanwhile.
 */
export function RoadmapCreationBanner() {
  const { state, dismiss } = useRoadmapCreation();

  if (state.status === "idle") return null;

  if (state.status === "creating") {
    return (
      <div className={styles.banner} role="status">
        <span className={styles.spinner} aria-hidden="true" />
        <span className={styles.text}>{state.label}</span>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className={`${styles.banner} ${styles.errorBanner}`} role="alert">
        <span className={styles.text}>{state.message}</span>
        <button type="button" className={styles.close} onClick={dismiss} aria-label="Dismiss">
          ✕
        </button>
      </div>
    );
  }

  const { envelope } = state;

  if (envelope.goal_id && !envelope.interrupt) {
    return (
      <div className={`${styles.banner} ${styles.readyBanner}`} role="status">
        <span className={styles.text}>Your roadmap is ready.</span>
        <Link href={`/goal/${envelope.goal_id}`} className="btn-inline" onClick={dismiss}>
          View roadmap
        </Link>
        <button type="button" className={styles.close} onClick={dismiss} aria-label="Dismiss">
          ✕
        </button>
      </div>
    );
  }

  if (envelope.interrupt) {
    return (
      <div className={`${styles.banner} ${styles.readyBanner}`} role="status">
        <span className={styles.text}>
          {envelope.interrupt.kind === "clarify"
            ? "Αθηνα has a question about your roadmap."
            : "Αθηνα drafted your roadmap — take a look."}
        </span>
        <Link
          href={`/goal/new?thread=${envelope.thread_id}`}
          className="btn-inline"
          onClick={dismiss}
        >
          Continue
        </Link>
        <button type="button" className={styles.close} onClick={dismiss} aria-label="Dismiss">
          ✕
        </button>
      </div>
    );
  }

  // Neither committed nor interrupted: the graph died mid-run.
  return (
    <div className={`${styles.banner} ${styles.errorBanner}`} role="alert">
      <span className={styles.text}>That roadmap stopped partway — nothing was saved.</span>
      <Link href={`/goal/new?thread=${envelope.thread_id}`} className="btn-inline" onClick={dismiss}>
        Take a look
      </Link>
      <button type="button" className={styles.close} onClick={dismiss} aria-label="Dismiss">
        ✕
      </button>
    </div>
  );
}
