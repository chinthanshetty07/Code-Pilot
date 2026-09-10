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
