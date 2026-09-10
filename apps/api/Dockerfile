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

# Migrations run as part of the image's own default startup (not just a
# separate manual step) so a production deploy always ends up schema-current
# without depending on a platform-specific "pre-deploy command" feature --
# checked for Render specifically and its docs don't clearly confirm one
# exists for Docker-runtime services, so this works everywhere instead of
# assuming it. Local dev is unaffected: docker-compose.yml's `command:`
# override for both api and worker replaces this CMD entirely, same as
# before -- migrations there stay a deliberate, separate `alembic upgrade
# head` step (every migration in this project gets a manual look before
# being applied; see any file under alembic/versions for why). `alembic
# upgrade head` is idempotent (a no-op once already at head), so running it
# on every container start is equally safe, not just the first.
#
# --forwarded-allow-ips='*': on Render (or any PaaS behind an edge proxy),
# uvicorn's own connecting peer is the proxy, not the real client -- without
# this, --proxy-headers (on by default) still ignores X-Forwarded-For
# because --forwarded-allow-ips defaults to '127.0.0.1' only (confirmed via
# `uvicorn --help`), so request.client.host silently becomes the proxy's own
# IP for every request in production. That value feeds app/core/rate_limit.py's
# per-IP key, which would otherwise collapse into one shared bucket for all
# users. Trusting '*' here is safe: the container has no inbound path except
# through Render's own edge, same reasoning platforms like Heroku/Railway
# deployments rely on.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --forwarded-allow-ips='*'"]
