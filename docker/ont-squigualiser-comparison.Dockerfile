ARG SOURCE_DATE_EPOCH=1
FROM python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015

ARG SOURCE_DATE_EPOCH
ARG SQUIGUALISER_VERSION=0.7.0
ARG SQUIGUALISER_COMMIT=5a2404f1f43bc3227a85475c59b2b77970078b2e
ARG SQUIGUALISER_ARCHIVE_SHA256=b459f9cef1873efbfa576f33c6ca46bc9e25eaf694a5bbb709e85d1bdf7ec0fd
ARG CA_CERTIFICATES_VERSION=20250419~deb12u1
ARG CURL_VERSION=7.88.1-10+deb12u15

LABEL org.opencontainers.image.title="BioModStack Squigualiser comparison renderer" \
      org.opencontainers.image.description="Pinned offline two-track shared-x comparison renderer" \
      org.opencontainers.image.version="squigualiser-${SQUIGUALISER_VERSION}" \
      org.opencontainers.image.revision="${SQUIGUALISER_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOKEH_RESOURCES=inline \
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH=/usr/local/bin:/usr/bin:/bin

COPY docker/ont-squigualiser-requirements.txt /opt/bms/requirements.txt
COPY scripts/ont_signal_comparison_runtime.py /opt/bms/ont_signal_comparison_runtime.py
RUN apt-get update \
    && apt-get install -y --no-install-recommends "ca-certificates=${CA_CERTIFICATES_VERSION}" "curl=${CURL_VERSION}" \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
RUN pip install --no-cache-dir --require-hashes -r /opt/bms/requirements.txt
RUN set -eux; \
    curl -fsSL --retry 3 "https://codeload.github.com/hiruna72/squigualiser/tar.gz/${SQUIGUALISER_COMMIT}" -o /tmp/squigualiser.tar.gz; \
    echo "${SQUIGUALISER_ARCHIVE_SHA256}  /tmp/squigualiser.tar.gz" | sha256sum -c -; \
    mkdir -p /tmp/squigualiser; \
    tar -xzf /tmp/squigualiser.tar.gz --strip-components=1 -C /tmp/squigualiser; \
    python3 -c 'from pathlib import Path; lines=[line.strip() for line in Path("/tmp/squigualiser/src/_version.py").read_text(encoding="utf-8").splitlines() if line.strip().startswith("__version__ = ")]; assert lines == ["__version__ = " + chr(34) + "0.7.0" + chr(34)]'; \
    pip install --no-cache-dir --no-deps /tmp/squigualiser; \
    mkdir -p /usr/share/licenses; \
    install -m 0644 /tmp/squigualiser/LICENSE /usr/share/licenses/squigualiser-LICENSE; \
    rm -rf /tmp/squigualiser /tmp/squigualiser.tar.gz
RUN set -eux; \
    squigualiser --version; \
    python3 /opt/bms/ont_signal_comparison_runtime.py --help >/dev/null; \
    find /usr/local/lib/python3.10/site-packages /opt/bms -type d -name __pycache__ -prune -exec rm -rf '{}' +; \
    python3 -c 'import os; from pathlib import Path; epoch=int(os.environ["SOURCE_DATE_EPOCH"]); roots=[Path("/opt/bms"), Path("/usr/local/lib/python3.10/site-packages")]; [os.utime(path, (epoch, epoch), follow_symlinks=False) for root in roots for path in [root, *root.rglob("*")]]'

USER 65534:65534
WORKDIR /work
ENTRYPOINT []
CMD ["python3", "/opt/bms/ont_signal_comparison_runtime.py", "--help"]
