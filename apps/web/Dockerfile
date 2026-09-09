# Build context is the monorepo root (not apps/web) so pnpm can resolve the
# @codepilot/shared-types workspace dependency. See docker-compose.yml.
FROM node:24-slim

WORKDIR /repo

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json

RUN corepack enable && corepack prepare pnpm@latest --activate
RUN pnpm install --frozen-lockfile

COPY apps/web apps/web
COPY packages/shared-types packages/shared-types

WORKDIR /repo/apps/web

EXPOSE 3000

CMD ["pnpm", "dev"]
