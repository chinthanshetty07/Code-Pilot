import { Suspense } from "react";
import { HealthBadge } from "@/components/health-badge";
import { AuthSection } from "@/components/auth-section";
import { OAuthErrorBanner } from "@/components/oauth-error-banner";

// Placeholder landing screen for Milestone 1 — proves the frontend can
// build/deploy and reach the API. The real marketing landing page is a
// later milestone; deliberately not investing in visual design here.
export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 bg-zinc-50 px-6 text-center dark:bg-black">
      <div className="flex flex-col items-center gap-3">
        <h1 className="text-4xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
          CodePilot
        </h1>
        <p className="max-w-md text-lg text-zinc-600 dark:text-zinc-400">
          Your AI pair programmer for real-world repositories.
        </p>
      </div>
      {/* useSearchParams() needs a Suspense boundary or the page can't be
          statically rendered. */}
      <Suspense fallback={null}>
        <OAuthErrorBanner />
      </Suspense>
      <AuthSection />
      <HealthBadge />
    </div>
  );
}
