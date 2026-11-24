ARG SOURCE_DATE_EPOCH=1
FROM python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015

ARG SOURCE_DATE_EPOCH
ARG BLUE_CRAB_VERSION=0.6.0
ARG BLUE_CRAB_SHA256=bdc4b239dfa4d094dd3466ccfd7ec7ed96a524706e7c2240c26e147894b183d5
ARG SLOW5TOOLS_VERSION=1.5.0
ARG SLOW5TOOLS_SHA256=20dadbc0a2209e272b3a03b15b744d717b8966099f18dd50eb80fb8391d58d36
ARG PYSLOW5_VERSION=1.5.0
ARG PYSLOW5_WHEEL_SHA256=3624e229936f43c771f46825ae6fae16e2d5a9b57973e26318d6decf2e7fd7a5

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
    pyslow5_url="https://files.pythonhosted.org/packages/cf/92/b088a99386186702343e3f6ad7c39b0b95a5f2d682ad991a20edfaee7e43/pyslow5-${PYSLOW5_VERSION}-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"; \
    pyslow5_wheel="/tmp/pyslow5-${PYSLOW5_VERSION}-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"; \
    curl -fsSL --retry 3 "$pyslow5_url" -o "$pyslow5_wheel"; \
    echo "${PYSLOW5_WHEEL_SHA256}  $pyslow5_wheel" | sha256sum -c -; \
    UV_VERSION="0.13.10"; \
    case "$(uname -m)" in \
        x86_64|amd64) UV_TARGET="x86_64-unknown-linux-gnu"; UV_SHA256="bf65ac1a0af10f92c522d91ac096c7c0a39657647b155577e7836c2cbbb4526c" ;; \
        aarch64|arm64) UV_TARGET="aarch64-unknown-linux-gnu"; UV_SHA256="14590f644805a6234ffe071154340669e38137f38e60aa9629690119704107d2" ;; \
        *) echo "Unsupported architecture for uv: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    UV_TMP="$(mktemp -d)"; \
    curl -fsSL --retry 3 --retry-delay 2 \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TARGET}.tar.gz" \
        -o "${UV_TMP}/uv.tar.gz"; \
    printf '%s  %s\n' "${UV_SHA256}" "${UV_TMP}/uv.tar.gz" | sha256sum -c -; \
    tar -xzf "${UV_TMP}/uv.tar.gz" -C "${UV_TMP}"; \
    "${UV_TMP}/uv-${UV_TARGET}/uv" pip install --system --no-cache "$pyslow5_wheel" 'numpy==2.2.6' 'pod5==0.3.49'; \
    rm -rf "${UV_TMP}" "$pyslow5_wheel"

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
