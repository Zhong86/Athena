"use client";

/**
 * Timestamps are stored UTC; only the browser knows the reader's timezone.
 * Server output is corrected on hydration, hence suppressHydrationWarning.
 */
export function When({ iso, suffix }: { iso: string; suffix?: string }) {
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);

  let label: string;
  if (Number.isNaN(date.getTime())) {
    label = iso;
  } else {
    const time = date.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
    const today = new Date();
    const isToday = date.toDateString() === today.toDateString();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const isYesterday = date.toDateString() === yesterday.toDateString();

    if (isToday) label = `Today, ${time}`;
    else if (isYesterday) label = `Yesterday, ${time}`;
    else
      label = `${date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })}, ${time}`;
  }

  return (
    <time dateTime={iso} suppressHydrationWarning>
      {label}
      {suffix ? ` · ${suffix}` : ""}
    </time>
  );
}
