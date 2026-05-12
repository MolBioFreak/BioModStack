FROM python:3.10-slim-bookworm AS api-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        docker.io \
        docker-compose \
        git \
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
        libpng-dev \
        libssl-dev \
        libtiff5-dev \
        libuv1-dev \
        libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.9.12

RUN groupadd --gid 1000 biomodstack \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash biomodstack \
    && mkdir -p /var/lib/biomodstack \
    && chown -R biomodstack:biomodstack /app /var/lib/biomodstack

COPY --chown=biomodstack:biomodstack . /app

WORKDIR /app/platform/api

USER biomodstack

RUN uv sync --frozen --no-dev

ENV BMS_HOME=/app \
    BMS_DATA=/var/lib/biomodstack \
    BMS_INPUTS=/var/lib/biomodstack/inputs \
    BMS_DB_PATH=/var/lib/biomodstack/biomodstack.db

EXPOSE 8000

CMD ["/app/platform/api/.venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]

FROM api-base AS api-runtime

USER root
ARG MAMBA_ROOT_PREFIX=/opt/micromamba
ENV MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX} \
    BMS_MICROMAMBA_BIN=/usr/local/bin/micromamba \
    BMS_MICROMAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX} \
    BMS_PLANNOTATE_ENV=plannotate

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bzip2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p "${MAMBA_ROOT_PREFIX}" \
    && curl -L "https://micro.mamba.pm/api/micromamba/linux-64/latest" \
        | tar -xvj -C /usr/local/bin --strip-components=1 bin/micromamba \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" create -y -n "${BMS_PLANNOTATE_ENV}" \
        -c conda-forge -c bioconda plannotate \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" install -y -n "${BMS_PLANNOTATE_ENV}" \
        -c conda-forge "pandas<3" "setuptools<81" \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" python -c "from pathlib import Path; import streamlit; (Path(streamlit.__file__).parent / 'cli.py').write_text('from streamlit.web.cli import *\\n', encoding='utf-8')" \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" python -c "from pathlib import Path; import plannotate; p=Path(plannotate.__file__).parent / 'annotate.py'; s=p.read_text(); p.write_text(s.replace('.any(1) #only the rows that are in the columns of hit', '.any(axis=1) #only the rows that are in the columns of hit'))" \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" run -n "${BMS_PLANNOTATE_ENV}" plannotate setupdb \
    && micromamba --root-prefix "${MAMBA_ROOT_PREFIX}" clean -a -y \
    && chown -R biomodstack:biomodstack "${MAMBA_ROOT_PREFIX}"

USER biomodstack

FROM api-base AS stats-tools-runtime

USER root

ARG BMS_R_INSTALL_NCPUS=1
ENV BMS_R_INSTALL_NCPUS=${BMS_R_INSTALL_NCPUS}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        r-base \
        r-base-dev \
        r-cran-coin \
        r-cran-doparallel \
        r-cran-dorng \
        r-cran-emmeans \
        r-cran-ggally \
        r-cran-igraph \
        r-cran-lme4 \
        r-cran-matrixmodels \
        r-cran-tidyverse \
    && rm -rf /var/lib/apt/lists/*

RUN Rscript /app/docker/install_assay_r_packages.R

USER biomodstack
