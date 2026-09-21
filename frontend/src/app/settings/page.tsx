import { ChatLauncher } from "@/components/ChatLauncher";
import { Nav } from "@/components/Nav";

import { SettingsPanel } from "./SettingsPanel";

export const metadata = { title: "Settings · Αθηνα" };

export default function SettingsPage() {
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

        <SettingsPanel />

        <p className="footnote">Placeholder — not yet backed by the API</p>
      </div>

      <ChatLauncher />
    </>
  );
}
