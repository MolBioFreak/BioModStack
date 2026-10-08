FROM node:20-bookworm-slim AS build

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

COPY . /app

RUN pnpm install --frozen-lockfile
RUN pnpm --dir platform/frontend build

FROM nginx:1.27-alpine

COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/platform/frontend/dist /usr/share/nginx/html/bms

EXPOSE 80
