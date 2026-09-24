import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import {
  ApiError,
  getTestMode,
  listTopics,
  listUploads,
  type SourceFile,
  type Topic,
} from "@/lib/api";

import styles from "./materials.module.css";
import { ResetButton } from "./ResetButton";
import { TopicCard } from "./TopicCard";
import { UploadPanel } from "./UploadPanel";

export const metadata = { title: "Materials · Αθηνα" };

export default async function MaterialsPage() {
  let topics: Topic[] = [];
  let uploads: SourceFile[] = [];
  let error: string | null = null;

  try {
    [topics, uploads] = await Promise.all([listTopics(), listUploads()]);
  } catch (err) {
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  // Absent entirely outside TEST_MODE, not just hidden -- the fetch failing
  // (backend unreachable, whatever) must not accidentally show a reset
  // button that would just 404 when clicked.
  const testMode = await getTestMode().catch(() => ({ enabled: false }));

  const chunkTotal = topics.reduce((sum, topic) => sum + topic.chunk_count, 0);
  // -1 is "no signal yet", so it must not be counted as a weak score.
  const unscored = topics.filter((topic) => topic.user_understanding < 0).length;

  return (
    <>
      <Nav active="Materials" />

      <div className="shell">
        <div className="greeting">
          <h1>What Αθηνα has to work with.</h1>
          <p>
            Everything you upload is split into chunks and each chunk is tagged with one
            topic — so a topic is a set of chunks, not a set of files.
          </p>
        </div>

        {error ? <div className="banner-error">{error}</div> : null}

        {testMode.enabled ? <ResetButton /> : null}

        <div className="section">
          <div className="section-head">
            <h2>Topics</h2>
            {topics.length ? (
              <span className={styles.subtle}>
                {topics.length} topic{topics.length === 1 ? "" : "s"} · {chunkTotal}{" "}
                chunk{chunkTotal === 1 ? "" : "s"}
                {unscored ? ` · ${unscored} not scored yet` : ""}
              </span>
            ) : null}
          </div>

          {topics.length ? (
            <ul className={styles.topicGrid}>
              {topics.map((topic) => (
                <TopicCard key={topic.id} topic={topic} />
              ))}
            </ul>
          ) : !error ? (
            <div className="empty-state">
              <strong>No topics yet</strong>
              Topics are created by Αθηνα as it reads your material — add something
              below and they will appear here.
            </div>
          ) : null}
        </div>

        <div className="section">
          <div className="section-head">
            <h2>Sources</h2>
            {uploads.length ? (
              <span className={styles.subtle}>
                {uploads.length} file{uploads.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>

          <UploadPanel initial={uploads} />
        </div>

        <p className="footnote">Feeds quiz generation, confidence scores and your Goal roadmap</p>
      </div>

      <ChatLauncher />
    </>
  );
}
