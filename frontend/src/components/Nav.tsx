import Link from "next/link";

// Calendar is deliberately absent: per the plan's nav cleanup, Calendar is
// backend-only (synced into calendar_events) and never a nav destination.
const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/goal", label: "Goal" },
  { href: "/materials", label: "Materials" },
  { href: "/sessions", label: "Sessions" },
  { href: "/settings", label: "Settings" },
];

export function Nav({ active }: { active?: string }) {
  return (
    <nav className="navbar">
      <div className="nav-inner">
        <Link href="/" className="wordmark">
          Αθηνα
        </Link>

        <ul className="nav-links">
          {LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className={link.label === active ? "active" : undefined}
                aria-current={link.label === active ? "page" : undefined}
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>

        <div className="nav-right">
          <div className="avatar">Z</div>
        </div>
      </div>
    </nav>
  );
}
