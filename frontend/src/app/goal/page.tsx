import Link from "next/link";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  goalPill,
  goalSubtitle,
  listGoals,
  listUnfinishedRuns,
  type GoalCard,
  type RoadmapRun,
} from "@/lib/api";

import styles from "./goal.module.css";

export const metadata = { title: "Goals · Αθηνα" };

function GoalRow({ goal }: { goal: GoalCard }) {
  const pill = goalPill(goal);
  const parts = goalSubtitle(goal);

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
          <div className={styles.titleLine}>
            <p className={styles.rowTitle}>{goal.short_name || goal.title}</p>
            <span className={`${styles.statusPill} ${styles[pill.tone]}`}>{pill.label}</span>
          </div>
          <p className={styles.sub}>
            {parts.join(" · ")}
            {goal.focus_title ? (
              <>
                {parts.length ? " · " : ""}
                <span className={styles.focus}>{goal.focus_title}</span> is the current
                focus
              </>
            ) : null}
          </p>
        </div>
        <span className={styles.chevron} aria-hidden="true">
          ›
        </span>
      </Link>
    </li>
  );
}

function RunRow({ run }: { run: RoadmapRun }) {
  return (
    <li>
      <Link href={`/goal/new?thread=${run.thread_id}`} className={styles.goalRow}>
        <div
          className={styles.ring}
          style={{ "--pct": run.status === "awaiting_approval" ? 66 : 33 } as React.CSSProperties}
          aria-hidden="true"
        >
          <span>…</span>
        </div>
        <div className={styles.meta}>
          <div className={styles.titleLine}>
            <p className={styles.rowTitle}>{run.raw_goal_input}</p>
            <span className={`${styles.statusPill} ${styles.archived}`}>Draft</span>
          </div>
          <p className={styles.sub}>
            {run.status === "awaiting_approval"
              ? "Waiting on your review of the draft roadmap"
              : "Waiting on your answers"}{" "}
            · started <When iso={run.created_at} />
          </p>
        </div>
        <span className={styles.chevron} aria-hidden="true">
          ›
        </span>
      </Link>
    </li>
  );
}

export default async function GoalListPage() {
  let goals: GoalCard[] = [];
  let runs: RoadmapRun[] = [];
  let error: string | null = null;

  try {
    [goals, runs] = await Promise.all([listGoals(), listUnfinishedRuns()]);
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  // `goals.status` has no "done": a finished goal is still committed, and
  // percent is computed from its milestones rather than stored.
  const active = goals.filter((g) => g.status === "committed" && g.percent < 100);
  const completed = goals.filter((g) => g.status === "committed" && g.percent >= 100);
  const archived = goals.filter((g) => g.status === "archived");

  return (
    <>
      <Nav active="Goal" />

      <div className="shell">
        <div className={styles.headRow}>
          <div>
            <h1>Your goals.</h1>
            <p>
              Each one turns into a roadmap Αθηνα builds from your Materials and keeps
              current as check-ins come in.
            </p>
          </div>
          <Link href="/goal/new" className="btn">
            + New goal
          </Link>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        {runs.length ? (
          <div className="section">
            <div className="section-head">
              <h2>In progress</h2>
              <span className={styles.subtle}>Not committed yet</span>
            </div>
            <ul className={styles.goalList}>
              {runs.map((run) => (
                <RunRow key={run.thread_id} run={run} />
              ))}
            </ul>
          </div>
        ) : null}

        {active.length ? (
          <div className="section">
            <div className="section-head">
              <h2>Active</h2>
            </div>
            <ul className={styles.goalList}>
              {active.map((goal) => (
                <GoalRow key={goal.id} goal={goal} />
              ))}
            </ul>
          </div>
        ) : null}

        {completed.length ? (
          <div className="section">
            <div className="section-head">
              <h2>Completed</h2>
            </div>
            <ul className={styles.goalList}>
              {completed.map((goal) => (
                <GoalRow key={goal.id} goal={goal} />
              ))}
            </ul>
          </div>
        ) : null}

        {archived.length ? (
          <div className="section">
            <div className="section-head">
              <h2>Archived</h2>
            </div>
            <ul className={styles.goalList}>
              {archived.map((goal) => (
                <GoalRow key={goal.id} goal={goal} />
              ))}
            </ul>
          </div>
        ) : null}

        <div className="section">
          <Link href="/goal/new" className={styles.newGoalCard}>
            <div className={styles.plusIcon} aria-hidden="true">
              +
            </div>
            <div>
              <strong>Start a new goal</strong>
              <p>
                Tell Αθηνα what you&rsquo;re working toward — a test, an internship, a
                skill — and it will build the roadmap from there.
              </p>
            </div>
          </Link>
        </div>

        <p className="footnote">
          Roadmaps are ordered from your topic scores
        </p>
      </div>

      <ChatLauncher />
    </>
  );
}
