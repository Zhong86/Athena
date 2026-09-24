import Link from "next/link";
import { notFound } from "next/navigation";

import { GetStartedGuide } from "@/components/GetStartedGuide";
import { Nav, type DrillNav } from "@/components/Nav";
import { TraceTimeline } from "@/components/SessionTrace";
import { When } from "@/components/When";
import {
  ApiError,
  getGatherRunMaterials,
  getSession,
  listSessions,
  titleOf,
  traceOf,
  TYPE_LABEL,
  type GatherRunMaterials,
  type Session,
} from "@/lib/api";

import { GatherMaterials } from "../GatherMaterials";
import styles from "./run.module.css";

export const metadata = { title: "Run · Αθηνα" };

export default async function RunDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId < 1) notFound();

  let session: Session | null = null;
  let error: string | null = null;
  try {
    session = await getSession(sessionId);
  } catch (err) {
    if (err instanceof ApiError && err.message.includes("not found")) notFound();
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  if (!session) {
    return (
      <>
        <Nav active="Knowledge-Sync" />
        <div className="shell">
          <div className="banner-error">{error}</div>
          <Link href="/knowledge-sync" className="btn-inline ghost">
            ← Back to Knowledge-Sync
          </Link>
        </div>
      </>
    );
  }

  // /knowledge-sync is the two unprompted types; a chat or quiz reached by
  // guessing an id belongs to its own section, not here.
  if (session.type !== "cron" && session.type !== "agent_action") notFound();

  // A gather run's payload is file ids, not a Hermes trace -- see
  // GatherRunMaterials's docstring for why that's resolved here instead of
  // stored on the session. Other cron/agent_action sessions keep the
  // generic trace timeline below.
  const isGatherRun = session.payload?.kind === "materials_gather";
  let materials: GatherRunMaterials | null = null;
  let materialsError: string | null = null;
  if (isGatherRun) {
    try {
      materials = await getGatherRunMaterials(session.id);
    } catch (err) {
      materialsError = err instanceof ApiError ? err.message : "Something went wrong.";
    }
  }

  const trace = traceOf(session);

  // Desktop D2: sibling runs take over the rail. Losing them costs the rail
  // only -- the back link and the run itself still work.
  let siblings: Session[] = [];
  try {
    const [findings, actions] = await Promise.all([
      listSessions({ type: "cron", limit: 25 }),
      listSessions({ type: "agent_action", limit: 25 }),
    ]);
    siblings = [...findings.items, ...actions.items].sort((a, b) =>
      b.started_at.localeCompare(a.started_at),
    );
  } catch {
    siblings = [session];
  }

  const drill: DrillNav = {
    back: { href: "/knowledge-sync", label: "Knowledge-Sync" },
    title: "RUNS",
    items: siblings.map((row) => ({
      href: `/knowledge-sync/${row.id}`,
      label: titleOf(row),
      meta: TYPE_LABEL[row.type],
      active: row.id === session.id,
    })),
  };

  return (
    <>
      <Nav active="Knowledge-Sync" drill={drill} />

      <div className="shell">
        <div className={styles.head}>
          <p className={styles.eyebrow}>
            {TYPE_LABEL[session.type]} · <When iso={session.started_at} />
          </p>
          <h1 className={styles.title}>{titleOf(session)}</h1>
          {session.summary ? (
            // The run's own conclusion, stated before the working that
            // produced it -- you should not have to read a tool log to find
            // out what Αθηνα decided.
            <p className={styles.conclusion}>{session.summary}</p>
          ) : null}
        </div>

        {isGatherRun ? (
          <div className="section">
            <div className="section-head">
              <h2>Materials added</h2>
            </div>

            {materialsError ? (
              <div className="banner-error">{materialsError}</div>
            ) : materials ? (
              <GatherMaterials materials={materials} />
            ) : null}
          </div>
        ) : (
          <>
            <div className="section">
              <div className="section-head">
                <h2>How it got there</h2>
                {trace.length ? (
                  <span className={styles.count}>
                    {trace.length} {trace.length === 1 ? "step" : "steps"}
                  </span>
                ) : null}
              </div>

              {trace.length ? (
                <TraceTimeline trace={trace} />
              ) : (
                <div className="empty-state">
                  <strong>No trace recorded</strong>
                  This run finished without reporting its steps — older runs
                  predate step-by-step tracing.
                </div>
              )}
            </div>

            <p className="footnote">
              Tool results are previews: Hermes truncates each to 500 characters.
            </p>
          </>
        )}
      </div>

      <GetStartedGuide />
    </>
  );
}
