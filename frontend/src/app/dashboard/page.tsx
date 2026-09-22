import Link from "next/link";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import {
  API_BASE,
  ApiError,
  getDashboard,
  goalSubtitle,
  listGoals,
  type Dashboard,
  type GoalCard,
} from "@/lib/api";
import { quoteOfTheDay } from "@/lib/quotes";

import styles from "./dashboard.module.css";

export const metadata = { title: "Dashboard · Αθηνα" };

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

async function getDashboardSafe(): Promise<Dashboard | null> {
  try {
    return await getDashboard();
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

function GoalProgressRow({ goal }: { goal: GoalCard }) {
  return (
    <li>
      <Link href={`/goal/${goal.id}`} className={styles.goalRow}>
        <div
          className={styles.ring}
          style={{ "--pct": goal.percent } as React.CSSProperties}
          aria-hidden="true"
        >
          <span>{goal.percent >= 100 ? "✓" : `${goal.percent}%`}</span>
        </div>
        <div className={styles.meta}>
          <p className={styles.title}>{goal.short_name || goal.title}</p>
          <p className={styles.reason}>{goalSubtitle(goal).join(" · ")}</p>
        </div>
        <span className={styles.chevron} aria-hidden="true">
          ›
        </span>
      </Link>
    </li>
  );
}

export default async function DashboardPage() {
  const quote = quoteOfTheDay();

  let goalsError: string | null = null;
  const [health, dashboard, goals] = await Promise.all([
    getHealth(),
    getDashboardSafe(),
    listGoals().catch((err) => {
      goalsError = err instanceof ApiError ? err.message : "Something went wrong.";
      return [] as GoalCard[];
    }),
  ]);

  const weakNames = dashboard?.weak_topics.map((topic) => topic.name) ?? [];
  const checkIn = dashboard?.check_in ?? null;
  const activeGoals = goals
    .filter((g) => g.status === "committed" && g.percent < 100)
    .slice(0, 3);

  return (
    <>
      <Nav active="Dashboard" />

      <div className="shell">
        <div className="greeting">
          <h1>{timeOfDay()}, Zhong.</h1>
          <p>Here&rsquo;s what&rsquo;s worth your attention today.</p>
        </div>

        <div className="section">
          <blockquote className={styles.quoteCard}>
            <p className={styles.quoteText}>&ldquo;{quote.text}&rdquo;</p>
            <footer className={styles.quoteAuthor}>— {quote.author}</footer>
          </blockquote>
        </div>

        {goalsError ? <div className="banner-error">{goalsError}</div> : null}

        <div className="section">
          <div className="section-head">
            <h2>Goals in progress</h2>
            <Link href="/goal" className={styles.subtleLink}>
              See all
            </Link>
          </div>

          {activeGoals.length ? (
            <ul className={styles.goalList}>
              {activeGoals.map((goal) => (
                <GoalProgressRow key={goal.id} goal={goal} />
              ))}
            </ul>
          ) : (
            <div className={styles.emptyCard}>
              <p>No goals in progress yet.</p>
              <Link href="/goal/new" className="btn-inline">
                Start a goal
              </Link>
            </div>
          )}
        </div>

        {/* Freshest information first: what the last check-in actually noticed
            is the reason weak topics below are worth attention. */}
        <div className="section">
          <div className="section-head">
            <h2>Last check-in</h2>
          </div>

          {checkIn ? (
            <div className={styles.checkinCard}>
              <h3>{checkIn.topic_name}</h3>
              <p>{checkIn.reason}</p>
            </div>
          ) : (
            <div className={styles.checkinCard}>
              <h3>No check-ins yet</h3>
              <p>
                Upload materials and take a quiz or two — once Hermes scores a
                topic, this card shows what it noticed.
              </p>
            </div>
          )}
        </div>

        {weakNames.length > 0 && (
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
                  check-ins.
                </p>
              </div>
            </div>
          </div>
        )}

        <p className="footnote">
          {health
            ? `backend ${health.status} · schema ${health.sqlite.schema_version ?? "none"} · hermes ${health.hermes ? "reachable" : "unreachable"}`
            : "backend unreachable"}
        </p>
      </div>

      <ChatLauncher />
    </>
  );
}
