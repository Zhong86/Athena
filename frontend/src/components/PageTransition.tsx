"use client";

import { usePathname } from "next/navigation";

/**
 * Replays the page-in animation on every route change.
 *
 * The animation itself is plain CSS on `.shell` (globals.css) rather than a
 * `<ViewTransition>`: the browser skips view transitions in a lot of ordinary
 * situations -- a backgrounded tab, an interrupted navigation, anything short
 * of full API support -- and when it skips, nothing animates at all.
 *
 * A CSS animation only replays if the element is genuinely new, and React
 * would otherwise reuse the `.shell` node across routes since every page
 * renders the same `<Nav>` + `<div className="shell">` shape. The key forces
 * the whole page subtree to remount so the animation starts over. The wrapper
 * is `display: contents` so it adds a remount boundary without adding a box --
 * the rail and the mobile top bar are `position: fixed` and would shift if an
 * ancestor box came between them and the viewport.
 *
 * Query-only updates (e.g. `?prompt=`, `?thread=`) don't touch the pathname,
 * so they don't remount -- only a real navigation does.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} style={{ display: "contents" }}>
      {children}
    </div>
  );
}
