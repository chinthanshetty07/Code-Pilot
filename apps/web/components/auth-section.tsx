"use client";

import Link from "next/link";
import { API_BASE_URL } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

// Auth-aware call to action for the landing page: "Connect GitHub" when
// signed out, or a link into the app when signed in.
export function AuthSection() {
  const { user, loading } = useCurrentUser();

  if (loading) {
    return null;
  }

  if (user) {
    return (
      <div className="flex flex-col items-center gap-3">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Connected as{" "}
          <span className="font-medium text-zinc-950 dark:text-zinc-50">
            {user.username}
          </span>
        </p>
        <Link
          href="/repositories"
          className="rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
        >
          View repositories
        </Link>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => {
        // Full page navigation, not a fetch — the backend redirects the
        // browser through GitHub's OAuth consent screen and back. This is
        // an absolute URL to a different origin (the API server), not an
        // internal Next.js route.
        window.location.href = API_BASE_URL + "/api/auth/github/login";
      }}
      className="rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
    >
      Connect GitHub
    </button>
  );
}
