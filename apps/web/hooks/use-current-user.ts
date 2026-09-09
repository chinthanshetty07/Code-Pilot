"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AuthUser } from "@codepilot/shared-types";
import { apiFetch } from "@/lib/api";

interface UseCurrentUserResult {
  user: AuthUser | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * Wraps `GET /api/auth/me`.
 *
 * A 401 response means "not logged in" — that's an expected, valid state
 * here, not an error to log or throw. Any other failure (network error,
 * unreachable API, 5xx) is treated the same way from the caller's
 * perspective: once `loading` settles, `user` is `null`, so pages can
 * render a logged-out state instead of crashing.
 */
export function useCurrentUser(): UseCurrentUserResult {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Guards against out-of-order responses when `refetch` is called again
  // before a previous request has resolved.
  const requestId = useRef(0);

  // Fires the request and settles state via `.then/.catch/.finally` only —
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from the effect below (calling something
  // that sets state synchronously inside an effect causes an extra,
  // avoidable render).
  const load = useCallback((id: number) => {
    apiFetch<AuthUser>("/api/auth/me")
      .then((data) => {
        if (id === requestId.current) {
          setUser(data);
        }
      })
      .catch(() => {
        // A 401 ("not logged in") is expected and valid here. Any other
        // failure (network error, unreachable API, 5xx) is treated the
        // same way from the UI's perspective — no session to show — and
        // isn't logged: an unreachable backend is a normal, expected
        // condition in this milestone (see `HealthBadge`, which follows
        // the same silent-catch convention).
        if (id === requestId.current) {
          setUser(null);
        }
      })
      .finally(() => {
        if (id === requestId.current) {
          setLoading(false);
        }
      });
  }, []);

  // Public refetch: resets `loading` back to `true` before firing a new
  // request. Only ever called from event handlers, never from an effect.
  const refetch = useCallback(() => {
    setLoading(true);
    load(++requestId.current);
  }, [load]);

  useEffect(() => {
    load(++requestId.current);
    // `loading` already starts `true`, so the initial fetch doesn't need
    // to set it again — this keeps the effect's own body free of any
    // synchronous setState call.
  }, [load]);

  return { user, loading, refetch };
}
