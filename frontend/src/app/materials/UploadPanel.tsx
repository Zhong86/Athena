"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { When } from "@/components/When";
import {
  ApiError,
  deleteUpload,
  formatBytes,
  ingestLabel,
  isDegraded,
  isIngesting,
  listUploads,
  retryUpload,
  UPLOAD_GLYPH,
  uploadFile,
  type SourceFile,
} from "@/lib/api";

import { DrivePicker } from "./DrivePicker";
import styles from "./materials.module.css";
import { SourceName } from "./SourceName";

const POLL_MS = 1500;
const ACCEPT = ".txt,.md,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tiff";

export function UploadPanel({ initial }: { initial: SourceFile[] }) {
  const router = useRouter();
  const [uploads, setUploads] = useState<SourceFile[]>(initial);
  const [picked, setPicked] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const ingesting = uploads.some((u) => isIngesting(u.ingest_status));

  // Ingestion runs in a background task, so the only way to learn it finished
  // is to ask. Polling stops as soon as nothing is in flight.
  useEffect(() => {
    if (!ingesting) return;

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const fresh = await listUploads();
        if (cancelled) return;
        setUploads(fresh);
        // Topic counts and the topic list itself are server-rendered above,
        // and a finished ingest is exactly what changes them.
        if (!fresh.some((u) => isIngesting(u.ingest_status))) router.refresh();
      } catch {
        /* a dropped poll is harmless -- the next tick retries */
      }
    }, POLL_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ingesting, uploads, router]);

  async function refresh() {
    try {
      setUploads(await listUploads());
    } catch {
      /* leave the current list in place */
    }
  }

  function take(list: FileList | null) {
    if (!list || list.length === 0) return;
    setError(null);
    setPicked(Array.from(list));
  }

  function clearPicked() {
    setPicked([]);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError(null);

    if (picked.length === 0) {
      setError("Choose a file first.");
      return;
    }

    setBusy(true);
    try {
      // One request per file: the endpoint takes a single file, and uploading
      // in sequence keeps a failure attributable to the file that caused it.
      for (const file of picked) {
        await uploadFile(file);
      }
      clearPicked();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not upload that.");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function onRetry(id: number) {
    setError(null);
    try {
      await retryUpload(id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not retry that.");
    }
  }

  async function onDelete(upload: SourceFile) {
    // Removing a file drops its chunks and their vectors for good, so make the
    // user say so -- there is no undo and no re-upload from the server side.
    // For a Drive row only our index goes; saying so matters, because "Remove"
    // next to a file that lives in their Drive reads like it deletes it there.
    const warning =
      upload.origin === "drive"
        ? `Remove "${upload.filename}" from Αθηνα? The file stays in your Drive — only the chunks and topics read from it are dropped.`
        : `Remove "${upload.filename}" and everything tagged from it?`;
    if (!window.confirm(warning)) {
      return;
    }
    const id = upload.id;
    setError(null);
    try {
      await deleteUpload(id);
      await refresh();
      // Deleting a file removes its chunks, which can empty a topic.
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that.");
    }
  }

  return (
    <>
      <div className={styles.sourceTabs}>
        <DrivePicker onImported={refresh} />
      </div>

      <form onSubmit={submit} className={styles.uploadForm}>
        <input
          ref={fileRef}
          id="material-file"
          type="file"
          accept={ACCEPT}
          multiple
          className={styles.fileInput}
          onChange={(e) => take(e.target.files)}
        />

        {/* The label is the drop target: activating it opens the picker for
            free, so no click handler is needed. */}
        <label
          htmlFor="material-file"
          className={`${styles.dropZone} ${dragging ? styles.dropZoneActive : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            take(e.dataTransfer.files);
          }}
        >
          <span className={styles.dropIcon} aria-hidden="true">
            ↑
          </span>
          <span className={styles.dropTitle}>
            Drop a file here, or click to choose one
          </span>
          <span className={styles.dropHint}>
            PDF, image or plain text, up to 10MB. Scanned PDFs with no text layer
            are not read yet.
          </span>
        </label>

        {picked.length ? (
          <ul className={styles.pickedList}>
            {picked.map((file) => (
              <li key={`${file.name}-${file.size}`} className={styles.pickedItem}>
                <span className={styles.pickedName}>{file.name}</span>
                <span className={styles.subtle}>{formatBytes(file.size)}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {error ? <div className="banner-error">{error}</div> : null}

        <div className={styles.formActions}>
          <button type="submit" className="btn" disabled={busy || picked.length === 0}>
            {busy
              ? "Uploading…"
              : picked.length > 1
                ? `Add ${picked.length} files`
                : "Add material"}
          </button>
          {picked.length > 0 && !busy ? (
            <button type="button" className="btn-inline ghost" onClick={clearPicked}>
              Clear
            </button>
          ) : null}
          {ingesting ? (
            <span className={styles.hint}>
              Αθηνα is reading and tagging — this page updates itself.
            </span>
          ) : null}
        </div>
      </form>

      {uploads.length === 0 ? (
        <div className="empty-state">
          <strong>No material yet</strong>
          Add a file above. Αθηνα breaks it into chunks, tags each one with a topic,
          and uses them for quizzes and your roadmap.
        </div>
      ) : (
        <ul className={styles.fileList}>
          {uploads.map((upload) => (
            <li key={upload.id} className={styles.fileRow}>
              <div
                className={`${styles.fileIcon} ${styles[upload.upload_type]}`}
                aria-hidden="true"
              >
                {UPLOAD_GLYPH[upload.upload_type]}
              </div>
              <div className={styles.meta}>
                <p className={styles.fileTitle}>
                  <SourceName {...upload} />
                </p>
                <p className={styles.fileSub}>
                  {upload.origin === "drive" ? (
                    <span className={styles.originTag}>Drive</span>
                  ) : null}
                  <When iso={upload.uploaded_at} />
                  {formatBytes(upload.byte_size) ? ` · ${formatBytes(upload.byte_size)}` : ""}
                  {upload.ingest_status === "ready"
                    ? ` · ${upload.chunk_count} chunk${upload.chunk_count === 1 ? "" : "s"}`
                    : ""}
                </p>
                {upload.ingest_error ? (
                  <p
                    className={isDegraded(upload) ? styles.fileNote : styles.fileError}
                  >
                    {upload.ingest_error}
                  </p>
                ) : null}
              </div>
              <div className={styles.fileActions}>
                <span
                  className={`${styles.statusPill} ${
                    isDegraded(upload) ? styles.degraded : styles[upload.ingest_status]
                  }`}
                >
                  {ingestLabel(upload)}
                </span>
                {upload.ingest_status === "failed" || isDegraded(upload) ? (
                  <button
                    type="button"
                    className="btn-inline"
                    onClick={() => onRetry(upload.id)}
                  >
                    Retry
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn-inline ghost"
                  onClick={() => onDelete(upload)}
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
