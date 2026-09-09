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
