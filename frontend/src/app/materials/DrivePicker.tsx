"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { When } from "@/components/When";
import {
  ApiError,
  formatBytes,
  importDriveFiles,
  listDriveFiles,
  UPLOAD_GLYPH,
  type DriveFile,
} from "@/lib/api";

import styles from "./materials.module.css";

/** Long enough that typing a word does not cost a Drive round-trip per key. */
const SEARCH_DEBOUNCE_MS = 350;

/** Matches DriveImportRequest's max_length on the backend. */
const MAX_PER_IMPORT = 25;

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  /** 409: the user has to do something in Settings. */
  | { kind: "needsSetup"; message: string }
  /** Anything else: Google or the network, worth a retry. */
  | { kind: "error"; message: string };

/**
 * `onImported` hands the new rows back to the Sources list, which owns them in
 * state. Without it a fresh import would sit invisible until a reload: the
 * list is seeded from a server prop and only re-polls once it can already see
 * something in flight.
 */
export function DrivePicker({ onImported }: { onImported?: () => void | Promise<void> }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [files, setFiles] = useState<DriveFile[]>([]);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [rejected, setRejected] = useState<Record<string, string>>({});

  // Guards against an earlier, slower search overwriting a later one.
  const requestId = useRef(0);

  const load = useCallback(async (term: string, pageToken?: string) => {
    const id = ++requestId.current;
    setStatus({ kind: "loading" });
    try {
      const page = await listDriveFiles({ search: term, pageToken });
      if (id !== requestId.current) return;
      setFiles((prev) => (pageToken ? [...prev, ...page.items] : page.items));
      setNextPage(page.next_page_token);
      setStatus({ kind: "ready" });
    } catch (err) {
      if (id !== requestId.current) return;
      const message =
        err instanceof ApiError ? err.message : "Could not reach Google Drive.";
      setStatus(
        err instanceof ApiError && err.status === 409
          ? { kind: "needsSetup", message }
          : { kind: "error", message },
      );
    }
  }, []);

  // Debounced search, and the initial load when the panel first opens.
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => load(search), search ? SEARCH_DEBOUNCE_MS : 0);
    return () => clearTimeout(timer);
  }, [open, search, load]);

  function toggle(file: DriveFile) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(file.drive_file_id)) {
        next.delete(file.drive_file_id);
      } else if (next.size < MAX_PER_IMPORT) {
        next.add(file.drive_file_id);
      }
      return next;
    });
  }

  async function submit() {
    if (selected.size === 0 || importing) return;
    setImporting(true);
    setRejected({});
    try {
      const result = await importDriveFiles([...selected]);
      setRejected(result.rejected);
      setSelected(new Set());
      // The Sources list owns its rows in state, so it has to be told; the
      // topic cards above are server-rendered, so they need the refresh.
      await onImported?.();
      router.refresh();
      if (Object.keys(result.rejected).length === 0) setOpen(false);
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof ApiError ? err.message : "Could not import those.",
      });
    } finally {
      setImporting(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn-inline" onClick={() => setOpen(true)}>
        Import from Google Drive
      </button>
    );
  }

  return (
    <div className={styles.drivePanel}>
      <div className={styles.driveHead}>
        <div>
          <p className={styles.driveTitle}>Your Google Drive</p>
          <p className={styles.driveLede}>
            Αθηνα reads the file and keeps the chunks — the original stays in your
            Drive.
          </p>
        </div>
        <button type="button" className="btn-inline ghost" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>

      {status.kind === "needsSetup" ? (
        <div className="empty-state">
          <strong>Google is not connected yet</strong>
          {status.message}
          <p>
            <Link href="/settings" className={styles.fileLink}>
              Open Settings to connect it →
            </Link>
          </p>
        </div>
      ) : (
        <>
          <input
            type="search"
            className={styles.driveSearch}
            placeholder="Search your Drive by name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />

          {status.kind === "error" ? (
            <div className="banner-error">
              {status.message}{" "}
              <button
                type="button"
                className="btn-inline ghost"
                onClick={() => load(search)}
              >
                Try again
              </button>
            </div>
          ) : null}

          {Object.keys(rejected).length > 0 ? (
            <div className="banner-error">
              <strong>Some files were not imported</strong>
              <ul className={styles.rejectList}>
                {Object.entries(rejected).map(([name, reason]) => (
                  <li key={name}>
                    <span className={styles.pickedName}>{name}</span> — {reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {status.kind === "loading" && files.length === 0 ? (
            <p className={styles.hint}>Looking through your Drive…</p>
          ) : null}

          {status.kind === "ready" && files.length === 0 ? (
            <div className="empty-state">
              <strong>Nothing readable found</strong>
              {search
                ? "No Drive file matching that name is a document, PDF, text file or image."
                : "Αθηνα can read Google Docs, Sheets and Slides, PDFs, text files and images."}
            </div>
          ) : null}

          {files.length > 0 ? (
            <ul className={styles.driveList}>
              {files.map((file) => {
                const checked = selected.has(file.drive_file_id);
                return (
                  <li key={file.drive_file_id} className={styles.driveRow}>
                    <label className={styles.driveLabel}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(file)}
                        // Refuse quietly past the cap rather than letting the
                        // backend reject the whole batch on submit.
                        disabled={!checked && selected.size >= MAX_PER_IMPORT}
                      />
                      <span
                        className={`${styles.fileIcon} ${styles[file.upload_type]}`}
                        aria-hidden="true"
                      >
                        {UPLOAD_GLYPH[file.upload_type]}
                      </span>
                      <span className={styles.meta}>
                        <span className={styles.fileTitle}>{file.name}</span>
                        <span className={styles.fileSub}>
                          {file.source_file_id ? (
                            <span className={styles.originTag}>Already added</span>
                          ) : null}
                          {file.modified_at ? <When iso={file.modified_at} /> : null}
                          {formatBytes(file.size) ? ` · ${formatBytes(file.size)}` : ""}
                          {file.exported ? " · read as text" : ""}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          ) : null}

          {nextPage ? (
            <button
              type="button"
              className="btn-inline ghost"
              disabled={status.kind === "loading"}
              onClick={() => load(search, nextPage)}
            >
              {status.kind === "loading" ? "Loading…" : "Show more"}
            </button>
          ) : null}

          <div className={styles.formActions}>
            <button
              type="button"
              className="btn"
              disabled={selected.size === 0 || importing}
              onClick={submit}
            >
              {importing
                ? "Importing…"
                : selected.size > 1
                  ? `Import ${selected.size} files`
                  : "Import"}
            </button>
            {selected.size >= MAX_PER_IMPORT ? (
              <span className={styles.hint}>
                {MAX_PER_IMPORT} at a time — import these, then pick more.
              </span>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
