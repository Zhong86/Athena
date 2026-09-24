"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ApiError, sendChat, type ChatMessage } from "@/lib/api";
import { SessionTrace } from "@/components/SessionTrace";

import styles from "./transcript.module.css";

export function Transcript({
  sessionId,
  initialMessages,
}: {
  sessionId: number;
  initialMessages: ChatMessage[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pendingPrompt = searchParams.get("prompt");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // Guards the auto-send below against firing twice (StrictMode's double
  // effect, or a re-render before the URL has dropped the query param).
  const sentPromptRef = useRef(false);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  useEffect(() => {
    if (!pendingPrompt || sentPromptRef.current) return;
    sentPromptRef.current = true;
    // Drop it from the URL right away so a reload can't resend it.
    router.replace(`/sessions/${sessionId}`, { scroll: false });
    void send(pendingPrompt);
    // `send` is stable enough for this one-shot, mount-time effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingPrompt]);

  async function send(message: string) {
    setError(null);
    setPending(true);
    // Show the user's message straight away; the backend persists it only
    // once the turn succeeds, so drop it again if the request fails.
    setMessages((prev) => [...prev, { role: "user", content: message }]);

    try {
      const { reply, trace } = await sendChat(sessionId, message);
      setMessages((prev) => [...prev, { role: "assistant", content: reply, trace }]);
    } catch (err) {
      setMessages((prev) => prev.slice(0, -1));
      setDraft(message);
      setError(err instanceof ApiError ? err.message : "Could not send that message.");
    } finally {
      setPending(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || pending) return;
    setDraft("");
    await send(message);
  }

  return (
    <>
      <div className={styles.transcript}>
        {messages.length === 0 && !pending ? (
          <div className={styles.empty}>
            <strong>Nothing here yet</strong>
            Ask Αθηνα something to get started.
          </div>
        ) : null}

        {messages.map((message, i) => (
          <Fragment key={i}>
            <div
              className={`${styles.bubble} ${
                message.role === "user" ? styles.user : styles.assistant
              }`}
            >
              {message.content}
            </div>
            {message.trace?.length ? <SessionTrace trace={message.trace} /> : null}
          </Fragment>
        ))}

        {pending ? (
          <div
            className={`${styles.bubble} ${styles.assistant} ${styles.thinking}`}
            role="status"
          >
            <span className={styles.spinner} aria-hidden="true" />
            Thinking…
          </div>
        ) : null}

        <div ref={endRef} />
      </div>

      {error ? <div className="banner-error">{error}</div> : null}

      <form className={styles.composer} onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask Αθηνα anything..."
          aria-label="Message"
          disabled={pending}
        />
        <button type="submit" className="btn" disabled={pending || !draft.trim()}>
          {pending ? "Sending…" : "Send"}
        </button>
      </form>
    </>
  );
}
