import type { ConnectedRepository } from "@codepilot/shared-types";
import { describe, expect, it } from "vitest";
import {
  isPendingGenerationStatus,
  isPendingIndexingStatus,
  isPendingPlanningStatus,
  isPendingPRStatus,
  isPendingReviewStatus,
  isPendingTestRunStatus,
  isRepositoryIndexed,
  notIndexedMessage,
} from "./status";

function makeRepo(overrides: Partial<ConnectedRepository> = {}): ConnectedRepository {
  return {
    id: "repo-1",
    github_id: 1,
    full_name: "owner/repo",
    description: null,
    language: null,
    default_branch: "main",
    private: false,
    indexing_status: "not_indexed",
    indexing_error: null,
    file_count: 0,
    chunk_count: 0,
    indexed_at: null,
    connected_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

describe.each([
  ["isPendingIndexingStatus", isPendingIndexingStatus, ["queued", "indexing"], ["indexed", "failed", "not_indexed"]],
  ["isPendingPlanningStatus", isPendingPlanningStatus, ["queued", "planning"], ["planned", "failed"]],
  ["isPendingGenerationStatus", isPendingGenerationStatus, ["queued", "generating"], ["generated", "failed"]],
  ["isPendingTestRunStatus", isPendingTestRunStatus, ["queued", "running", "fixing"], ["passed", "failed", "error"]],
  ["isPendingReviewStatus", isPendingReviewStatus, ["queued", "reviewing"], ["approved", "changes_requested", "failed"]],
  ["isPendingPRStatus", isPendingPRStatus, ["queued", "creating"], ["created", "failed"]],
] as const)("%s", (_name, fn, pending, terminal) => {
  it.each(pending)("treats %s as pending", (status) => {
    expect(fn(status)).toBe(true);
  });

  it.each(terminal)("treats %s as not pending", (status) => {
    expect(fn(status)).toBe(false);
  });

  it("treats an unrecognized status as not pending", () => {
    expect(fn("some-future-status")).toBe(false);
  });
});

describe("isRepositoryIndexed", () => {
  it("is true once indexed with at least one chunk", () => {
    expect(isRepositoryIndexed(makeRepo({ indexing_status: "indexed", chunk_count: 5 }))).toBe(
      true,
    );
  });

  it("is false when indexed but with zero chunks (e.g. an empty repo)", () => {
    expect(isRepositoryIndexed(makeRepo({ indexing_status: "indexed", chunk_count: 0 }))).toBe(
      false,
    );
  });

  it("is false while still queued/indexing even if chunk_count is stale-nonzero", () => {
    expect(isRepositoryIndexed(makeRepo({ indexing_status: "indexing", chunk_count: 5 }))).toBe(
      false,
    );
  });

  it("is false when indexing failed", () => {
    expect(isRepositoryIndexed(makeRepo({ indexing_status: "failed", chunk_count: 5 }))).toBe(
      false,
    );
  });
});

describe("notIndexedMessage", () => {
  it.each(["queued", "indexing"])("explains that indexing is still in progress for %s", (status) => {
    expect(notIndexedMessage(makeRepo({ indexing_status: status }))).toMatch(/still being indexed/);
  });

  it("explains that indexing failed", () => {
    expect(notIndexedMessage(makeRepo({ indexing_status: "failed" }))).toMatch(/failed/);
  });

  it("explains the repo hasn't been indexed yet, for any other status", () => {
    expect(notIndexedMessage(makeRepo({ indexing_status: "not_indexed" }))).toMatch(
      /hasn't been indexed yet/,
    );
  });
});
