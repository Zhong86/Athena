"use client";

import Link from "next/link";
import { useState, useTransition } from "react";

import type { GatherConfig, GatherRunResult } from "@/lib/api";

import { clearGatherFolderAction, runGatherNow, saveGatherFolder } from "./actions";
import styles from "../settings/settings.module.css";

type Props = {
  config: GatherConfig;
  /** Null when the initial config fetch failed -- the controls still render
      so "Sync now" and retrying the folder save both stay reachable. */
  loadError: string | null;
};

export function SyncControls({ config, loadError }: Props) {
  // Two transitions, not one: sync and the folder form are independent
  // actions, and sharing a single `pending` flag made "Sync now" show
  // "Saving…" (and vice versa) whichever ran first.
  const [syncPending, startSync] = useTransition();
  const [folderPending, startFolder] = useTransition();
  const [current, setCurrent] = useState(config);
  const [folderInput, setFolderInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GatherRunResult | null>(null);

  function run<T>(
    startTransition: (work: () => Promise<void>) => void,
    work: () => Promise<{ data: T | null; error: string | null }>,
    onSuccess: (data: T) => void,
  ) {
    setError(null);
    startTransition(async () => {
      const res = await work();
      if (res.error) setError(res.error);
      else if (res.data) onSuccess(res.data);
    });
  }

  function sync() {
    setResult(null);
    run(startSync, runGatherNow, setResult);
  }

  function submitFolder(event: React.FormEvent) {
    event.preventDefault();
    run(startFolder, () => saveGatherFolder(folderInput), (data) => {
      setCurrent(data);
      setFolderInput("");
    });
  }

  function clearFolder() {
    run(startFolder, clearGatherFolderAction, setCurrent);
  }

  return (
    <div className="section">
      <div className="section-head">
        <h2>Auto-sync</h2>
      </div>

      <div className={styles.group}>
        {loadError ? <div className="banner-error">{loadError}</div> : null}
        {error ? <div className="banner-error">{error}</div> : null}

        <div className={`${styles.row} ${styles.rowStacked}`}>
          <span className={styles.rowText}>
            <span className={styles.label}>Run sync now</span>
            <span className={styles.desc}>
              Checks your materials inbox and connected Drive for anything new,
              judges what&rsquo;s worth keeping, and imports it &mdash; the same
              job the schedule runs on its own.
            </span>
          </span>
          <button
            type="button"
            className="btn-inline"
            onClick={sync}
            disabled={syncPending}
          >
            {syncPending ? "Syncing…" : "Sync now"}
          </button>
        </div>

        {result ? (
          <p className={`${styles.row} ${styles.hint}`}>
            {/* "Sync now" never sends scheduled=true, so the backend never
                skips it -- session_id is only ever null here in principle,
                not in practice. Guarded anyway since the type allows it. */}
            {result.candidates_seen === 0 ? (
              // The common case on a healthy schedule: nothing changed in the
              // inbox or Drive since the last run's cursor. Distinct from the
              // itemised line below -- "0 of 0" reads as broken otherwise.
              "Nothing new since the last sync."
            ) : (
              <>
                Imported {result.imported_file_ids.length}, refreshed{" "}
                {result.refreshed_file_ids.length}, skipped{" "}
                {result.skipped_local.length} of {result.candidates_seen} candidates.{" "}
              </>
            )}
            {result.session_id !== null ? (
              <Link href={`/knowledge-sync/${result.session_id}`} className="btn-inline ghost">
                See details
              </Link>
            ) : null}
          </p>
        ) : null}

        <div className={`${styles.row} ${styles.rowStacked}`}>
          <span className={styles.rowText}>
            <span className={styles.label}>Limit Drive to a folder</span>
            <span className={styles.desc}>
              {current.folder_id
                ? `Only scanning inside "${current.folder_name}" and its subfolders.`
                : "Scanning all of connected Drive."}{" "}
              Narrowing this down keeps sync fast and avoids pulling in files
              that aren&rsquo;t coursework.
            </span>
          </span>

          {current.folder_id ? (
            <ul className={styles.chips}>
              <li className={styles.chip}>
                {current.folder_name}
                <button
                  type="button"
                  className={styles.chipRemove}
                  onClick={clearFolder}
                  disabled={folderPending}
                  aria-label="Remove folder scope, scan all of Drive again"
                >
                  ×
                </button>
              </li>
            </ul>
          ) : (
            <form onSubmit={submitFolder} className={styles.siteForm}>
              <input
                type="text"
                className={styles.input}
                placeholder="https://drive.google.com/drive/folders/…"
                value={folderInput}
                onChange={(e) => setFolderInput(e.target.value)}
                disabled={folderPending}
                aria-label="Drive folder link or id"
              />
              <button
                type="submit"
                className="btn-inline"
                disabled={folderPending || !folderInput.trim()}
              >
                {folderPending ? "Saving…" : "Save"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
