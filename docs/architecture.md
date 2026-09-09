# CodePilot Architecture

## High-level architecture

```text
                    ┌──────────────────┐
                    │ Developer / User │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Next.js / React  │
                    │ TypeScript UI    │
                    └────────┬─────────┘
                             │ HTTPS
                             ▼
                    ┌──────────────────┐
                    │ Python FastAPI   │
                    │ API Layer        │
                    └───────┬──────────┘
                            │
          ┌─────────────────┼───────────────────┐
          ▼                 ▼                   ▼
   ┌────────────┐   ┌──────────────┐   ┌───────────────┐
   │ PostgreSQL │   │    Redis     │   │ GitHub API    │
   │ + pgvector │   │ jobs/cache   │   │ OAuth/Webhook │
   └─────┬──────┘   └──────┬───────┘   └───────────────┘
         │                  │
         ▼                  ▼
   Code / task data      Workers
                            │
                            ▼
                    ┌──────────────────┐
                    │ Agent Orchestrator│
                    └────────┬─────────┘
                             │
              ┌──────────────┼─────────────┐
              ▼              ▼             ▼
         Planner Agent   Coder Agent   Reviewer Agent
              │              │             │
              └──────────────┼─────────────┘
                             ▼
                    Controlled Tools
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
           Code Search    File Diff    Test Runner
                                           │
                                           ▼
                                    Docker Sandbox
```

## Service responsibilities

### Web
Responsible for:
- authentication UI
- repository dashboard
- task dashboard
- code search
- plan display
- diff display
- test results
- reviewer output
- human approval

### API
Responsible for:
- authentication/session
- authorization
- REST APIs
- database access
- GitHub integration
- task orchestration
- AI service coordination

### Worker
Responsible for long-running processes:
- repository indexing
- embeddings
- agent runs
- test execution
- AI review
- GitHub synchronization

### PostgreSQL + pgvector
Stores:
- users
- repositories
- files/chunks
- tasks
- agent runs
- diffs
- tests
- reviews
- pull requests
- audit logs
- embeddings

### Redis
Used for:
- background job queue
- caching
- task progress/state where appropriate
- rate limiting

## Security model

The system must:
- keep GitHub tokens server-side
- validate every tool call
- restrict file operations
- run code inside a controlled container
- limit CPU/memory/time
- restrict network access
- avoid logging secrets
- enforce repository ownership/authorization
- require human approval before PR creation
