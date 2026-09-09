# CodePilot Workflow — Brief Explanation

## 1. GitHub Login
The developer signs in with GitHub OAuth. CodePilot stores GitHub credentials securely on the backend and never exposes them to the frontend.

## 2. Select Repository
The user selects a GitHub repository. CodePilot retrieves repository metadata and prepares it for indexing.

## 3. Repository Indexing
The Python backend:
- filters unnecessary/binary/generated files
- parses supported source files
- identifies functions/classes/symbols
- splits code into meaningful chunks
- generates embeddings
- stores chunks and metadata in PostgreSQL + pgvector

This creates a searchable representation of the codebase.

## 4. Developer Creates an Issue
Example:

> Fix the login API returning HTTP 500 when email is empty.

The user provides the problem. They do not need to know which files must change.

## 5. Planner Agent
The Planner Agent searches the repository and identifies:
- relevant files
- relevant functions/classes
- dependencies
- implementation steps
- tests that should be added or changed
- potential risks

It returns a structured implementation plan.

## 6. Coder Agent
The Coder Agent reads the relevant context and uses controlled tools such as:
- search_code
- read_file
- create_file
- edit_file
- get_git_diff

It produces tracked code changes instead of blindly rewriting the project.

## 7. Show Diff
CodePilot displays the exact proposed changes in a Git-style diff.

The user can inspect what will be modified before anything reaches GitHub.

## 8. Run Tests
The generated changes are tested in a sandbox/container.

The runner detects the repository's test commands where possible, for example:
- pytest
- npm test
- project-specific CI/test commands

The host machine must not run arbitrary repository commands directly.

## 9. Failure-Fix Loop
If tests fail:

Code → Test → Failure → Analyze → Fix → Test Again

Limit the number of automatic iterations, for example 3–5, to prevent infinite loops.

All attempts are stored for observability and debugging.

## 10. Independent AI Review
A separate reviewer checks:
- correctness
- security
- performance
- maintainability
- error handling
- test quality
- architectural concerns

The review is advisory, not a guarantee of correctness.

## 11. Human Approval
The developer sees:
- implementation summary
- diff
- test results
- AI review
- risk indicators

Only the developer can approve creation of the Pull Request.

## 12. GitHub Pull Request
After approval:

1. create a task branch
2. commit the changes
3. generate a PR title/body
4. create the PR on GitHub

Example branch:

`codepilot/task-184`

Example commit:

`feat: fix login input validation`

## 13. Final Result

The full workflow is:

```text
Developer
   ↓
GitHub Login
   ↓
Repository Selection
   ↓
Repository Indexing
   ↓
Semantic Code Search / RAG
   ↓
Issue
   ↓
Planner Agent
   ↓
Coder Agent
   ↓
Code Diff
   ↓
Sandbox Test Runner
   ↓
 ┌───────────────┐
 │ Tests Passed? │
 └───────┬───────┘
     No  │  Yes
         │
         ↓
 Failure Analysis
         ↓
      Fix Code
         ↓
    Test Again
         │
         └──────────→
                    ↓
              AI Code Review
                    ↓
              Human Approval
                    ↓
              Branch + Commit
                    ↓
              GitHub Pull Request
```
