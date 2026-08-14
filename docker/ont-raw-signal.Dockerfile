ARG SOURCE_DATE_EPOCH=1
FROM python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015

ARG SOURCE_DATE_EPOCH
ARG BLUE_CRAB_VERSION=0.5.0
ARG BLUE_CRAB_SHA256=c8b6b671c41540998d93cd9f4acf69a0f5afe0e016da29a095497f99b6585a71
ARG SLOW5TOOLS_VERSION=1.4.0
ARG SLOW5TOOLS_SHA256=f6dabe68942c9699b264e6825be3391c8d5be6a03d73c1a5ed5780896dc981ca
ARG PYSLOW5_VERSION=1.4.0
ARG PYSLOW5_WHEEL_SHA256=0050fdea9df8181add12c3bb9fdb908d0fd981784c23bfaaf3ad72193607909d

LABEL org.opencontainers.image.title="BioModStack ONT raw-signal runtime" \
      org.opencontainers.image.description="Pinned lossless POD5 to indexed BLOW5 conversion and validation runtime" \
      org.opencontainers.image.version="blue-crab-${BLUE_CRAB_VERSION}_slow5tools-${SLOW5TOOLS_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:/usr/bin:/bin

COPY scripts/ont_raw_signal_validate.py /opt/bms/ont_raw_signal_validate.py
COPY scripts/ont_raw_signal_lookup.py /opt/bms/ont_raw_signal_lookup.py

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN set -eux; \
    curl -fsSL --retry 3 \
      "https://github.com/Psy-Fer/blue-crab/releases/download/v${BLUE_CRAB_VERSION}/blue-crab-v${BLUE_CRAB_VERSION}-x86_64-linux-binaries.tar.gz" \
      -o /tmp/blue-crab.tar.gz; \
    echo "${BLUE_CRAB_SHA256}  /tmp/blue-crab.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/blue-crab; \
    tar -xzf /tmp/blue-crab.tar.gz --strip-components=1 -C /opt/blue-crab; \
    ln -s /opt/blue-crab/blue-crab /usr/local/bin/blue-crab; \
    rm /tmp/blue-crab.tar.gz

RUN set -eux; \
    curl -fsSL --retry 3 \
      "https://github.com/hasindu2008/slow5tools/releases/download/v${SLOW5TOOLS_VERSION}/slow5tools-v${SLOW5TOOLS_VERSION}-x86_64-linux-binaries.tar.gz" \
      -o /tmp/slow5tools.tar.gz; \
    echo "${SLOW5TOOLS_SHA256}  /tmp/slow5tools.tar.gz" | sha256sum -c -; \
    mkdir -p /tmp/slow5tools; \
    tar -xzf /tmp/slow5tools.tar.gz --strip-components=1 -C /tmp/slow5tools; \
    install -m 0755 /tmp/slow5tools/slow5tools /usr/local/bin/slow5tools; \
    rm -rf /tmp/slow5tools /tmp/slow5tools.tar.gz

RUN set -eux; \
    pyslow5_url="https://files.pythonhosted.org/packages/41/11/8fa387c0bcf60829836d494040d7cd2c8ee5704522a473a502b478af2d70/pyslow5-${PYSLOW5_VERSION}-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"; \
    pyslow5_wheel="/tmp/pyslow5-${PYSLOW5_VERSION}-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"; \
    curl -fsSL --retry 3 "$pyslow5_url" -o "$pyslow5_wheel"; \
    echo "${PYSLOW5_WHEEL_SHA256}  $pyslow5_wheel" | sha256sum -c -; \
    pip install --no-cache-dir "$pyslow5_wheel" 'numpy==2.2.6' 'pod5==0.3.35'; \
    rm "$pyslow5_wheel"

RUN set -eux; \
    blue-crab --version; \
    slow5tools --version; \
    python3 -c "import importlib.metadata, numpy, pod5, pyslow5; print(numpy.__version__, pod5.__version__, importlib.metadata.version('pyslow5'))"; \
    python3 /opt/bms/ont_raw_signal_validate.py --help >/dev/null; \
    python3 /opt/bms/ont_raw_signal_lookup.py --help >/dev/null; \
    find /opt/blue-crab /usr/local/lib/python3.10/site-packages -type d -name __pycache__ -prune -exec rm -rf '{}' +; \
    python3 -c 'import os; from pathlib import Path; epoch=int(os.environ["SOURCE_DATE_EPOCH"]); roots=[Path("/opt/blue-crab"), Path("/usr/local/bin"), Path("/usr/local/lib/python3.10/site-packages")]; [os.utime(path, (epoch, epoch), follow_symlinks=False) for root in roots for path in [root, *root.rglob("*")]]'

USER 65534:65534
WORKDIR /work
ENTRYPOINT []
CMD ["slow5tools", "--help"]
