"use client";

import { useState, useTransition } from "react";

import { When } from "@/components/When";
import {
  CONNECTION_LABEL,
  type Capability,
  type Connection,
  scopeLabel,
} from "@/lib/api";

import {
  disconnectGoogleAction,
  finishGoogleConnect,
  startGoogleConnect,
  toggleCapability,
} from "./actions";
import styles from "./google.module.css";
import settingsStyles from "./settings.module.css";

/**
 * Three steps, because Hermes needs a *Desktop app* OAuth client and Google
 * only lets those redirect to loopback -- which is the user's own machine, not
 * our server. So there is no callback we can receive: they grant consent, land
 * on a dead page, and paste the URL back. Same shape as Hermes' own console
 * flow, minus the terminal.
 */
type Props = {
  connection: Connection | null;
  secretsReady: boolean;
  hermesDestination: string;
  /** Null when the backend could not be reached at all. */
  loadError: string | null;
};

const DRIVE: Capability = "drive.read";

export function GoogleConnection({
  connection,
  secretsReady,
  hermesDestination,
  loadError,
}: Props) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  // Held in state rather than read from `connection`: the consent URL is built
  // from the uploaded client secret and is never persisted, so it exists only
  // for this browser session. A reload lands the user back on step 1 with the
  // row still in `authorizing`, which the copy below explains.
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [picked, setPicked] = useState<File | null>(null);
  const [confirming, setConfirming] = useState(false);

  const status = connection?.status ?? "disconnected";
  const connected = status === "connected";
  const awaitingCode = Boolean(authorizeUrl) || status === "authorizing";

  function run(work: () => Promise<{ error: string | null }>) {
    setError(null);
    startTransition(async () => {
      const result = await work();
      if (result.error) setError(result.error);
    });
  }

  function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!picked) {
      setError("Choose the client secret JSON file first.");
      return;
    }
    const form = new FormData();
    form.append("file", picked);
    run(async () => {
      const result = await startGoogleConnect(form);
      if (result.data) {
        setAuthorizeUrl(result.data.authorize_url);
        setPicked(null);
      }
      return result;
    });
  }

  function finish(event: React.FormEvent) {
    event.preventDefault();
    run(async () => {
      const result = await finishGoogleConnect(pasted);
      if (result.data) {
        setPasted("");
        setAuthorizeUrl(null);
      }
      return result;
    });
  }

  function disconnect() {
    setConfirming(false);
    setWarning(null);
    run(async () => {
      const result = await disconnectGoogleAction();
      setAuthorizeUrl(null);
      if (result.warning) setWarning(result.warning);
      return result;
    });
  }

  return (
    <div className="section">
      <div className="section-head">
        <h2>Google Drive</h2>
        <span className={styles.status} data-status={status}>
          {CONNECTION_LABEL[status]}
        </span>
      </div>

      <div className={settingsStyles.group}>
        {loadError ? (
          <div className="banner-error">{loadError}</div>
        ) : null}
        {error ? <div className="banner-error">{error}</div> : null}
        {warning ? (
          <p className={`${settingsStyles.row} ${settingsStyles.warn}`}>
            Disconnected here, but not cleanly: {warning}
          </p>
        ) : null}

        {!secretsReady ? (
          <div className={settingsStyles.notice}>
            <strong>Credential storage isn&rsquo;t configured.</strong> Set{" "}
            <code>CONNECTIONS_SECRET_KEY</code> in the backend environment and
            restart it. Until then connecting is refused rather than storing your
            Google credentials unencrypted.
          </div>
        ) : null}

        {connected ? (
          <Connected
            connection={connection!}
            pending={pending}
            confirming={confirming}
            onConfirm={setConfirming}
            onDisconnect={disconnect}
            onToggle={(enabled) =>
              run(() => toggleCapability(connection!.slug, DRIVE, enabled))
            }
          />
        ) : (
          <>
            <Instructions />

            <div className={styles.steps}>
              <Step
                index={1}
                title="Upload the client secret file"
                done={awaitingCode}
              >
                <form onSubmit={upload} className={styles.stepBody}>
                  {/* The label is the click target, so no click handler is
                      needed -- same trick as the materials drop zone. */}
                  <input
                    id="google-client-json"
                    type="file"
                    accept=".json,application/json"
                    className="sr-only"
                    onChange={(e) => {
                      setPicked(e.target.files?.[0] ?? null);
                      setError(null);
                    }}
                    disabled={pending || !secretsReady}
                  />
                  <label htmlFor="google-client-json" className={styles.dropZone}>
                    {picked ? (
                      <strong>{picked.name}</strong>
                    ) : (
                      <>
                        <strong>Choose the JSON file</strong>
                        <span>
                          The one Google Cloud Console downloaded — don&rsquo;t
                          edit it first.
                        </span>
                      </>
                    )}
                  </label>
                  <button
                    type="submit"
                    className="btn-inline"
                    disabled={pending || !picked || !secretsReady}
                  >
                    {pending ? "Sending…" : "Upload and get consent link"}
                  </button>
                </form>
              </Step>

              <Step index={2} title="Grant access in your browser" done={false}>
                {authorizeUrl ? (
                  <div className={styles.stepBody}>
                    <a
                      href={authorizeUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="btn"
                    >
                      Open Google&rsquo;s consent screen
                    </a>
                    <p className={settingsStyles.hint}>
                      After you choose <strong>Allow</strong>, the page will fail
                      to load — that is expected and means it worked. Nothing is
                      listening on that address; the code you need is in the
                      browser&rsquo;s address bar.
                    </p>
                  </div>
                ) : status === "authorizing" ? (
                  <p className={settingsStyles.hint}>
                    A connection was started but the consent link isn&rsquo;t in
                    this browser any more. Upload the file again above to get a
                    fresh one.
                  </p>
                ) : (
                  <p className={settingsStyles.hint}>
                    Available once the file is uploaded.
                  </p>
                )}
              </Step>

              <Step index={3} title="Paste the address back here" done={false}>
                <form onSubmit={finish} className={styles.stepBody}>
                  <input
                    type="text"
                    className={settingsStyles.input}
                    placeholder="http://localhost:9004/?code=4/0A…"
                    value={pasted}
                    onChange={(e) => setPasted(e.target.value)}
                    disabled={pending || !awaitingCode}
                    aria-label="Redirect URL or authorization code"
                  />
                  <button
                    type="submit"
                    className="btn-inline"
                    disabled={pending || !awaitingCode || !pasted.trim()}
                  >
                    {pending ? "Connecting…" : "Finish connecting"}
                  </button>
                  <p className={settingsStyles.hint}>
                    Copy the whole address, or just the <code>code</code> value if
                    you prefer. It is exchanged on the server and never stored in
                    your browser.
                  </p>
                </form>
              </Step>
            </div>
          </>
        )}

        <p className={`${settingsStyles.row} ${settingsStyles.hint}`}>
          Credentials are written to the Hermes host at{" "}
          <code>{hermesDestination}</code>. Hermes may need a restart to pick up
          new files.
        </p>
      </div>
    </div>
  );
}

