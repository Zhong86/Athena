import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import {
  DUMMY_CHECK_IN,
  DUMMY_DEADLINES,
  DUMMY_WEAK_TOPICS,
  type Deadline,
} from "@/lib/dummy";

import styles from "./dashboard.module.css";

export const metadata = { title: "Dashboard · Αθηνα" };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

type Health = {
  status: string;
  sqlite: { schema_version: string | null; pending_migrations: number };
  hermes: boolean;
};

async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

/** Server-side clock, which for a single-user local app is the user's clock. */
function timeOfDay(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function FeedRow({ deadline }: { deadline: Deadline }) {
  return (
    <li className={styles.feedItem}>
      <span className={`${styles.dot} ${styles[deadline.urgency]}`} />
      <div className={styles.meta}>
        <p className={styles.title}>{deadline.title}</p>
        <p className={styles.reason}>
          {deadline.weakTopic ? (
            <>
              <span className={styles.tagWeak}>{deadline.weakTopic}</span> shows up
              here
            </>
          ) : (
            deadline.course
          )}{" "}
          · {deadline.dueLabel}
        </p>
      </div>
      {deadline.startable ? (
        <button type="button" className="btn-inline">
          Start
        </button>
      ) : (
        <span
          className={`${styles.when} ${
            deadline.urgency === "urgent" ? styles.whenUrgent : ""
          }`}
        >
          {deadline.weakTopic ? "Bumped up" : deadline.dueLabel}
        </span>
      )}
    </li>
  );
}

export default async function DashboardPage() {
  const health = await getHealth();
  const weakNames = DUMMY_WEAK_TOPICS.map((topic) => topic.name);

  return (
    <>
      <Nav active="Dashboard" />

      <div className="shell">
        <div className="greeting">
          <h1>{timeOfDay()}, Zhong.</h1>
          <p>Here&rsquo;s what&rsquo;s worth your attention today.</p>
        </div>

        {/* Freshest information first: what the last check-in actually noticed
            is the reason the feed below is ordered the way it is. */}
        <div className="section">
          <div className="section-head">
            <h2>Last check-in</h2>
          </div>

          <div className={styles.checkinCard}>
            <h3>{DUMMY_CHECK_IN.headline}</h3>
            <p>{DUMMY_CHECK_IN.body}</p>
          </div>
        </div>

        <div className="section">
          <div className={styles.weakAlert}>
            <span className={styles.weakAlertIcon}>◆</span>
            <div>
              <p className={styles.weakAlertTitle}>
                {weakNames.length} weak {weakNames.length === 1 ? "topic is" : "topics are"}{" "}
                shaping today&rsquo;s priorities
              </p>
              <p className={styles.weakAlertBody}>
                <strong>{joinNames(weakNames)}</strong> came up as struggles in past
                check-ins — deadlines tied to them are ranked higher below.
              </p>
            </div>
          </div>
        </div>

        <div className="section">
          <div className="section-head">
            <h2>Priority feed</h2>
          </div>

          <ul className={styles.feedList}>
            {DUMMY_DEADLINES.map((deadline) => (
              <FeedRow key={deadline.id} deadline={deadline} />
            ))}
          </ul>
        </div>

        {/* The mockup's footnote claims a portal/Calendar sync that does not
            exist yet. Until it does, report what is actually true. */}
        <p className="footnote">
          Dashboard content is placeholder data ·{" "}
          {health
            ? `backend ${health.status} · schema ${health.sqlite.schema_version ?? "none"} · hermes ${health.hermes ? "reachable" : "unreachable"}`
            : "backend unreachable"}
        </p>
      </div>

      <ChatLauncher />
    </>
  );
}
