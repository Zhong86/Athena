"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";

// Calendar is deliberately absent: there is no Calendar feature. Deadline
// tracking was cut, so nothing backs a destination here.
// "/" is the landing page, not a rail destination — the wordmark below is the
// way back to it, so the rail starts at the dashboard.
const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/goal", label: "Goal" },
  { href: "/materials", label: "Materials" },
  { href: "/sessions", label: "Sessions" },
  { href: "/quizzes", label: "Quizzes" },
  { href: "/knowledge-sync", label: "Knowledge-Sync" },
  { href: "/settings", label: "Settings" },
];

/**
 * The drill-in state of the nav (templates/athena_subnav_concepts.html, D2):
 * the sub-list takes over the rail instead of sitting beside it, so there is
 * only ever one sidebar. On mobile the same rail slides in as a drawer.
 */
export type DrillNav = {
  back: { href: string; label: string };
  title: string;
  items: { href: string; label: string; meta?: string; active?: boolean }[];
  add?: { href: string; label: string };
};

export function Nav({ active, drill }: { active?: string; drill?: DrillNav }) {
  const [open, setOpen] = useState(false);

  // M1's detail screen is titled by the item you drilled into, not by the
  // section — the section name is already the back link.
  const mobileTitle =
    drill?.items.find((item) => item.active)?.label ?? drill?.title ?? active ?? "Αθηνα";

  // The drawer is an overlay, so Escape has to dismiss it and the page behind
  // it must not scroll while it is up.
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open]);

  const rail = drill ? (
    <>
      <Link href={drill.back.href} className="rail-back" onClick={() => setOpen(false)}>
        <span aria-hidden="true">←</span> {drill.back.label}
      </Link>
      <p className="rail-section">{drill.title}</p>
      <ul className="rail-sub">
        {drill.items.map((item) => (
          <li key={item.href}>
            <Link
              href={item.href}
              className={item.active ? "active" : undefined}
              aria-current={item.active ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              <span className="t">{item.label}</span>
              {item.meta ? <span className="m">{item.meta}</span> : null}
            </Link>
          </li>
        ))}
      </ul>
      {drill.add ? (
        <Link href={drill.add.href} className="rail-add" onClick={() => setOpen(false)}>
          {drill.add.label}
        </Link>
      ) : null}
    </>
  ) : (
    <>
      <Link href="/" className="wordmark" onClick={() => setOpen(false)}>
        {/* The mark's linework is cream, which is why the rail itself is
            indigo — it reads directly on that, no chip behind it. */}
        <Image
          className="wordmark-mark"
          src="/logo-mark.png"
          alt=""
          width={460}
          height={320}
          priority
        />
        Αθηνα
      </Link>
      <ul className="rail-nav">
        {LINKS.map((link) => (
          <li key={link.href}>
            <Link
              href={link.href}
              className={link.label === active ? "active" : undefined}
              aria-current={link.label === active ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );

  return (
    <>
      {/* One rail markup, two presentations: fixed on desktop, slid in from
          the left on mobile. `open` only has an effect below the breakpoint.
          The page transition holds this still rather than sliding it with the
          page -- see the `view-transition-class` rules in globals.css. An
          inline `viewTransitionName` can't do that job: <ViewTransition>
          rewrites the style attribute of its children when it captures them. */}
      <nav className={`rail ${open ? "rail-open" : ""}`} aria-label="Main">
        {rail}
        <div className="rail-foot">
          <div className="avatar">Z</div>
        </div>
      </nav>

      {open ? (
        <button
          type="button"
          className="rail-scrim"
          aria-label="Close menu"
          onClick={() => setOpen(false)}
        />
      ) : null}

      {/* Mobile: title bar. The menu button opens the rail; a back arrow still
          appears alongside it once drilled in. Held still across navigations
          for the same reason as the rail above. */}
      <header className="m-top">
        <div className="m-top-left">
          <button
            type="button"
            className="m-menu"
            aria-label="Open menu"
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            <span aria-hidden="true">☰</span>
          </button>
          {drill ? (
            <Link href={drill.back.href} className="m-back" aria-label={drill.back.label}>
              ←
            </Link>
          ) : null}
          <span className="wordmark">{mobileTitle}</span>
        </div>
        <div className="avatar">Z</div>
      </header>
    </>
  );
}
