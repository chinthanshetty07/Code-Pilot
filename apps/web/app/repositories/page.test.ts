import { describe, expect, it } from "vitest";
import { indexButtonLabel } from "./page";

describe("indexButtonLabel", () => {
  it("shows 'Starting…' while the POST is in flight, regardless of status", () => {
    expect(indexButtonLabel("not_indexed", true)).toBe("Starting…");
    expect(indexButtonLabel("failed", true)).toBe("Starting…");
  });

  it("shows 'Queued…' for a queued repo", () => {
    expect(indexButtonLabel("queued", false)).toBe("Queued…");
  });

  it("shows 'Indexing…' for a repo actively being indexed", () => {
    expect(indexButtonLabel("indexing", false)).toBe("Indexing…");
  });

  it("offers 'Re-index' once already indexed", () => {
    expect(indexButtonLabel("indexed", false)).toBe("Re-index");
  });

  it("offers 'Re-index' after a failed attempt, not a generic retry label", () => {
    expect(indexButtonLabel("failed", false)).toBe("Re-index");
  });

  it("defaults to 'Index' for a never-indexed repo", () => {
    expect(indexButtonLabel("not_indexed", false)).toBe("Index");
  });
});
