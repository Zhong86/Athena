"use client";

import { usePathname } from "next/navigation";
import { ViewTransition } from "react";

/**
 * Keyed by pathname so every route change is a fresh mount as far as
 * `ViewTransition` is concerned, even though the layout itself never
 * unmounts. Query-only updates (e.g. `?prompt=`, `?thread=`) don't touch the
 * pathname, so they don't replay this — only a real navigation does.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <ViewTransition key={pathname} enter="page-enter" exit="page-exit" default="none">
      {children}
    </ViewTransition>
  );
}
