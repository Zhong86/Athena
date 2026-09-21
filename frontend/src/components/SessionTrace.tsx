import { formatDuration, type TraceEntry } from "@/lib/api";
import styles from "@/styles/trace.module.css";

/**
 * The bare timeline. The run detail page shows this on its own -- that page
 * exists to show the working, so hiding it behind a disclosure there would be
 * one click in front of the only thing on screen.
 */
export function TraceTimeline({ trace }: { trace: TraceEntry[] }) {
  return (
    <ol className={styles.steps}>
      {trace.map((entry, i) => (
        <li key={i} className={styles.step}>
          <Step entry={entry} />
        </li>
      ))}
    </ol>
  );
}

/**
 * The same timeline folded behind a disclosure, for the Knowledge-Sync list
 * where it is one row among many. Native <details> keeps this a server
 * component -- nothing here is live, so there is no reason to ship JS for it.
 */
export function SessionTrace({ trace }: { trace: TraceEntry[] }) {
  if (!trace.length) return null;

  return (
    <details className={styles.wrap}>
      <summary className={styles.toggle}>
        Show its working · {trace.length} {trace.length === 1 ? "step" : "steps"}
      </summary>
      <TraceTimeline trace={trace} />
    </details>
  );
}

function Step({ entry }: { entry: TraceEntry }) {
  switch (entry.kind) {
    case "note":
      // Hermes' own commentary -- the most readable part of a run, so it is
      // the only step type set in prose rather than mono.
      return (
        <>
          <span className={`${styles.dot} ${styles.think}`} aria-hidden />
          <p className={styles.note}>{entry.text}</p>
        </>
      );

    case "tool": {
      const failed = entry.status === "error";
      return (
        <>
          <span
            className={`${styles.dot} ${failed ? styles.bad : styles.tool}`}
            aria-hidden
          />
          <div className={styles.body}>
            <p className={styles.head}>
              <code className={styles.name}>{entry.tool}</code>
              {entry.input ? <span className={styles.arg}>{entry.input}</span> : null}
              {entry.duration != null ? (
                <span className={styles.duration}>{formatDuration(entry.duration)}</span>
              ) : null}
            </p>
            {entry.output ? (
              <p className={failed ? styles.errorText : styles.output}>{entry.output}</p>
            ) : null}
          </div>
        </>
      );
    }

    case "subagent":
      return (
        <>
          <span className={`${styles.dot} ${styles.sub}`} aria-hidden />
          <div className={styles.body}>
            <p className={styles.head}>
              <span className={styles.name}>Delegated task</span>
              {entry.duration != null ? (
                <span className={styles.duration}>{formatDuration(entry.duration)}</span>
              ) : null}
            </p>
            {entry.summary ? <p className={styles.note}>{entry.summary}</p> : null}
          </div>
        </>
      );

    case "error":
      return (
        <>
          <span className={`${styles.dot} ${styles.bad}`} aria-hidden />
          <p className={styles.errorText}>{entry.text}</p>
        </>
      );
  }
}
