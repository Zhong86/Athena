"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { ApiError, resetMaterials } from "@/lib/api";

import styles from "./materials.module.css";

/**
 * Only ever rendered when the backend reports TEST_MODE on (see page.tsx) --
 * this component doesn't re-check that itself, since the endpoint it calls
 * already 404s outside TEST_MODE regardless of what the UI shows.
 */
export function ResetButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  function reset() {
    setConfirming(false);
    setError(null);
    startTransition(async () => {
      try {
        await resetMaterials();
        router.refresh();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not reset.");
      }
    });
  }

  return (
    <div className={styles.testModeBanner}>
      <span className={styles.testModeLabel}>TEST MODE</span>
      <span className={styles.testModeText}>
        Wipes every source file, topic, chunk and gather-run record. Not
        available in production.
      </span>
      {error ? <span className={styles.testModeError}>{error}</span> : null}
      {confirming ? (
        <span className={styles.testModeConfirm}>
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
            className={`btn-inline ${styles.testModeDanger}`}
            onClick={reset}
            disabled={pending}
          >
            {pending ? "Resetting…" : "Yes, wipe everything"}
          </button>
        </span>
      ) : (
        <button
          type="button"
          className={`btn-inline ${styles.testModeDanger}`}
          onClick={() => setConfirming(true)}
          disabled={pending}
        >
          Reset all materials
        </button>
      )}
    </div>
  );
}
