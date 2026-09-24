"use client";

import { useState, useTransition } from "react";

import { saveGatherInterval } from "./actions";
import styles from "./settings.module.css";
import type { GatherIntervalConfig } from "@/lib/api";

type Interval = GatherIntervalConfig["interval"];

const CRON_OPTIONS: { value: Interval; label: string }[] = [
  { value: "daily", label: "Once a day" },
  { value: "weekly", label: "Once a week" },
  { value: "biweekly", label: "Once in 2 weeks" },
];

type Props = {
  /** Fetched server-side (`GET /materials/gather/interval`) so the select
      renders its real value on first paint instead of flashing the default. */
  initialInterval: Interval;
};

export function SettingsPanel({ initialInterval }: Props) {
  const [interval, setInterval_] = useState(initialInterval);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function onChange(next: Interval) {
    const previous = interval;
    setError(null);
    setInterval_(next);
    startTransition(async () => {
      const res = await saveGatherInterval(next);
      if (res.error) {
        setError(res.error);
        setInterval_(previous);
      }
    });
  }

  return (
    <>
      <div className="section">
        <div className="section-head">
          <h2>General</h2>
        </div>
        <div className={styles.group}>
          {error ? <div className="banner-error">{error}</div> : null}
          <div className={`${styles.row} ${styles.rowStacked} ${styles.rowChild}`}>
            <span>
              <p className={styles.label}>CRON routine</p>
              <p className={styles.desc}>
                How often the background runs happen — the ones that show up under
                Knowledge-Sync.
              </p>
            </span>
            <div className={styles.cron}>
              <select
                value={interval}
                onChange={(e) => onChange(e.target.value as Interval)}
                disabled={pending}
                className={styles.select}
                aria-label="CRON routine"
              >
                {CRON_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
