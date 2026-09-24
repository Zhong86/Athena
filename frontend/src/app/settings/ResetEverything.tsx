"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { ApiError, resetEverything } from "@/lib/api";

import styles from "./settings.module.css";

export function ResetEverything() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  function reset() {
    setConfirming(false);
    setError(null);
    setWarning(null);
    startTransition(async () => {
      try {
        const result = await resetEverything();
        setWarning(result.warning);
        router.refresh();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not reset.");
      }
    });
  }

  return (
    <div className="section">
      <div className="section-head">
        <h2>Danger zone</h2>
      </div>
      <div className={styles.group}>
        <div className={`${styles.row} ${styles.rowStacked}`}>
          <span>
            <p className={styles.label}>Reset everything</p>
            <p className={styles.desc}>
              Deletes every upload, topic, chat, goal, quiz and connection, and
              disconnects Google. There is no undo — Αθηνα starts from a
              completely empty state, as if freshly installed.
            </p>
          </span>
          {error ? <div className="banner-error">{error}</div> : null}
          {warning ? <div className="banner-error">{warning}</div> : null}
          {confirming ? (
            <span className={styles.dangerConfirm}>
              <button
                type="button"
                className="btn-inline ghost"
                onClick={() => setConfirming(false)}
                disabled={pending}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`btn-inline ${styles.dangerButton}`}
                onClick={reset}
                disabled={pending}
              >
                {pending ? "Resetting…" : "Yes, delete everything"}
              </button>
            </span>
          ) : (
            <button
              type="button"
              className={`btn-inline ${styles.dangerButton}`}
              onClick={() => setConfirming(true)}
              disabled={pending}
            >
              Reset everything
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
