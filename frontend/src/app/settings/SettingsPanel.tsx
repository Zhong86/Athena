"use client";

import { useState, useSyncExternalStore } from "react";

import styles from "./settings.module.css";

/* Temporary screen: there is no settings endpoint yet, so the whole thing
   lives in localStorage. When the backend lands, swap `read`/`write` for
   fetches and the rest of this component stays as it is. */
const STORAGE_KEY = "athena.settings.v1";

type Settings = {
  autoEmbed: boolean;
  scoreFromChat: boolean;
  autoRequiz: boolean;
  externalSources: boolean;
  /* "picked" honours `sites`; "any" ignores it and lets Αθηνα fetch from
     anywhere. The list is kept either way so flipping back to "picked" does
     not lose it. */
  externalScope: "picked" | "any";
  googleAccess: boolean;
  sites: string[];
  cron: string;
};

const DEFAULTS: Settings = {
  autoEmbed: true,
  scoreFromChat: true,
  autoRequiz: false,
  externalSources: false,
  externalScope: "picked",
  googleAccess: false,
  sites: [],
  cron: "daily",
};

const CRON_OPTIONS = [
  { value: "off", label: "Never — only when I ask" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Once a day" },
  { value: "weekly", label: "Once a week" },
];

/** "https://Wikipedia.org/wiki/X" and "wikipedia.org" are the same entry. */
function toHost(input: string) {
  return input
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .replace(/\/.*$/, "");
}

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
  const [site, setSite] = useState("");

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    update((prev) => ({ ...prev, [key]: value }));
  }

  function allow(host: string, on: boolean) {
    update((prev) => ({
      ...prev,
      sites: on
        ? prev.sites.includes(host)
          ? prev.sites
          : [...prev.sites, host]
        : prev.sites.filter((s) => s !== host),
    }));
  }

  function addSite(event: React.FormEvent) {
    event.preventDefault();
    const host = toHost(site);
    if (host) allow(host, true);
    setSite("");
  }

  function toggle(key: keyof Settings, label: string, desc: string) {
    const checked = settings[key] as boolean;
    return (
      <label className={styles.row}>
        {/* spans, not <p>: a <label> only takes phrasing content */}
        <span className={styles.rowText}>
          <span className={styles.label}>{label}</span>
          <span className={styles.desc}>{desc}</span>
        </span>
        <span className={styles.switch}>
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => set(key, e.target.checked as Settings[typeof key])}
          />
          <span className={styles.track} />
        </span>
      </label>
    );
  }

  return (
    <>
      <div className={styles.notice}>
        <strong>Nothing here is wired up yet.</strong> These switches are kept in
        this browser only — they are a placeholder for the real settings endpoint,
        so clearing site data resets them and another device won&rsquo;t see them.
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Materials</h2>
        </div>
        <div className={styles.group}>
          {toggle(
            "autoEmbed",
            "Allow LLM-auto embedding",
            "Αθηνα reads, chunks and tags new uploads on its own. Turn this off and material sits untouched until you ask for it.",
          )}
          {toggle(
            "scoreFromChat",
            "Allow scoring from chat sessions",
            "Confidence scores move based on what you say in a session, not just on quizzes.",
          )}
          {toggle(
            "autoRequiz",
            "Auto re-quiz",
            "Topics you scored badly on come back as a check-in without you asking for one.",
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Goal</h2>
        </div>
        <div className={styles.group}>
          {toggle(
            "externalSources",
            "Allow external sources",
            "Your roadmap can pull in material you didn’t upload — public references Αθηνα finds for a step you are stuck on.",
          )}

          <div
            className={`${styles.row} ${styles.rowStacked} ${styles.rowChild} ${
              settings.externalSources ? "" : styles.rowDisabled
            }`}
            // Hidden from assistive tech when the parent switch is off: the
            // controls below do nothing until it is on.
            aria-hidden={settings.externalSources ? undefined : true}
          >
            <fieldset className={styles.modes}>
              <legend className={styles.label}>Where it may look</legend>

              <label className={styles.mode}>
                <input
                  type="radio"
                  name="external-scope"
                  checked={settings.externalScope === "picked"}
                  onChange={() => set("externalScope", "picked")}
                />
                <span className={styles.rowText}>
                  <span className={styles.modeName}>Only the sources I pick</span>
                  <span className={styles.desc}>
                    Αθηνα may fetch from these domains and nothing else. An empty
                    list means it fetches nothing.
                  </span>
                </span>
              </label>

              <label className={styles.mode}>
                <input
                  type="radio"
                  name="external-scope"
                  checked={settings.externalScope === "any"}
                  onChange={() => set("externalScope", "any")}
                />
                <span className={styles.rowText}>
                  <span className={styles.modeName}>Any resource</span>
                  <span className={styles.desc}>
                    No restriction — anything Αθηνα can reach on the open web is
                    fair game, including sources neither of you have vetted.
                  </span>
                </span>
              </label>
            </fieldset>

            {settings.externalScope === "picked" ? (
              <>
                <form onSubmit={addSite} className={styles.siteForm}>
                  <input
                    type="text"
                    value={site}
                    onChange={(e) => setSite(e.target.value)}
                    placeholder="wikipedia.org"
                    className={styles.input}
                    aria-label="Add a domain"
                  />
                  <button type="submit" className="btn-inline">
                    Add
                  </button>
                </form>

                {settings.sites.length ? (
                  <ul className={styles.chips}>
                    {settings.sites.map((host) => (
                      <li key={host} className={styles.chip}>
                        {host}
                        <button
                          type="button"
                          className={styles.chipRemove}
                          aria-label={`Remove ${host}`}
                          onClick={() => allow(host, false)}
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}

                <p className={styles.hint}>
                  {settings.sites.length
                    ? `${settings.sites.length} domain${
                        settings.sites.length === 1 ? "" : "s"
                      } allowed.`
                    : "No domains allowed yet — Αθηνα will not fetch anything."}
                </p>
              </>
            ) : (
              <p className={styles.warn}>
                Αθηνα can fetch from any site. Your picked domains are kept and come
                back if you switch to the list.
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>General</h2>
        </div>
        <div className={styles.group}>
          {toggle(
            "googleAccess",
            "Google access",
            "Lets Αθηνα read your Calendar and Drive to line the roadmap up with what is actually in your week.",
          )}

          {/* "Websites to access" used to live here as a second list. There is
              only one set of domains Αθηνα may fetch, so it is the Goal
              allowlist above and this row just points at it. */}
          <p className={`${styles.row} ${styles.rowChild} ${styles.hint}`}>
            Which websites Αθηνα may reach is set under Goal → Allow external
            sources.
          </p>

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
