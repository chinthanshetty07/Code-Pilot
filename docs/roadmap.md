# CodePilot Implementation Roadmap

## Phase 1 — Foundation
- create monorepo
- Next.js frontend
- FastAPI backend
- PostgreSQL
- Redis
- Docker Compose
- health checks
- environment configuration

## Phase 2 — Authentication + GitHub
- GitHub OAuth
- secure sessions
- repository listing
- repository metadata
- permission checks

## Phase 3 — Repository Intelligence
- repository ingestion
- file filtering
- language detection
- AST/symbol extraction
- intelligent chunking
- embeddings
- pgvector search

## Phase 4 — AI Planning
- task creation
- Planner Agent
- repository-aware RAG
- structured plan output
- task state persistence

## Phase 5 — Code Agent
- read/search tools
- patch-based file editing
- diff generation
- tool validation

## Phase 6 — Testing
- Docker sandbox
- test framework detection
- test execution
- output capture
- failure analysis
- bounded automatic fix loop

## Phase 7 — AI Review
- independent reviewer
- security review
- quality scoring
- review storage

## Phase 8 — Human Approval + GitHub PR
- approval screen
- branch creation
- commit
- PR creation
- PR status

## Phase 9 — Production Engineering
- structured logging
- request IDs
- token/cost tracking
- rate limiting
- background workers
- observability

## Phase 10 — Quality + Deployment
- unit tests
- integration tests
- Playwright E2E tests
- GitHub Actions
- Docker deployment
- documentation
- demo repository
- portfolio polish
