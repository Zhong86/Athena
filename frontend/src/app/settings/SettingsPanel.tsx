"use client";

import { useSyncExternalStore } from "react";

import styles from "./settings.module.css";

/* Temporary screen: there is no settings endpoint yet, so the whole thing
   lives in localStorage. When the backend lands, swap `read`/`write` for
   fetches and the rest of this component stays as it is. */
const STORAGE_KEY = "athena.settings.v1";

type Settings = {
  cron: string;
};

const DEFAULTS: Settings = {
  cron: "daily",
};

const CRON_OPTIONS = [
  { value: "daily", label: "Once a day" },
  { value: "weekly", label: "Once a week" },
  { value: "biweekly", label: "Once in 2 weeks" },
];

/* localStorage is an external store, so it is subscribed to rather than
   copied into state: the server render and the hydration pass both get
   DEFAULTS (no mismatch), and the stored values arrive on the pass after. */
const listeners = new Set<() => void>();

// useSyncExternalStore re-renders whenever the snapshot changes identity, so
// the parsed object is cached and only rebuilt when the raw string moves.
let cachedRaw: string | null = null;
let cached: Settings = DEFAULTS;
// Private mode and a full quota both make writes throw. The switches still
// have to move when that happens, so the cache becomes the store instead.
let persists = true;

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // Another tab writing the same key fires `storage` here, not in the tab
  // that wrote it -- hence the explicit notify in write().
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function read(): Settings {
  if (!persists) return cached;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    persists = false;
    return cached;
  }
  if (raw === cachedRaw) return cached;
  cachedRaw = raw;
  try {
    // Spread over the defaults so a key added after something was saved does
    // not come back undefined.
    cached = raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Settings>) } : DEFAULTS;
  } catch {
    cached = DEFAULTS;
  }
  return cached;
}

function readServer(): Settings {
  return DEFAULTS;
}

/**
 * Always derives from the store rather than from a render's closure: two
 * changes in one tick (clicking two sources before React re-renders) would
 * otherwise both start from the same stale object and the first would be lost.
 */
function update(patch: (prev: Settings) => Settings) {
  write(patch(read()));
}

function write(next: Settings) {
  const raw = JSON.stringify(next);
  try {
    window.localStorage.setItem(STORAGE_KEY, raw);
  } catch {
    persists = false;
  }
  cachedRaw = raw;
  cached = next;
  listeners.forEach((fn) => fn());
}

export function SettingsPanel() {
  const settings = useSyncExternalStore(subscribe, read, readServer);

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    update((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <>
      <div className="section">
        <div className="section-head">
          <h2>General</h2>
        </div>
        <div className={styles.group}>
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
                value={settings.cron}
                onChange={(e) => set("cron", e.target.value)}
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
