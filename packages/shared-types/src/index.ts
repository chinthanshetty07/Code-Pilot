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
  connected_at: string;
}