function Connected({
  connection,
  pending,
  confirming,
  onConfirm,
  onDisconnect,
  onToggle,
}: {
  connection: Connection;
  pending: boolean;
  confirming: boolean;
  onConfirm: (value: boolean) => void;
  onDisconnect: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  // Absent means Google never granted a Drive scope, so there is nothing to
  // toggle -- distinct from granted-but-switched-off.
  const granted = connection.capabilities[DRIVE];

  return (
    <>
      <div className={`${settingsStyles.row} ${styles.account}`}>
        <span className={settingsStyles.rowText}>
          <span className={settingsStyles.label}>
            {connection.account_label ?? "Google account"}
          </span>
          <span className={settingsStyles.desc}>
            {connection.connected_at ? (
              <>
                Connected <When iso={connection.connected_at} />
              </>
            ) : (
              "Connected"
            )}
          </span>
        </span>
        {confirming ? (
          <span className={styles.confirm}>
            <button
              type="button"
              className="btn-inline ghost"
              onClick={() => onConfirm(false)}
              disabled={pending}
            >
              Keep it
            </button>
            <button
              type="button"
              className={`btn-inline ${styles.danger}`}
              onClick={onDisconnect}
              disabled={pending}
            >
              {pending ? "Disconnecting…" : "Yes, disconnect"}
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn-inline ghost"
            onClick={() => onConfirm(true)}
            disabled={pending}
          >
            Disconnect
          </button>
        )}
      </div>

      {connection.scopes.length ? (
        <div className={`${settingsStyles.row} ${settingsStyles.rowStacked}`}>
          <span className={settingsStyles.label}>What you granted Google</span>
          <ul className={styles.scopes}>
            {connection.scopes.map((scope) => (
              <li key={scope}>{scopeLabel(scope)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {granted === undefined ? (
        <p className={`${settingsStyles.row} ${settingsStyles.warn}`}>
          Google didn&rsquo;t grant a Drive scope, so Αθηνα still can&rsquo;t read
          your files. Disconnect and connect again, accepting the Drive
          permission on the consent screen.
        </p>
      ) : (
        <label className={`${settingsStyles.row} ${settingsStyles.rowChild}`}>
          <span className={settingsStyles.rowText}>
            <span className={settingsStyles.label}>Read my Drive files</span>
            <span className={settingsStyles.desc}>
              Lets Αθηνα turn lecture notes and problem sets into Materials
              without re-uploading them. Switching this off keeps the connection
              but stops the reads.
            </span>
          </span>
          <span className={settingsStyles.switch}>
            <input
              type="checkbox"
              checked={granted}
              onChange={(e) => onToggle(e.target.checked)}
              disabled={pending}
            />
            <span className={settingsStyles.track} />
          </span>
        </label>
      )}
    </>
  );
}

function Step({
  index,
  title,
  done,
  children,
}: {
  index: number;
  title: string;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.step} data-done={done || undefined}>
      <span className={styles.stepIndex} aria-hidden>
        {index}
      </span>
      <div className={styles.stepMain}>
        <p className={styles.stepTitle}>{title}</p>
        {children}
      </div>
    </div>
  );
}

/** Collapsed by default: it is a one-time chore, and an open wall of numbered
    steps buries the actual controls underneath it. */
function Instructions() {
  return (
    <details className={styles.instructions}>
      <summary>First time? Create the Google credentials</summary>
      <ol>
        <li>
          Open the{" "}
          <a
            href="https://console.cloud.google.com/"
            target="_blank"
            rel="noreferrer"
          >
            Google Cloud Console
          </a>{" "}
          and create a project.
        </li>
        <li>
          Under <strong>APIs &amp; Services → Library</strong>, enable the{" "}
          <strong>Google Drive API</strong>. Enable Gmail, Calendar, Sheets, Docs
          or People too if you want Athena to reach those later.
        </li>
        <li>
          Under <strong>APIs &amp; Services → Credentials</strong>, create an{" "}
          <strong>OAuth 2.0 Client ID</strong> with application type{" "}
          <strong>Desktop app</strong>. Desktop matters — a Web application
          client can&rsquo;t use the paste-the-code flow below.
        </li>
        <li>
          While you are there, add your own Google account as a{" "}
          <strong>test user</strong> on the OAuth consent screen, or Google will
          refuse the grant.
        </li>
        <li>Download the client secret JSON and upload it below.</li>
      </ol>
    </details>
  );
}
