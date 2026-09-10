FROM python:3.12-slim

# git: the Coder agent's workspace (app/services/workspace.py) shells out to
# it for get_git_diff. docker-cli: the client only, not docker.io (which on
# this Debian base turned out to ship dockerd/docker-proxy/docker-init but
# no `docker` binary at all -- confirmed via `dpkg -L docker.io` empirically
# rather than assumed) -- this container never runs its own dockerd, the
# Milestone 7 sandbox runner (app/services/sandbox.py) uses the CLI to spin
# up sibling containers against the host's Docker socket, mounted in for the
# worker service specifically (see docker-compose.yml). Neither package is
# present in the slim base image by default.
RUN apt-get update && apt-get install -y --no-install-recommends git docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
