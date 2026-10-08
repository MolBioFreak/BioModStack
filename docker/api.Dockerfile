FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libcurl4-openssl-dev \
        libfontconfig1-dev \
        libfreetype6-dev \
        libfribidi-dev \
        libharfbuzz-dev \
        libjpeg-dev \
        libpng-dev \
        libssl-dev \
        libtiff5-dev \
        libxml2-dev \
        r-base \
        r-base-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.9.12

COPY . /app

WORKDIR /app/platform/api

RUN uv sync --frozen --no-dev

RUN Rscript /app/docker/install_assay_r_packages.R

RUN groupadd --gid 1000 biomodstack \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash biomodstack \
    && mkdir -p /var/lib/biomodstack \
    && chown -R biomodstack:biomodstack /app /var/lib/biomodstack

USER biomodstack

ENV BMS_HOME=/app \
    BMS_DATA=/var/lib/biomodstack \
    BMS_INPUTS=/var/lib/biomodstack/inputs \
    BMS_DB_PATH=/var/lib/biomodstack/biomodstack.db

EXPOSE 8000

CMD ["/app/platform/api/.venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
