"use client";

import { useSearchParams } from "next/navigation";

// Reads `?error=<code>` set by the backend after a failed GitHub OAuth
// round trip (e.g. `oauth_state_mismatch`, `github_oauth_failed`). We don't
// branch on the specific code — just show one generic message.
export function OAuthErrorBanner() {
  const searchParams = useSearchParams();

  if (!searchParams.has("error")) {
    return null;
  }

  return (
    <p
      role="alert"
      className="max-w-md rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-600 dark:text-red-400"
    >
      GitHub connection failed, please try again.
    </p>
  );
}
