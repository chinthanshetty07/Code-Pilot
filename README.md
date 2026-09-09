# CodePilot

**Your AI pair programmer for real-world repositories.**

CodePilot is an AI-powered software engineering platform: connect a GitHub repository and it indexes the codebase, turns issues into implementation plans, generates code changes, runs tests in a sandbox, performs an AI code review, and opens a pull request — with a human approving every step before anything touches the real repository.

This repo is being built incrementally, milestone by milestone (see [Roadmap](#roadmap)). **Current status: Milestone 1 — monorepo scaffold.** Next.js, FastAPI, Postgres/pgvector, and Redis are wired together end to end; there are no product features yet.

## Stack

- **Frontend:** Next.js, React, TypeScript, Tailwind CSS
- **Backend:** Python, FastAPI, SQLAlchemy, Alembic
- **AI / agents:** Python — agent orchestration, RAG, LLM tool calling (Anthropic Claude by default, pluggable)
- **Database:** PostgreSQL + pgvector
- **Queue / cache:** Redis + arq
- **Infra:** Docker Compose (local), GitHub Actions (CI, added in a later milestone)

## Local development

Prerequisites: Docker (or [Colima](https://github.com/abiosoft/colima)), Node.js 24+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up
```

- Frontend: http://localhost:3000
- API: http://localhost:8010 — interactive docs at `/docs`, health at `/health` and `/health/ready` (mapped off the default 8000 because another local project already uses that port on this machine; change it back in `docker-compose.yml` / `.env` if that's no longer true for you)

### Running the pieces individually (no Docker)

```bash
# API
cd apps/api
uv run uvicorn app.main:app --reload

# Web
pnpm install
pnpm --filter web dev
```

## Project layout

```
apps/web          Next.js frontend
apps/api          FastAPI backend
  app/core/          settings, database, redis
  app/api/           HTTP routes
  app/models/        SQLAlchemy models
  app/schemas/       Pydantic request/response schemas
  app/services/      business logic
  app/repositories/  data access
  app/agents/        planner / coder / reviewer agents
  app/tools/         agent tool implementations
  app/rag/           retrieval-augmented generation pipeline
  app/github/        GitHub API integration
  app/workers/       arq background jobs
  app/tests/         pytest suite
packages/          Shared TypeScript types (@codepilot/shared-types)
infrastructure/    Docker and deployment config
docs/              Architecture and design docs (added as milestones land)
```

## Roadmap

1. Monorepo scaffold *(current)*
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
