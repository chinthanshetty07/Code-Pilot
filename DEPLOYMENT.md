# Deploying CodePilot

This covers deploying to [Render](https://render.com) using the
[`render.yaml`](./render.yaml) Blueprint at the repo root, which defines all
five services this app needs: the web app, the API, the background worker,
managed Postgres (with pgvector), and a Redis-compatible Key Value store.

Render was chosen over Railway specifically because its managed Postgres
supports the `pgvector` extension natively (`CREATE EXTENSION vector;`, no
custom image needed) -- Railway's default Postgres template does not, and
would need a community-maintained Docker image instead.

**Read "Known limitation" below before you start** -- one real feature of
this app will not work on Render (or on almost any similar platform), and
it's better to know that going in than to discover it after setup.

## Known limitation: the sandboxed test runner needs a Docker socket

Milestone 7's "Run tests" feature works by having the worker talk to a
*host* Docker socket to spin up ephemeral sibling containers that actually
execute a generated diff's test suite (see `apps/api/app/services/
sandbox.py`). That requires the worker process to have access to a real
Docker daemon on the machine it's running on.

Render's Background Worker service -- like virtually every PaaS (Railway,
Heroku, Fly.io's standard offering, etc.) -- runs your code inside its own
container with no host Docker socket exposed, for the same isolation
reasons this project's own sandbox exists in the first place. There's no
Render setting that changes this; it's a structural property of the
platform, not a config gap.

**What this means in practice:** every other stage of the pipeline (repo
indexing, semantic search, the Planner, the Coder, the AI reviewer, PR
creation) needs nothing but outbound HTTPS to GitHub and the LLM providers,
and works fully on Render. Only "Run tests" will fail -- cleanly, not by
crashing: `run_in_sandbox`'s `docker create` call raises a `SandboxError`,
which `test_runner.py`'s existing error handling already catches and turns
into a normal `test_run.status = "error"` with a clear message, the same
graceful-failure path used for every other kind of test-run infrastructure
problem. Nothing else in the app breaks.

If you need "Run tests" to actually work in a deployed environment, the
realistic options are: (a) deploy the worker specifically to a host you
control with real Docker access (a VPS, running just that one service),
while keeping web/api/Postgres/Redis on Render, or (b) replace the sandbox
mechanism with a hosted code-execution API (e.g. a "sandboxes as a service"
provider) that doesn't need a local Docker daemon at all -- a real
architecture change, out of scope for this pass.

## Prerequisites

- A Render account (render.com) -- free to create, but Postgres/Key Value
  plans below aren't free tier for anything you'd want to keep running.
- This repo pushed to a GitHub repository Render can access.
- Your existing GitHub OAuth App's **Client ID and Secret** (from whatever
  Milestone 2 setup you already did for local dev) -- you'll reuse the same
  OAuth App, just add a second callback URL to it (see step 4).
- Your Groq and Gemini API keys (same ones used locally).

## 1. Push `render.yaml` to your repo

Already done if you're reading this from the repo -- `render.yaml` and both
production Dockerfiles (`apps/api/Dockerfile`, `apps/web/Dockerfile.prod`)
are already committed.

## 2. Create the Blueprint

In the Render dashboard: **New > Blueprint**, connect the GitHub repo, and
point it at `render.yaml`. Render parses it and shows you all five services
it's about to create.

## 3. Fill in the secrets it prompts for

Render prompts for every env var marked `sync: false` in `render.yaml`
during this *initial* creation flow only (not on later blueprint updates --
see the comment in `render.yaml` itself). You'll be asked for the same six
values **twice** (once for `codepilot-api`, once for `codepilot-worker`) --
both services need them independently:

- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` -- your existing OAuth App's
  values.
- `GROQ_API_KEY`, `GEMINI_API_KEY`, `EMBEDDING_API_KEY` -- your existing
  keys (EMBEDDING_API_KEY is the same value as GEMINI_API_KEY unless you've
  switched `EMBEDDING_PROVIDER` to `openai`).

`SECRET_KEY` is generated automatically (`generateValue: true`) -- you
don't need to supply it.

Click **Apply** once everything's filled in. Render builds and deploys all
five services.

## 4. Update your GitHub OAuth App's callback URL

Your GitHub OAuth App (github.com/settings/developers) needs to know about
the new deployed callback in addition to (or instead of) your local one.
Add:

```
https://codepilot-api.onrender.com/api/auth/github/callback
```

as an **Authorization callback URL**. (GitHub OAuth Apps support only one
callback URL at a time in the classic flow -- if you still want local dev
login to keep working too, you'll need to switch between the two URLs as
needed, or register a second OAuth App specifically for production.)

## 5. That's it -- no manual migration step

`apps/api/Dockerfile`'s own startup command runs `alembic upgrade head`
before starting the API or the worker (see the Dockerfile's comment) --
every deploy, not just the first, and it's a no-op if already current. You
don't need to shell into anything or run a command by hand.

## Redeploying

Render auto-deploys on every push to the branch you connected the
Blueprint to (default: your repo's default branch). No separate CI/CD step
is required for the deploy itself -- see `.github/workflows/ci.yml` for the
test/lint gate that runs on every push and PR, independent of Render's own
deploy trigger.

## If you rename a service

`render.yaml` hardcodes `https://codepilot-api.onrender.com` and
`https://codepilot-web.onrender.com` in a few places (env vars, and the
`apps/web/Dockerfile.prod` build arg default) rather than using Render's
dynamic `fromService` cross-references -- deliberate, see the comment at
the top of `render.yaml` for why. If you rename `codepilot-api` or
`codepilot-web` during Blueprint setup, update every occurrence of the old
URL to match, including the `ARG NEXT_PUBLIC_API_URL` default in
`apps/web/Dockerfile.prod` (that one matters most: it's baked into the
frontend's JS bundle at build time, not read at runtime -- see that
file's own comment).

## Cost

Nothing here is free-tier-forever. Rough starting point (check Render's
current pricing before committing):
- `codepilot-postgres`: smallest paid Postgres plan (pgvector needs a real
  plan, not the free tier's 30-day-expiring instance, to be usable long-term)
- `codepilot-redis`: free Key Value tier works, but has no data
  persistence -- an instance restart loses sessions, rate-limit counters,
  and any queued-but-not-yet-processed background job. Acceptable for a
  demo; upgrade if that matters for your use.
- `codepilot-api` / `codepilot-worker` / `codepilot-web`: each needs at
  least the smallest paid web/worker plan to stay running continuously
  (free web services on Render spin down when idle).
