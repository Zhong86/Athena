import { Nav } from "@/components/Nav";
import {
  ApiError,
  type Connection,
  type GatherIntervalConfig,
  getConnections,
  getGatherInterval,
} from "@/lib/api";

import { GoogleConnection } from "./GoogleConnection";
import { ResetEverything } from "./ResetEverything";
import { SettingsPanel } from "./SettingsPanel";

export const metadata = { title: "Settings · Αθηνα" };

export default async function SettingsPage() {
  // Failing to reach the backend must not blank the page, so each section's
  // error is rendered inline instead of thrown.
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

  let interval: GatherIntervalConfig["interval"] = "daily";
  try {
    interval = (await getGatherInterval()).interval;
  } catch {
    // SettingsPanel falls back to the same default and lets the next save
    // retry -- one more failed fetch on this page must not block rendering.
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

        <SettingsPanel initialInterval={interval} />

        <ResetEverything />
      </div>
    </>
  );
}
