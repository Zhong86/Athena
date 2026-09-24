import Link from "next/link";

import {
  INGEST_LABEL,
  UPLOAD_GLYPH,
  type GatherPendingFile,
  type GatherRunMaterials,
  type GatherTopicFile,
} from "@/lib/api";

import { SourceName } from "../materials/SourceName";
import materialsStyles from "../materials/materials.module.css";
import styles from "./[id]/run.module.css";

/**
 * What a gather run actually added, grouped by topic -- reusing the same
 * file-row markup the topic-detail page renders (`materials.module.css`),
 * since this is the same fact ("here's what backs this topic") just scoped
 * to one run instead of the whole topic. A file can appear under more than
 * one topic here, same as there: tagging is per-chunk, not per-file.
 */
export function GatherMaterials({ materials }: { materials: GatherRunMaterials }) {
  const empty =
    materials.topics.length === 0 &&
    materials.pending.length === 0 &&
    materials.removed.length === 0 &&
    materials.skipped.length === 0;

  if (empty) {
    return (
      <div className="empty-state">
        <strong>Nothing added</strong>
        This run found nothing worth keeping.
      </div>
    );
  }

  return (
    <div className={styles.materialsGroups}>
      {materials.topics.map((topic) => (
        <div key={topic.topic_id} className={styles.materialsGroup}>
          <Link href={`/materials/${topic.topic_id}`} className={styles.groupHeading}>
            {topic.topic_name}
          </Link>
          <ul className={materialsStyles.fileList}>
            {topic.files.map((file) => (
              <TopicFileRow key={file.source_file_id} file={file} />
            ))}
          </ul>
        </div>
      ))}

      {materials.pending.length ? (
        <div className={styles.materialsGroup}>
          <span className={styles.groupHeading}>Still processing</span>
          <ul className={materialsStyles.fileList}>
            {materials.pending.map((file) => (
              <PendingFileRow key={file.source_file_id} file={file} />
            ))}
          </ul>
        </div>
      ) : null}

      {materials.removed.length ? (
        <div className={styles.materialsGroup}>
          <span className={styles.groupHeading}>No longer in your Materials</span>
          <p className={styles.skippedHint}>
            Added by this run, since deleted -- shown by the name it had then.
          </p>
          <ul className={materialsStyles.fileList}>
            {materials.removed.map((name) => (
              <li key={name} className={materialsStyles.fileRow}>
                <div className={styles.meta}>
                  <p className={materialsStyles.fileTitle}>{name}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {materials.skipped.length ? (
        <div className={styles.materialsGroup}>
          <span className={styles.groupHeading}>Skipped</span>
          <p className={styles.skippedHint}>
            Judged and left out of your materials -- not real coursework, or
            too ambiguous to auto-import.
          </p>
          <ul className={materialsStyles.fileList}>
            {materials.skipped.map((name) => (
              <li key={name} className={materialsStyles.fileRow}>
                <div className={styles.meta}>
                  <p className={materialsStyles.fileTitle}>{name}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function TopicFileRow({ file }: { file: GatherTopicFile }) {
  return (
    <li className={materialsStyles.fileRow}>
      <div
        className={`${materialsStyles.fileIcon} ${materialsStyles[file.upload_type]}`}
        aria-hidden="true"
      >
        {UPLOAD_GLYPH[file.upload_type]}
      </div>
      <div className={materialsStyles.meta}>
        <p className={materialsStyles.fileTitle}>
          <SourceName {...file} />
        </p>
        <p className={materialsStyles.fileSub}>
          {file.origin === "drive" ? (
            <span className={materialsStyles.originTag}>Drive</span>
          ) : null}
          {file.chunk_count} {file.chunk_count === 1 ? "chunk" : "chunks"} added
        </p>
      </div>
    </li>
  );
}

function PendingFileRow({ file }: { file: GatherPendingFile }) {
  return (
    <li className={materialsStyles.fileRow}>
      <div
        className={`${materialsStyles.fileIcon} ${materialsStyles[file.upload_type]}`}
        aria-hidden="true"
      >
        {UPLOAD_GLYPH[file.upload_type]}
      </div>
      <div className={materialsStyles.meta}>
        <p className={materialsStyles.fileTitle}>
          <SourceName {...file} />
        </p>
        <p className={materialsStyles.fileSub}>
          {file.origin === "drive" ? (
            <span className={materialsStyles.originTag}>Drive</span>
          ) : null}
          {INGEST_LABEL[file.ingest_status]}
        </p>
      </div>
    </li>
  );
}
