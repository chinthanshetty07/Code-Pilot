# CodePilot

**Your AI pair programmer for real-world repositories.**

CodePilot is an AI-powered software engineering platform: connect a GitHub repository and it indexes the codebase, turns issues into implementation plans, generates code changes, runs tests in a sandbox (with an automatic fix loop on failure), performs an AI code review, and opens a pull request — with a human approving the diff before anything is pushed to the real repository.

**Status: all 12 planned milestones are built.** Every stage of the pipeline below is implemented and covered by the automated test suite; see [Known limitation](#known-limitation) for the one deployment-environment caveat, and [Deployment](#deployment) for going live.

## Pipeline

```
GitHub repo → indexing → semantic search (RAG) → issue → Planner agent
  → Coder agent → diff → sandboxed tests → fix loop (auto-retry on failure)
  → AI code review → human approval → git branch + commit → GitHub pull request
```

## Stack

- **Frontend:** Next.js 15 (App Router), React, TypeScript, Tailwind CSS
- **Backend:** Python, FastAPI, SQLAlchemy (async), Alembic
- **AI / agents:** Groq (`openai/gpt-oss-120b`) as the default LLM for the Planner, Coder, and Reviewer agents; Gemini (`gemini-3.6-flash`) as a pluggable alternate provider — swap via `PLANNER_LLM_PROVIDER` in `.env`. Gemini also powers embeddings by default (OpenAI is a pluggable alternative for that).
- **Database:** PostgreSQL + pgvector (semantic code search)
- **Queue / cache:** Redis + arq (background jobs: indexing, planning, coding, testing, review, PR creation)
- **Sandboxing:** ephemeral Docker containers for running a generated diff's test suite in isolation
- **Observability:** structured JSON logging with request/job correlation IDs, per-call LLM token usage and cost tracking
- **Infra:** Docker Compose (local dev), GitHub Actions (CI), Render (deployment — see [Deployment](#deployment))

## Local development

Prerequisites: Docker (or [Colima](https://github.com/abiosoft/colima)), Node.js 24+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up
```

- Frontend: http://localhost:3000
- API: http://localhost:8010 — interactive docs at `/docs`, health at `/health` and `/health/ready` (mapped off the default 8000 because another local project already uses that port on this machine; change it back in `docker-compose.yml` / `.env` if that's no longer true for you)

Fill in `.env` with a GitHub OAuth App's client ID/secret and a Groq + Gemini API key (both have free tiers with no card required) — see the comments in `.env.example` for exactly where to get each one.

### Running the pieces individually (no Docker)

```bash
# API
cd apps/api
uv run uvicorn app.main:app --reload

# Worker (background jobs — indexing, agents, tests, PR creation)
cd apps/api
uv run arq app.workers.worker.WorkerSettings

# Web
pnpm install
pnpm --filter web dev
```

### Tests, types, lint

```bash
# Backend — via the worker container specifically: it's the one with the
# host's Docker socket mounted in, which the sandboxed-test-runner tests need
docker compose exec worker uv run pytest -q
docker compose exec worker uv run mypy app
docker compose exec worker uv run ruff check .

# Frontend
pnpm --filter web lint
pnpm --filter web exec tsc --noEmit
pnpm --filter web test
pnpm --filter web build
```

CI (`.github/workflows/ci.yml`) runs all of the above — plus a fresh-database migration check — on every push and pull request.

## Known limitation

The sandboxed test runner (`apps/api/app/services/sandbox.py`) talks to a *host* Docker socket to spin up ephemeral containers that execute a generated diff's test suite. That works in local dev (the `worker` service mounts `docker.sock`, see `docker-compose.yml`) and in GitHub Actions (runners have Docker natively). It does **not** work on most managed PaaS platforms, including Render's worker service — there's no host Docker socket to mount. There, "Run tests" fails cleanly (`test_run.status = "error"`, not a crash) rather than working. See [DEPLOYMENT.md](./DEPLOYMENT.md#known-limitation-the-sandboxed-test-runner-needs-a-docker-socket) for the full explanation and workaround options.

## Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the full walkthrough — deploys to [Render](https://render.com) via the [`render.yaml`](./render.yaml) Blueprint (web, API, worker, managed Postgres with pgvector, and Redis-compatible Key Value store, all in one file).

## Documentation

- [docs/architecture.md](docs/architecture.md) — system architecture and security model
- [docs/workflow.md](docs/workflow.md) — the end-to-end issue-to-PR workflow, step by step
- [docs/roadmap.md](docs/roadmap.md) — phase-by-phase build plan
- [DEPLOYMENT.md](./DEPLOYMENT.md) — deploying to Render

## Project layout

```
apps/web          Next.js frontend
  app/               routes (repositories, issues, search, PR review)
  components/        UI components
  hooks/, lib/       data fetching, API client
apps/api          FastAPI backend
  app/core/          settings, database, redis, structured logging
  app/api/           HTTP routes
  app/models/        SQLAlchemy models
  app/schemas/       Pydantic request/response schemas
  app/services/      business logic (indexing, workspace/git, test runner, PR creation, usage tracking)
  app/agents/        planner / coder / reviewer agents + shared tool implementations
  app/rag/           chunking, embeddings, semantic search
  app/github/        GitHub OAuth + REST API client
  app/workers/       arq background job definitions
  app/tests/         pytest suite
packages/          Shared TypeScript types (@codepilot/shared-types)
infrastructure/    Docker Compose Postgres init script
docs/              Architecture and design docs
```

## Milestones (all complete)

1. Monorepo scaffold
2. GitHub OAuth + repositories
3. Repository indexing (chunking + embeddings)
4. Semantic code search
5. Planner agent
6. Coder agent + diff viewer
7. Sandboxed test runner
8. Test-failure-fix loop
9. AI code reviewer
10. Human approval + GitHub PR creation
11. Observability + AI cost tracking
12. Full test suite, CI/CD, deployment, documentation
