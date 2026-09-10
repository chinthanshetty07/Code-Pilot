import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatAbsoluteTime, formatRelativeTime, truncate } from "./format";

describe("truncate", () => {
  it("returns the text unchanged when it fits within maxLength", () => {
    expect(truncate("hello", 10)).toBe("hello");
  });

  it("returns the text unchanged when it exactly equals maxLength", () => {
    expect(truncate("hello", 5)).toBe("hello");
  });

  it("truncates and appends an ellipsis when text exceeds maxLength", () => {
    expect(truncate("hello world", 8)).toBe("hello w…");
  });

  it("truncated output (including the ellipsis) is exactly maxLength long", () => {
    const result = truncate("a very long string indeed", 10);
    expect(result).toHaveLength(10);
    expect(result.endsWith("…")).toBe(true);
  });
});

describe("formatRelativeTime", () => {
  const NOW = new Date("2026-06-15T12:00:00.000Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("formats a moment seconds ago", () => {
    const iso = new Date(NOW.getTime() - 30 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("30 seconds ago");
  });

  it("formats minutes ago", () => {
    const iso = new Date(NOW.getTime() - 5 * 60 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("5 minutes ago");
  });

  it("formats hours ago", () => {
    const iso = new Date(NOW.getTime() - 3 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("3 hours ago");
  });

  it("formats days ago", () => {
    const iso = new Date(NOW.getTime() - 2 * 24 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("2 days ago");
  });

  it("formats a future time (e.g. clock skew) as 'in N minutes', not negative", () => {
    const iso = new Date(NOW.getTime() + 10 * 60 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("in 10 minutes");
  });

  it("picks the largest applicable unit rather than the smallest", () => {
    // 25 hours ago should read in days, not hours.
    const iso = new Date(NOW.getTime() - 25 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(iso)).toBe("yesterday");
  });
});

describe("formatAbsoluteTime", () => {
  it("formats an ISO timestamp as a fixed-locale, UTC-pinned string", () => {
    // Deliberately not locale/timezone-sensitive to the host running the
    // test -- see format.ts's own comment on why both are pinned.
    expect(formatAbsoluteTime("2026-06-15T12:34:56.000Z")).toBe(
      "Jun 15, 2026, 12:34 PM UTC",
    );
  });

  it("is stable regardless of the host's own timezone", () => {
    const original = process.env.TZ;
    process.env.TZ = "America/Los_Angeles";
    try {
      expect(formatAbsoluteTime("2026-06-15T12:34:56.000Z")).toBe(
        "Jun 15, 2026, 12:34 PM UTC",
      );
    } finally {
      process.env.TZ = original;
    }
  });
});
