import Link from "next/link";
import { notFound } from "next/navigation";

import { EvidenceList } from "@/components/EvidenceList";
import { Nav, type DrillNav } from "@/components/Nav";
import { When } from "@/components/When";
import {
  ApiError,
  getTopic,
  getTopicChunks,
  listTopics,
  listUnderstandingEvents,
  UPLOAD_GLYPH,
  type ChunkPage,
  type Topic,
  type TopicDetail,
  type TopicSource,
  type UnderstandingEvent,
} from "@/lib/api";

import styles from "../materials.module.css";
import { SourceName } from "../SourceName";
import { TopicHeader } from "./TopicHeader";

export const metadata = { title: "Topic · Αθηνα" };

/** The mockup's "9 of 34 chunks tagged to Entropy · rest tagged to …" line. */
function sourceLine(source: TopicSource, topicName: string): string {
  const noun = source.chunks_in_topic === 1 ? "chunk" : "chunks";
  const head = `${source.chunks_in_topic} of ${source.chunks_total} ${noun} tagged to ${topicName}`;
  // No "pasted text" suffix: upload_type 'text' covers .txt and .md files too,
  // so it called uploaded files pasted -- and pasting is no longer a way in.
  // The TXT glyph already says what kind of file this is.
  if (source.other_topics.length) {
    return `${head} · rest tagged to ${source.other_topics.join(", ")}`;
  }
  return head;
}

export default async function TopicDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  const topicId = Number(id);
  if (!Number.isInteger(topicId) || topicId < 1) notFound();

  let topic: TopicDetail | null = null;
  let error: string | null = null;
  try {
    topic = await getTopic(topicId);
  } catch (err) {
    if (err instanceof ApiError && err.message.includes("not found")) notFound();
    error = err instanceof ApiError ? err.message : "Something went wrong.";
  }

  if (!topic) {
    return (
      <>
        <Nav active="Materials" />
        <div className="shell">
          <div className="banner-error">{error}</div>
          <Link href="/materials" className="btn-inline ghost">
            ← Back to materials
          </Link>
        </div>
      </>
    );
  }

  // All three are nice-to-have: losing one should cost that block, not the page.
  const [siblings, chunks, events] = await Promise.all([
    listTopics().catch((): Topic[] => []),
    getTopicChunks(topicId, 20).catch((): ChunkPage | null => null),
    listUnderstandingEvents(topicId).catch((): UnderstandingEvent[] => []),
  ]);

  const drill: DrillNav = {
    back: { href: "/materials", label: "Materials" },
    title: "TOPICS",
    items: (siblings.length ? siblings : [{ ...topic, source_count: 0 }]).map((t) => ({
      href: `/materials/${t.id}`,
      label: t.name,
      meta: `${t.chunk_count}`,
      active: t.id === topic.id,
    })),
  };

  return (
    <>
      <Nav active="Materials" drill={drill} />

      <div className="shell">
        <TopicHeader topic={topic} />

        {topic.user_understanding < 0 ? (
          <div className="section">
            <div className="empty-state">
              <strong>No confidence score yet</strong>
              Αθηνα scores a topic from quizzes and chat sessions. Until one of those
              happens this stays unscored rather than guessing at a number.
            </div>
          </div>
        ) : null}

        <div className="section">
          <div className="section-head">
            <h2>Materials</h2>
            <span className={styles.subtle}>
              {topic.chunk_count} chunk{topic.chunk_count === 1 ? "" : "s"} ·{" "}
              {topic.sources.length} source{topic.sources.length === 1 ? "" : "s"}
            </span>
          </div>

          {topic.sources.length ? (
            <ul className={styles.fileList}>
              {topic.sources.map((source) => (
                <li key={source.source_file_id} className={styles.fileRow}>
                  <div
                    className={`${styles.fileIcon} ${styles[source.upload_type]}`}
                    aria-hidden="true"
                  >
                    {UPLOAD_GLYPH[source.upload_type]}
                  </div>
                  <div className={styles.meta}>
                    <p className={styles.fileTitle}>
                      <SourceName {...source} />
                    </p>
                    <p className={styles.fileSub}>
                      {source.origin === "drive" ? (
                        <span className={styles.originTag}>Drive</span>
                      ) : null}
                      {sourceLine(source, topic.name)}
                    </p>
                  </div>
                  <span className={styles.subtle}>
                    <When iso={source.uploaded_at} />
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty-state">
              <strong>Nothing tagged here yet</strong>
              Chunks appear once a file that covers this topic finishes ingesting.
            </div>
          )}
        </div>

        <div className="section">
          <div className="section-head">
            <h2>Chunks</h2>
            {chunks ? (
              <span className={styles.subtle}>
                showing {chunks.items.length} of {chunks.total}
              </span>
            ) : null}
          </div>

          {chunks?.items.length ? (
            <ul className={styles.chunkList}>
              {chunks.items.map((chunk) => (
                <li key={chunk.id} className={styles.chunkRow}>
                  <p className={styles.chunkSource}>
                    {chunk.filename} · #{chunk.order_index + 1}
                  </p>
                  <p className={styles.chunkText}>{chunk.text}</p>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty-state">
              <strong>No chunks to show</strong>
              These are the exact passages Αθηνα retrieves when it quizzes you on this
              topic.
            </div>
          )}
        </div>

        {/* The mockup's evidence trail. Every recorded move of this topic's
            score, with the reason the grader gave for it. */}
        <div className="section">
          <div className="section-head">
            <h2>What&rsquo;s building this score</h2>
            {events.length ? (
              <span className={styles.subtle}>
                {events.length} change{events.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>
          {events.length ? (
            <EvidenceList events={events} />
          ) : (
            <div className="empty-state">
              <strong>No evidence yet</strong>
              Quizzes and chat sessions about this topic will be listed here, with what
              each one moved the score by.
            </div>
          )}
        </div>

        <p className="footnote">Feeds your Goal roadmap and Dashboard priorities</p>
      </div>
    </>
  );
}
