"use client";

import { useEffect, useState } from "react";
import type { HealthResponse } from "@codepilot/shared-types";
import { apiFetch } from "@/lib/api";

type ConnectionState = "loading" | "connected" | "unreachable";

const STATE_LABEL: Record<ConnectionState, string> = {
  loading: "Checking API…",
  connected: "API connected",
  unreachable: "API unreachable",
};

const STATE_DOT_CLASS: Record<ConnectionState, string> = {
  loading: "bg-amber-400 animate-pulse",
  connected: "bg-emerald-500",
  unreachable: "bg-red-500",
};

export function HealthBadge() {
  const [state, setState] = useState<ConnectionState>("loading");

  useEffect(() => {
    let cancelled = false;

    apiFetch<HealthResponse>("/health")
      .then((data) => {
        if (!cancelled) {
          setState(data.status === "ok" ? "connected" : "unreachable");
        }
      })
      .catch(() => {
        // No backend reachable (or a network/CORS error) — this is an
        // expected, valid state whenever the API isn't running.
        if (!cancelled) {
          setState("unreachable");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div
      role="status"
      className="inline-flex items-center gap-2 rounded-full border border-black/[.08] bg-white/60 px-3 py-1.5 text-sm text-zinc-700 dark:border-white/[.145] dark:bg-white/[.04] dark:text-zinc-300"
    >
      <span
        aria-hidden
        className={`h-2 w-2 rounded-full ${STATE_DOT_CLASS[state]}`}
      />
      {STATE_LABEL[state]}
    </div>
  );
}
