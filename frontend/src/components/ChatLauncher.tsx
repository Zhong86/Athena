"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ApiError, createSession, sendChat } from "@/lib/api";

import styles from "./chatLauncher.module.css";

/**
 * The sticky chat button from the mockups. The panel is a launcher, not a
 * second chat surface: the first message creates a real session and hands off
 * to /sessions/[id], so there is only ever one transcript implementation.
 */
export function ChatLauncher() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A failed turn leaves a usable empty session behind; reuse it on retry
  // rather than stacking up blank rows in the timeline.
  const sessionRef = useRef<number | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || pending) return;

    setError(null);
    setPending(true);
    try {
      if (sessionRef.current === null) {
        sessionRef.current = (await createSession("chat")).id;
      }
      await sendChat(sessionRef.current, message);
      router.push(`/sessions/${sessionRef.current}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start that chat.");
      setPending(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={styles.fab}
        aria-label="Open chat with Αθηνα"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <Image
          className={styles.fabMark}
          src="/logo-mark.png"
          alt=""
          width={460}
          height={320}
        />
      </button>

      <div className={`${styles.panel} ${open ? styles.panelOpen : ""}`}>
        <div className={styles.panelHead}>
          <span className={styles.name}>Αθηνα</span>
          <button
            type="button"
            className={styles.close}
            aria-label="Close chat"
            onClick={() => setOpen(false)}
          >
            ✕
          </button>
        </div>

        <div className={styles.panelBody}>
          <div className={styles.bubble}>
            Hey — what do you want to work on? I&rsquo;ll open a full session for
            this.
          </div>
          {error ? <div className={styles.error}>{error}</div> : null}
        </div>

        <form className={styles.panelInput} onSubmit={submit}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask Αθηνα anything..."
            aria-label="Message"
            disabled={pending}
          />
          <button type="submit" className={styles.send} disabled={pending || !draft.trim()}>
            {pending ? "…" : "Send"}
          </button>
        </form>
      </div>
    </>
  );
}
