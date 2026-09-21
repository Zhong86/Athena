"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";

import { When } from "@/components/When";
import { messagesOf, titleOf, type Session } from "@/lib/api";

import styles from "@/styles/log.module.css";

import { archiveChat, deleteChat, renameChat, type RowActionResult } from "./actions";

/**
 * One chat in the log. The whole row is the link -- there is no Open button --
 * so the ⋯ menu has to sit above the link overlay and stop its own clicks
 * from bubbling into it.
 */
export function ChatRow({ session }: { session: Session }) {
  const messageCount = messagesOf(session).length;
  const title = titleOf(session);

  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const router = useRouter();
  const rowRef = useRef<HTMLLIElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  // Close on outside click or Escape, the two ways every other menu closes.
  useEffect(() => {
    if (!menuOpen) return;

    function onPointerDown(event: MouseEvent) {
      if (!rowRef.current?.contains(event.target as Node)) close();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        close();
        menuButtonRef.current?.focus();
      }
    }
    function close() {
      setMenuOpen(false);
      setConfirmDelete(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  function run(action: () => Promise<RowActionResult>, onDone?: () => void) {
    startTransition(async () => {
      const result = await action();
      setError(result.error);
      if (!result.error) {
        setMenuOpen(false);
        setConfirmDelete(false);
        onDone?.();
        // revalidatePath alone re-renders the route on the server but leaves
        // this already-mounted list showing its old props.
        router.refresh();
      }
    });
  }

  function submitRename(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new FormData(event.currentTarget).get("title");
    run(
      () => renameChat(session.id, String(next ?? "")),
      () => setRenaming(false),
    );
  }

  return (
    <li className={styles.logItem} ref={rowRef}>
      <div className={`${styles.logMarker} ${styles.chat}`}>💬</div>

      <div className={styles.meta}>
        {renaming ? (
          <form className={styles.renameForm} onSubmit={submitRename}>
            <input
              name="title"
              defaultValue={title}
              aria-label="Chat name"
              maxLength={200}
              autoFocus
              disabled={pending}
              onKeyDown={(e) => {
                if (e.key === "Escape") setRenaming(false);
              }}
            />
            <button type="submit" className="btn-inline" disabled={pending}>
              {pending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="btn-inline ghost"
              onClick={() => setRenaming(false)}
              disabled={pending}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className={styles.titleLine}>
            <Link href={`/sessions/${session.id}`} className={styles.rowLink}>
              {title}
            </Link>
          </div>
        )}

        {session.summary ? <p className={styles.summary}>{session.summary}</p> : null}

        <div className={styles.rowBottom}>
          <span className={styles.when}>
            <When
              iso={session.started_at}
              suffix={
                messageCount
                  ? `${messageCount} message${messageCount === 1 ? "" : "s"}`
                  : undefined
              }
            />
          </span>
          {error ? <span className={styles.rowError}>{error}</span> : null}
        </div>
      </div>

      <div className={styles.menuWrap}>
        <button
          type="button"
          ref={menuButtonRef}
          className={styles.menuButton}
          aria-label={`Actions for ${title}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => {
            setMenuOpen((open) => !open);
            setConfirmDelete(false);
            setError(null);
          }}
        >
          ⋯
        </button>

        {menuOpen ? (
          <div className={styles.menu} role="menu">
            <button
              type="button"
              role="menuitem"
              className={styles.menuItem}
              onClick={() => {
                setRenaming(true);
                setMenuOpen(false);
              }}
            >
              Rename
            </button>
            <button
              type="button"
              role="menuitem"
              className={styles.menuItem}
              disabled={pending}
              onClick={() => run(() => archiveChat(session.id))}
            >
              Archive
            </button>
            {/* Delete is permanent and the transcript is the only copy, so it
                takes a second click rather than a native confirm(). */}
            <button
              type="button"
              role="menuitem"
              className={`${styles.menuItem} ${styles.danger}`}
              disabled={pending}
              onClick={() => {
                if (!confirmDelete) return setConfirmDelete(true);
                run(() => deleteChat(session.id));
              }}
            >
              {confirmDelete ? "Delete for good?" : "Delete"}
            </button>
          </div>
        ) : null}
      </div>
    </li>
  );
}
