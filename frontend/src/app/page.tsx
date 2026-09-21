const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

type Health = {
  status: string;
  sqlite: { schema_version: string | null; pending_migrations: number };
  hermes: boolean;
};

async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await getHealth();

  return (
    <main style={{ maxWidth: 640, margin: "0 auto", padding: "96px 24px" }}>
      <h1 style={{ fontSize: 44, margin: 0 }}>Αθηνα</h1>
      <p style={{ color: "var(--ink-soft)", marginTop: 8 }}>
        Scaffolding only — pages land in Steps 7&ndash;10.
      </p>

      <ul style={{ listStyle: "none", padding: 0, marginTop: 40, lineHeight: 2 }}>
        <li>backend: {health ? "reachable" : "unreachable"}</li>
        <li>
          schema:{" "}
          {health?.sqlite.schema_version
            ? `${health.sqlite.schema_version} (${health.sqlite.pending_migrations} pending)`
            : "not migrated"}
        </li>
        <li>hermes: {health?.hermes ? "reachable" : "unreachable"}</li>
      </ul>
    </main>
  );
}
