import Link from "next/link";
import { notFound } from "next/navigation";

import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav, type DrillNav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  formatDue,
  getGoal,
  goalPill,
  listGoals,
  type GoalCard,
  type GoalDetail,
} from "@/lib/api";

import styles from "../goal.module.css";
import { Roadmap } from "./Roadmap";

export const metadata = { title: "Goal · Αθηνα" };

export default async function GoalDetailPage(props: { params: Promise<{ id: string }> }) {
  const { id } = await props.params;
  const goalId = Number(id);
  if (!Number.isInteger(goalId) || goalId < 1) notFound();

  let goal: GoalDetail | null = null;
  let error: string | null = null;
  try {
    goal = await getGoal(goalId);
  } catch (err) {
    if (err instanceof ApiError && err.message === "No such goal") notFound();
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  if (!goal) {
    return (
      <>
        <Nav active="Goal" />
        <div className="shell">
          <div className="banner-error">{error}</div>
          <Link href="/goal" className="btn-inline ghost">
            ← Back to goals
          </Link>
        </div>
      </>
    );
  }

  // Desktop D2: the sibling goals take over the rail. Losing them costs only
  // the sub-list, so a failed fetch falls back to this goal alone.
  let siblings: GoalCard[] = [];
  try {
    siblings = await listGoals();
  } catch {
    siblings = [];
  }

  const drill: DrillNav = {
    back: { href: "/goal", label: "Goals" },
    title: "GOALS",
    items: (siblings.length ? siblings : [{ id: goal.id, short_name: goal.short_name, title: goal.title }])
      .map((g) => ({
        href: `/goal/${g.id}`,
        label: g.short_name || g.title,
        active: g.id === goal.id,
      })),
    add: { href: "/goal/new", label: "+ New goal" },
  };

  const pill = goalPill(goal);
  const due = formatDue(goal.due_at);
  const researched = goal.milestones.filter((m) => m.source === "research");
  const subParts = [due, goal.course_code, goal.derivation].filter(Boolean);

  return (
    <>
      <Nav active="Goal" drill={drill} />

      <div className="shell">
        <div className="greeting">
          <div className={styles.titleLine} style={{ marginBottom: 10 }}>
            <span className={`${styles.statusPill} ${styles[pill.tone]}`}>{pill.label}</span>
          </div>
          <h1>{goal.title}</h1>
          <p>
            Built from what is in Materials and what is due on Calendar — not a plan you
            wrote, one Αθηνα is keeping current.
          </p>
        </div>

        {/* Why this order. Rendered only when commit_roadmap had a real signal to
            cite: order_rationale is null when the ranking found nothing to say. */}
        {goal.order_rationale ? (
          <div className="section">
            <div className={styles.deriveAlert}>
              <span className={styles.deriveIcon} aria-hidden="true">
                ◆
              </span>
              <div>
                <p className={styles.deriveTitle}>
                  This order came from your check-ins, not a template
                </p>
                <p className={styles.deriveBody}>{goal.order_rationale}</p>
              </div>
            </div>

            {researched.length ? (
              <div className={styles.researchNote}>
                <span aria-hidden="true">◆</span>
                <p>
                  <strong>
                    {researched.length} stage{researched.length === 1 ? "" : "s"}
                  </strong>{" "}
                  {researched.length === 1 ? "is" : "are"} not grounded in anything you have
                  uploaded yet — upload material for{" "}
                  {researched.map((m) => m.title).join(", ")} and Αθηνα will tie{" "}
                  {researched.length === 1 ? "it" : "them"} to your own notes.
                </p>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="section">
          <div className="section-head">
            <h2>Current goal</h2>
          </div>

          <div className={styles.goalCard}>
            <div className={styles.goalCardTop}>
              <div>
                <h3>{goal.short_name || goal.title}</h3>
                {subParts.length ? (
                  <p className={styles.sub}>{subParts.join(" · ")}</p>
                ) : null}
              </div>
              <span className={styles.stagePill}>
                {goal.done_count} of {goal.total_count} stage
                {goal.total_count === 1 ? "" : "s"}
              </span>
            </div>
            <div className={styles.progressTrack}>
              <div className={styles.progressFill} style={{ width: `${goal.percent}%` }} />
            </div>
            <p className={styles.progressLabel}>
              {goal.percent}% through the roadmap
              {goal.milestones.find((m) => m.progress_status === "current")
                ? ` · ${goal.milestones.find((m) => m.progress_status === "current")!.title} is next up`
                : goal.total_count
                  ? " · every stage is done"
                  : ""}
            </p>
          </div>
        </div>

        <Roadmap goalId={goal.id} initial={goal.milestones} />

        {goal.topic_strengths.length ? (
          <div className="section">
            <div className="section-head">
              <h2>Topic strength</h2>
              <Link href="/materials" className={styles.subtle}>
                Full breakdown
              </Link>
            </div>
            <ul className={styles.topicGrid}>
              {goal.topic_strengths.map((topic) => (
                <li key={topic.topic_id}>
                  <Link href={`/materials/${topic.topic_id}`} className={styles.topicChip}>
                    <span className={styles.label}>{topic.name}</span>
                    <span className={`${styles.strength} ${styles[topic.strength]}`}>
                      {topic.strength === "unknown"
                        ? "No signal"
                        : topic.strength === "fair"
                          ? "Building"
                          : topic.strength === "weak"
                            ? "Weak"
                            : "Strong"}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <p className="footnote">
          Ordered from your topic scores and Calendar deadlines
          {goal.updated_at ? (
            <>
              {" · last updated "}
              <When iso={goal.updated_at} />
            </>
          ) : null}
        </p>
      </div>

      <ChatLauncher />
    </>
  );
}
