ARG SOURCE_DATE_EPOCH=1
FROM python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015

ARG SOURCE_DATE_EPOCH
ARG SQUIGUALISER_VERSION=0.7.0
ARG SQUIGUALISER_COMMIT=5a2404f1f43bc3227a85475c59b2b77970078b2e
ARG SQUIGUALISER_ARCHIVE_SHA256=b459f9cef1873efbfa576f33c6ca46bc9e25eaf694a5bbb709e85d1bdf7ec0fd
ARG SLOW5TOOLS_VERSION=1.4.0
ARG SLOW5TOOLS_SHA256=f6dabe68942c9699b264e6825be3391c8d5be6a03d73c1a5ed5780896dc981ca
ARG CA_CERTIFICATES_VERSION=20250419~deb12u1
ARG CURL_VERSION=7.88.1-10+deb12u15
ARG HTSLIB_VERSION=1.16+ds-3
ARG SAMTOOLS_VERSION=1.16.1-1
ARG HTSCODECS_VERSION=1.3.0-4
ARG LIBDEFLATE_VERSION=1.14-1

LABEL org.opencontainers.image.title="BioModStack governed Squigualiser runtime" \
      org.opencontainers.image.description="Pinned offline signal mapping and bounded rendering runtime" \
      org.opencontainers.image.version="squigualiser-${SQUIGUALISER_VERSION}" \
      org.opencontainers.image.revision="${SQUIGUALISER_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOKEH_RESOURCES=inline \
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH=/usr/local/bin:/usr/bin:/bin

COPY docker/ont-squigualiser-requirements.txt /opt/bms/requirements.txt
COPY scripts/ont_signal_runtime.py /opt/bms/ont_signal_runtime.py

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      "ca-certificates=${CA_CERTIFICATES_VERSION}" \
      "curl=${CURL_VERSION}" \
      "libhts3=${HTSLIB_VERSION}" \
      "libhtscodecs2=${HTSCODECS_VERSION}" \
      "libdeflate0=${LIBDEFLATE_VERSION}" \
      "samtools=${SAMTOOLS_VERSION}" \
      "tabix=${HTSLIB_VERSION}" \
    && dpkg-query -W -f='${Package}\t${Version}\n' \
      ca-certificates curl libhts3 libhtscodecs2 libdeflate0 samtools tabix \
      > /opt/bms/native-packages.tsv \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN set -eux; \
    curl -fsSL --retry 3 \
      "https://github.com/hasindu2008/slow5tools/releases/download/v${SLOW5TOOLS_VERSION}/slow5tools-v${SLOW5TOOLS_VERSION}-x86_64-linux-binaries.tar.gz" \
      -o /tmp/slow5tools.tar.gz; \
    echo "${SLOW5TOOLS_SHA256}  /tmp/slow5tools.tar.gz" | sha256sum -c -; \
    mkdir -p /tmp/slow5tools; \
    tar -xzf /tmp/slow5tools.tar.gz --strip-components=1 -C /tmp/slow5tools; \
    install -m 0755 /tmp/slow5tools/slow5tools /usr/local/bin/slow5tools; \
    rm -rf /tmp/slow5tools /tmp/slow5tools.tar.gz

RUN pip install --no-cache-dir --require-hashes -r /opt/bms/requirements.txt

RUN set -eux; \
    curl -fsSL --retry 3 \
      "https://codeload.github.com/hiruna72/squigualiser/tar.gz/${SQUIGUALISER_COMMIT}" \
      -o /tmp/squigualiser.tar.gz; \
    echo "${SQUIGUALISER_ARCHIVE_SHA256}  /tmp/squigualiser.tar.gz" | sha256sum -c -; \
    mkdir -p /tmp/squigualiser; \
    tar -xzf /tmp/squigualiser.tar.gz --strip-components=1 -C /tmp/squigualiser; \
    python3 -c 'namespace={}; exec(open("/tmp/squigualiser/src/_version.py", encoding="utf-8").read(), namespace); assert namespace["__version__"] == "0.7.0"'; \
    pip install --no-cache-dir --no-deps /tmp/squigualiser; \
    rm -rf /tmp/squigualiser /tmp/squigualiser.tar.gz

RUN set -eux; \
    squigualiser --version; \
    slow5tools --version; \
    samtools --version | grep -F 'samtools 1.16.1'; \
    bgzip --version | grep -F 'bgzip (htslib) 1.16'; \
    tabix --version | grep -F 'tabix (htslib) 1.16'; \
    python3 /opt/bms/ont_signal_runtime.py --help >/dev/null; \
    find /usr/local/lib/python3.10/site-packages /opt/bms -type d -name __pycache__ -prune -exec rm -rf '{}' +; \
    python3 -c 'import os; from pathlib import Path; epoch=int(os.environ["SOURCE_DATE_EPOCH"]); roots=[Path("/opt/bms"), Path("/usr/local/bin"), Path("/usr/local/lib/python3.10/site-packages")]; [os.utime(path, (epoch, epoch), follow_symlinks=False) for root in roots for path in [root, *root.rglob("*")]]'

USER 65534:65534
WORKDIR /work
ENTRYPOINT []
CMD ["python3", "/opt/bms/ont_signal_runtime.py", "--help"]
