import type { SourceOrigin } from "@/lib/api";

import styles from "./materials.module.css";

/**
 * A source file's name, as a link when the original lives in Drive.
 *
 * Shared by the Sources list and the topic page: both list the same files, and
 * a row that opens in one place and is dead in the other reads like a bug.
 */
export function SourceName({
  filename,
  origin,
  drive_url,
}: {
  filename: string;
  origin: SourceOrigin;
  drive_url: string | null;
}) {
  if (origin !== "drive" || !drive_url) return <>{filename}</>;

  return (
    <a
      href={drive_url}
      target="_blank"
      rel="noopener noreferrer"
      className={styles.fileLink}
    >
      {filename}
      <span className={styles.external} aria-hidden="true">
        ↗
      </span>
      <span className="sr-only"> — opens in Google Drive</span>
    </a>
  );
}
