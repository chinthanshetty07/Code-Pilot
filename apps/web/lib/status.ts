import type { ConnectedRepository } from "@codepilot/shared-types";

// Pending-status checks, one per pipeline stage -- while a resource is in
// one of these, its page keeps polling for updates and disables the action
// that would re-trigger it. "fixing" (test runs) counts as pending too:
// it's the Milestone 8 fix loop actually working (the Coder agent
// generating a fix, in between two "running"s), not a resting state.
const PENDING_INDEXING_STATUSES = new Set(["queued", "indexing"]);
const PENDING_PLANNING_STATUSES = new Set(["queued", "planning"]);
const PENDING_GENERATION_STATUSES = new Set(["queued", "generating"]);
const PENDING_TEST_RUN_STATUSES = new Set(["queued", "running", "fixing"]);
const PENDING_REVIEW_STATUSES = new Set(["queued", "reviewing"]);
const PENDING_PR_STATUSES = new Set(["queued", "creating"]);

export function isPendingIndexingStatus(status: string): boolean {
  return PENDING_INDEXING_STATUSES.has(status);
}

export function isPendingPlanningStatus(status: string): boolean {
  return PENDING_PLANNING_STATUSES.has(status);
}

export function isPendingGenerationStatus(status: string): boolean {
  return PENDING_GENERATION_STATUSES.has(status);
}

export function isPendingTestRunStatus(status: string): boolean {
  return PENDING_TEST_RUN_STATUSES.has(status);
}

export function isPendingReviewStatus(status: string): boolean {
  return PENDING_REVIEW_STATUSES.has(status);
}

export function isPendingPRStatus(status: string): boolean {
  return PENDING_PR_STATUSES.has(status);
}

// A repository is usable (searchable / ready for planning) once it has
// actually finished indexing and produced chunks -- the Planner agent's
// `search_code` tool needs chunks to search, exactly like a manual search
// does. The same condition and message gate the Search page, the Issues
// page's "new issue" flow, and the repositories list's Search link, kept
// as one shared check (rather than three separately-named copies) so a
// user bouncing between them always sees identical behavior.
export function isRepositoryIndexed(repo: ConnectedRepository): boolean {
  return repo.indexing_status === "indexed" && repo.chunk_count > 0;
}

export function notIndexedMessage(repo: ConnectedRepository): string {
  switch (repo.indexing_status) {
    case "queued":
    case "indexing":
      return "This repository is still being indexed — check back in a bit.";
    case "failed":
      return "Indexing failed for this repository, so there's nothing to search yet.";
    default:
      return "This repository hasn't been indexed yet.";
  }
}
