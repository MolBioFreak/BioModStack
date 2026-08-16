FROM node:22-bookworm-slim AS build
WORKDIR /app/platform/frontend

ARG BMS_BUILD_SHA=unknown
ARG BMS_BUILD_ID=development
ARG BMS_BUILD_TIME=unknown
ENV VITE_BMS_BUILD_SHA=$BMS_BUILD_SHA \
    VITE_BMS_BUILD_ID=$BMS_BUILD_ID \
    VITE_BMS_BUILD_TIME=$BMS_BUILD_TIME
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

COPY . /app

RUN pnpm install --frozen-lockfile
RUN pnpm --dir platform/frontend build

FROM nginx:1.27-alpine

ARG BMS_BUILD_SHA=unknown
ARG BMS_BUILD_ID=development
ARG BMS_BUILD_TIME=unknown
LABEL org.opencontainers.image.revision=$BMS_BUILD_SHA \
      org.opencontainers.image.created=$BMS_BUILD_TIME \
      org.opencontainers.image.version=$BMS_BUILD_ID

COPY docker/web/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/platform/frontend/dist /usr/share/nginx/html/bms

EXPOSE 18080
