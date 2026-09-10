// Shared contract types between the CodePilot frontend and backend.
// Types-only package: no runtime code, no build step. Consume via `import type`.

export type ServiceStatus = "ok" | "error" | "unknown";

export interface HealthResponse {
  status: ServiceStatus;
}

export interface ReadinessResponse {
  status: ServiceStatus;
  checks: {
    database: ServiceStatus;
    redis: ServiceStatus;
  };
}

export interface AuthUser {
  id: string;
  username: string;
  name: string | null;
  avatar_url: string | null;
}

export interface GithubRepoSummary {
  github_id: number;
  full_name: string;
  description: string | null;
  language: string | null;
  private: boolean;
  default_branch: string;
}

export interface ConnectedRepository {
  id: string;
  github_id: number;
  full_name: string;
  description: string | null;
  language: string | null;
  default_branch: string;
  private: boolean;
  indexing_status: string;
  indexing_error: string | null;
  file_count: number;
  chunk_count: number;
  indexed_at: string | null;
  connected_at: string;
}

// chunk_type is "function" | "class" | "method" | "interface" | "text" on
// the backend (app/rag/chunking.py) -- left as `string` here rather than a
// union so the frontend doesn't need updating every time a new chunk type
// is added there.
export interface SearchResult {
  chunk_id: string;
  file_path: string;
  language: string;
  chunk_type: string;
  symbol_name: string | null;
  start_line: number;
  end_line: number;
  content: string;
  score: number;
}

export interface PlanRelevantFile {
  file_path: string;
  reason: string;
}

export interface Plan {
  id: string;
  summary: string;
  relevant_files: PlanRelevantFile[];
  implementation_steps: string[];
  tests_to_add_or_change: string[];
  risks: string[];
  created_at: string;
}

// status is "queued" | "running" | "fixing" | "passed" | "failed" | "error"
// on the backend (app/models/test_run.py) -- left as `string` for the same
// reason as SearchResult.chunk_type above. "failed" means the tests
// actually ran and some didn't pass (exit_code is set); "error" means no
// verdict was reached at all (no test command detected, a sandbox/infra
// failure, or a timeout) -- exit_code is null in that case. "fixing" is the
// Milestone 8 fix loop actually working (the Coder agent generating a fix,
// in between two "running"s) -- pending, like queued/running, just with its
// own label. fix_attempts counts how many fix passes this test_run has gone
// through so far (capped server-side); 0 means none has run yet.
export interface TestRun {
  id: string;
  status: string;
  command: string | null;
  output: string | null;
  exit_code: number | null;
  fix_attempts: number;
  created_at: string;
}

// severity is "blocking" | "suggestion" on the backend
// (app/agents/reviewer.py) -- left as `string` for the same reason as
// SearchResult.chunk_type above.
export interface ReviewComment {
  file_path: string;
  severity: string;
  comment: string;
}

// status is "queued" | "reviewing" | "approved" | "changes_requested" |
// "failed" on the backend (app/models/review.py) -- left as `string` for
// the same reason as SearchResult.chunk_type above. approved/
// changes_requested are the two real verdicts; "failed" means the review
// *process* broke (LLM/provider error), not a judgment about the code --
// same failed/error-style distinction TestRun makes for its own status.
// comments is null until a verdict is actually reached, and can still be
// an empty array after that (a clean approval with nothing to flag).
export interface Review {
  id: string;
  status: string;
  error: string | null;
  summary: string | null;
  comments: ReviewComment[] | null;
  created_at: string;
}

// generation_status is "queued" | "generating" | "generated" | "failed" on
// the backend (app/models/code_change.py) -- left as `string` for the same
// reason as SearchResult.chunk_type above.
export interface CodeChange {
  id: string;
  generation_status: string;
  generation_error: string | null;
  summary: string | null;
  diff: string | null;
  created_at: string;
  test_run: TestRun | null;
  review: Review | null;
}

// planning_status is "queued" | "planning" | "planned" | "failed" on the
// backend (app/models/issue.py) -- left as `string` for the same reason as
// SearchResult.chunk_type above.
export interface Issue {
  id: string;
  repository_id: string;
  description: string;
  planning_status: string;
  planning_error: string | null;
  created_at: string;
  plan: Plan | null;
  code_change: CodeChange | null;
}
