import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";
import { ApiError, type Connection, getConnections } from "@/lib/api";

import { GoogleConnection } from "./GoogleConnection";
import { SettingsPanel } from "./SettingsPanel";

export const metadata = { title: "Settings · Αθηνα" };

export default async function SettingsPage() {
  // The connection section is real; the switches below it are still
  // localStorage. Failing to reach the backend must not blank the page, so the
  // error is rendered inside the card instead of thrown.
  let google: Connection | null = null;
  let secretsReady = true;
  let hermesDestination = "unknown";
  let loadError: string | null = null;

  try {
    const page = await getConnections();
    google = page.items.find((c) => c.slug === "google") ?? null;
    secretsReady = page.secrets_ready;
    hermesDestination = page.hermes_destination;
  } catch (err) {
    loadError =
      err instanceof ApiError
        ? err.message
        : "Could not load your connections.";
  }

  return (
    <>
      <Nav active="Settings" />

      <div className="shell">
        <div className="greeting">
          <h1>What Αθηνα is allowed to do.</h1>
          <p>
            Every switch here is a permission, not a preference — each one lets
            Αθηνα act on your material without being asked.
          </p>
        </div>

        <GoogleConnection
          connection={google}
          secretsReady={secretsReady}
          hermesDestination={hermesDestination}
          loadError={loadError}
        />

        <SettingsPanel />

        <p className="footnote">
          Google Drive is live; the switches below are still browser-only
        </p>
      </div>

      <ChatLauncher />
    </>
  );
}
