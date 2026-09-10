// Shared formatting helpers used across the repositories/issues pages.
// Previously copy-pasted verbatim into three separate page files; kept in
// sync only by convention until now, which is exactly the kind of thing
// that drifts unnoticed -- see project memory / commit history for why
// this was pulled out.

export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

// Small relative-time formatter built on the native `Intl` API — no new
// date library needed for a "3 hours ago"-style label. Locale is pinned
// (not the runtime default) so a page rendering this — SSR'd on first load
// like any client component — renders identically on the server and the
// browser; the two `Date.now()` calls can still differ by the
// render/hydration gap, which callers render with `suppressHydrationWarning`.
export function formatRelativeTime(iso: string): string {
  const diffSeconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const divisions: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, secondsInUnit] of divisions) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return rtf.format(Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return rtf.format(diffSeconds, "second");
}

// Absolute-time label for a `title` tooltip. Locale and time zone are both
// pinned (rather than left to the runtime default) for the same reason as
// `formatRelativeTime` — `toLocaleString()` with no arguments resolves its
// default locale differently on the Node server than in the browser, which
// produces a real hydration mismatch (confirmed while eyeballing the
// repositories page: the server rendered "09/09/2026, 12:24:20" and the
// client rendered "9/9/2026, 12:24:20 PM" for the same instant).
export function formatAbsoluteTime(iso: string): string {
  const formatted = new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  });
  return `${formatted} UTC`;
}
