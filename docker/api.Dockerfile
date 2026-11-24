ARG SOURCE_DATE_EPOCH=1
FROM python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015 AS api-base
ARG SOURCE_DATE_EPOCH
ARG BMS_BUILD_SHA=unknown
ARG BMS_BUILD_ID=development
ARG BMS_BUILD_TIME=unknown

LABEL org.opencontainers.image.revision=$BMS_BUILD_SHA \
      org.opencontainers.image.created=$BMS_BUILD_TIME \
      org.opencontainers.image.version=$BMS_BUILD_ID

ENV BMS_BUILD_SHA=$BMS_BUILD_SHA \
    BMS_BUILD_ID=$BMS_BUILD_ID \
    BMS_BUILD_TIME=$BMS_BUILD_TIME \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        docker.io \
        docker-compose \
        git \
        openssh-client \
        libcurl4-openssl-dev \
        libfontconfig1-dev \
        libfreetype6-dev \
        libfribidi-dev \
        libharfbuzz-dev \
        libgmp-dev \
        libglpk-dev \
        libgsl-dev \
        libicu-dev \
        libjpeg-dev \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libpangoft2-1.0-0 \
        libssl-dev \
        libtiff5-dev \
        libuv1-dev \
        libxml2-dev \
    && rm -rf /var/lib/apt/lists/* \
        /var/log/apt/* \
        /var/log/dpkg.log \
        /var/log/alternatives.log \
        /var/cache/ldconfig/aux-cache

RUN set -eux; \
    UV_VERSION="0.13.9"; \
    case "$(uname -m)" in \
        x86_64|amd64) UV_TARGET="x86_64-unknown-linux-gnu"; UV_SHA256="a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5" ;; \
        aarch64|arm64) UV_TARGET="aarch64-unknown-linux-gnu"; UV_SHA256="0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d" ;; \
        *) echo "Unsupported architecture for uv: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    UV_TMP="$(mktemp -d)"; \
    curl -fsSL --retry 3 --retry-delay 2 \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TARGET}.tar.gz" \
        -o "${UV_TMP}/uv.tar.gz"; \
    printf '%s  %s\n' "${UV_SHA256}" "${UV_TMP}/uv.tar.gz" | sha256sum -c -; \
    tar -xzf "${UV_TMP}/uv.tar.gz" -C "${UV_TMP}"; \
    install -D -m 0755 "${UV_TMP}/uv-${UV_TARGET}/uv" /usr/local/bin/uv; \
    install -D -m 0755 "${UV_TMP}/uv-${UV_TARGET}/uvx" /usr/local/bin/uvx; \
    rm -rf "${UV_TMP}"; \
    uv --version

RUN groupadd --gid 1000 biomodstack \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash biomodstack \
    && mkdir -p /app/platform/api /var/lib/biomodstack \
    && chown -R biomodstack:biomodstack /app /var/lib/biomodstack

WORKDIR /app/platform/api

USER biomodstack

RUN --mount=type=bind,source=.,target=/src,readonly \
    cp -R --no-preserve=ownership,timestamps /src/. /app \
    && uv sync --frozen --no-dev \
    && python /app/scripts/check_removed_vocabulary.py --mode sanitize \
        /app/platform/api/.venv/lib/python3.10/site-packages \
    && rm -rf "${UV_CACHE_DIR}" /home/biomodstack/.cache/uv \
    && python -c 'import os, shutil; from pathlib import Path; venv=Path("/app/platform/api/.venv"); [shutil.rmtree(path) for path in sorted(venv.rglob("__pycache__"), key=lambda path: len(path.parts), reverse=True)]; root=Path("/app"); epoch=int(os.environ["SOURCE_DATE_EPOCH"]); [os.utime(path, (epoch, epoch), follow_symlinks=False) for path in [root, *root.rglob("*")]]'

ENV BMS_HOME=/app \
    BMS_DATA=/var/lib/biomodstack \
    BMS_INPUTS=/var/lib/biomodstack/inputs \
    BMS_DB_PATH=/var/lib/biomodstack/biomodstack.db

EXPOSE 18000

CMD ["/bin/sh", "-ec", "/app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 18000"]

FROM api-base AS api-runtime-prepared

USER root
ARG MAMBA_ROOT_PREFIX=/opt/micromamba
ARG MICROMAMBA_URL=https://github.com/mamba-org/micromamba-releases/releases/download/2.5.0-2/micromamba-linux-64
ARG MICROMAMBA_SHA256=c04571cfb0750e5432d530a3068b8fcd232ebed3133358e056e59a90b9852b00
ENV MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX} \
    BMS_MICROMAMBA_BIN=/usr/local/bin/micromamba \
    BMS_MICROMAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX} \
    BMS_PLANNOTATE_ENV=plannotate \
    BMS_PLANNOTATE_VERSION=2.0.0 \
    BMS_PLANNOTATE_STREAMLIT_VERSION=1.59.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bzip2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
        /var/log/apt/* \
        /var/log/dpkg.log \
        /var/log/alternatives.log \
        /var/cache/ldconfig/aux-cache

RUN mkdir -p "${MAMBA_ROOT_PREFIX}" \
    && curl -fsSL "${MICROMAMBA_URL}" -o /usr/local/bin/micromamba \
    && echo "${MICROMAMBA_SHA256}  /usr/local/bin/micromamba" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/micromamba \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" create -y -n "${BMS_PLANNOTATE_ENV}" \
        --file /app/docker/plannotate-conda-linux-64.lock \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" python -c "from importlib.metadata import version; expected={'plannotate': '2.0.0', 'streamlit': '1.59.2', 'pandas': '2.3.3', 'bokeh': '3.9.1'}; actual={name: version(name) for name in expected}; assert actual == expected, (actual, expected); import plannotate.streamlit_app" \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" plannotate setupdb \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" plannotate databases >/tmp/plannotate-databases.json \
    && test -s /tmp/plannotate-databases.json \
    && python /app/scripts/check_removed_vocabulary.py --mode sanitize \
        /app/platform/api/.venv/lib/python3.10/site-packages \
        "${MAMBA_ROOT_PREFIX}/envs/${BMS_PLANNOTATE_ENV}/lib/python3.12/site-packages" \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" clean -a -y \
    && rm -rf "${MAMBA_ROOT_PREFIX}/pkgs" \
        /root/.cache \
        /root/.conda \
        /root/.mamba \
        /usr/local/lib/python3.10/site-packages/pip* \
        /usr/local/lib/python3.10/site-packages/setuptools* \
        /usr/local/lib/python3.10/site-packages/uv* \
        /usr/local/bin/pip* /usr/local/bin/uv /usr/local/bin/uvx \
    && rm -f "${MAMBA_ROOT_PREFIX}/envs/${BMS_PLANNOTATE_ENV}/conda-meta/history" \
        /tmp/plannotate-databases.json \
        /tmp/uv-*.lock \
    && chown -R biomodstack:biomodstack "${MAMBA_ROOT_PREFIX}" \
    && python -c 'import os; from pathlib import Path; epoch=int(os.environ["SOURCE_DATE_EPOCH"]); excluded={"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}; roots=[Path(value) for value in ("/app", "/bin", "/etc", "/home", "/lib", "/lib64", "/opt", "/root", "/run", "/sbin", "/srv", "/tmp", "/usr", "/var") if Path(value).exists()]; [os.utime(path, (epoch, epoch), follow_symlinks=False) for root in roots for path in [root, *root.rglob("*")] if str(path) not in excluded]; os.utime(Path("/"), (epoch, epoch), follow_symlinks=False)'

FROM scratch AS api-runtime

ARG BMS_BUILD_SHA=unknown
ARG BMS_BUILD_ID=development
ARG BMS_BUILD_TIME=unknown

LABEL org.opencontainers.image.revision=$BMS_BUILD_SHA \
      org.opencontainers.image.created=$BMS_BUILD_TIME \
      org.opencontainers.image.version=$BMS_BUILD_ID

COPY --from=api-runtime-prepared / /

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BMS_BUILD_SHA=$BMS_BUILD_SHA \
    BMS_BUILD_ID=$BMS_BUILD_ID \
    BMS_BUILD_TIME=$BMS_BUILD_TIME \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    UV_CACHE_DIR=/tmp/uv-cache \
    BMS_HOME=/app \
    BMS_DATA=/var/lib/biomodstack \
    BMS_INPUTS=/app/inputs \
    BMS_DB_PATH=/var/lib/biomodstack/bms.db \
    MAMBA_ROOT_PREFIX=/opt/micromamba \
    BMS_MICROMAMBA_BIN=/usr/local/bin/micromamba \
    BMS_MICROMAMBA_ROOT_PREFIX=/opt/micromamba \
    BMS_PLANNOTATE_ENV=plannotate

WORKDIR /app/platform/api

USER 1000:1000

EXPOSE 18000

CMD ["/bin/sh", "-ec", "/app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 18000"]
