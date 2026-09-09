# AI Coding Agent Build Prompt

Build **CodePilot**, a production-quality AI software engineering platform.

Use:
- Next.js + React + TypeScript for frontend
- Python + FastAPI for backend
- PostgreSQL + pgvector for application data and code embeddings
- Redis for background jobs/cache
- Docker for isolated code execution
- GitHub OAuth + GitHub API for repository/branch/commit/PR workflows

The AI system must follow:

Issue
→ Repository Retrieval
→ Planner Agent
→ Coder Agent
→ Diff
→ Sandbox Tests
→ Failure Analysis
→ Fix Loop
→ Independent Review
→ Human Approval
→ Branch
→ Commit
→ Pull Request

Requirements:

1. Do not build a simple chatbot.
2. Python must be a primary part of the backend and AI system.
3. Use modular services and typed interfaces.
4. Store agent/task state in PostgreSQL.
5. Use structured agent outputs.
6. Use explicit tools such as search_code, read_file, edit_file, run_tests, git_diff.
7. Never allow unrestricted shell or filesystem access.
8. Run repository/generated code inside a controlled Docker sandbox.
9. Limit automatic test-fix iterations.
10. Never automatically ship code without human approval.
11. Never expose GitHub or LLM secrets to the frontend.
12. Add unit, integration, and E2E tests.
13. Add logging, request IDs, and AI usage tracking.
14. Add Docker Compose for local development.
15. Add GitHub Actions CI.
16. Write clear architecture/security/AI documentation.

Start by creating the repository structure and basic services. Do not generate the entire application in one step.

After each milestone:
- run tests
- run lint/type checks
- fix errors
- verify integration

The first working milestone must prove:
frontend starts
backend starts
database starts
redis starts
frontend can call backend
backend can connect to PostgreSQL
backend can connect to Redis

Then continue milestone-by-milestone according to `docs/roadmap.md`.
