/**
 * Placeholder dashboard content — NONE OF THIS IS REAL.
 *
 * The dashboard is being built visual-first, so these stand in for data that
 * later steps produce. Every field maps to a column that already exists or is
 * planned, so swapping in the real query is a one-file change:
 *
 *   checkIn    <- sessions WHERE type = 'cron' ORDER BY started_at DESC LIMIT 1
 *   weakTopics <- topics.user_understanding, once scoring lands (Step 4/7)
 *   deadlines  <- calendar_events, ranked by due date x topic weakness
 *
 * Delete this file once those are wired; nothing else should import it.
 */

export type CheckIn = {
  headline: string;
  body: string;
};

export type WeakTopic = {
  id: string;
  name: string;
};

export type Deadline = {
  id: string;
  title: string;
  /** Course code, or null when the event has no course attached. */
  course: string | null;
  /** Set when this deadline was bumped up because it touches a weak topic. */
  weakTopic: string | null;
  dueLabel: string;
  urgency: "urgent" | "soon" | "later";
  /** Whether the row offers a "Start" action rather than a due-date label. */
  startable: boolean;
};

export const DUMMY_CHECK_IN: CheckIn = {
  headline: "Entropy is still a weak spot",
  body:
    "Hermes noticed you're slower and less confident on entropy questions " +
    "specifically — not thermodynamics overall. That's why entropy-related " +
    "work is prioritized below.",
};

export const DUMMY_WEAK_TOPICS: WeakTopic[] = [
  { id: "entropy", name: "Entropy" },
  { id: "sql-joins", name: "SQL joins" },
];

export const DUMMY_DEADLINES: Deadline[] = [
  {
    id: "1",
    title: "Lab report — titration",
    course: "CHEM 2010",
    weakTopic: "Entropy",
    dueLabel: "due in 3 days",
    urgency: "urgent",
    startable: true,
  },
  {
    id: "2",
    title: "Problem Set 4",
    course: "CS 2110",
    weakTopic: null,
    dueLabel: "due tomorrow",
    urgency: "urgent",
    startable: false,
  },
  {
    id: "3",
    title: "SQL practice set",
    course: "CS 2110",
    weakTopic: "SQL joins",
    dueLabel: "due in 5 days",
    urgency: "soon",
    startable: false,
  },
  {
    id: "4",
    title: "Reading response 6",
    course: "ENGL 2100",
    weakTopic: null,
    dueLabel: "due next week",
    urgency: "later",
    startable: false,
  },
];
