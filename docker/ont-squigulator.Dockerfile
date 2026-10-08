ARG SOURCE_DATE_EPOCH=1
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS build

ARG SQUIGULATOR_VERSION=0.5.0
ARG SQUIGULATOR_COMMIT=c5f0c619a28b9532388877096acb7568c34b9c4b
ARG SQUIGULATOR_RELEASE_ASSET=squigulator-v0.5.0-release.tar.gz
ARG SQUIGULATOR_RELEASE_SHA256=f8b428655d586427c6e0c939d4a0383fa8569523234e3c21951edcd23372a66a

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl build-essential zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    curl -fsSL --retry 3 \
      "https://github.com/hasindu2008/squigulator/releases/download/v${SQUIGULATOR_VERSION}/${SQUIGULATOR_RELEASE_ASSET}" \
      -o "/tmp/${SQUIGULATOR_RELEASE_ASSET}"; \
    echo "${SQUIGULATOR_RELEASE_SHA256}  /tmp/${SQUIGULATOR_RELEASE_ASSET}" | sha256sum -c -; \
    mkdir -p /src; \
    tar -xzf "/tmp/${SQUIGULATOR_RELEASE_ASSET}" --strip-components=1 -C /src; \
    test "$(git=; sed -n 's/^#define SQUIGULATOR_VERSION[[:space:]]*"\([^"]*\)"/\1/p' /src/src/sim.h 2>/dev/null || true)" != "0.0.0" || true; \
    make -C /src -j1; \
    install -m 0755 /src/squigulator /usr/local/bin/squigulator; \
    mkdir -p /licenses/squigulator /licenses/slow5lib /licenses/streamvbyte; \
    cp /src/LICENSE /licenses/squigulator/LICENSE; \
    find /src -path '*/slow5lib*/LICENSE*' -type f -print -quit | xargs -r -I{} cp {} /licenses/slow5lib/LICENSE; \
    find /src -path '*/streamvbyte*/LICENSE*' -type f -print -quit | xargs -r -I{} cp {} /licenses/streamvbyte/LICENSE

FROM python:3.12-slim-bookworm@sha256:1704d57628b46e9a8e06d90e92a6c38f87e6a04ade7afc3e7a718b35de889a13
ARG SOURCE_DATE_EPOCH
ARG SQUIGULATOR_VERSION=0.5.0
ARG SQUIGULATOR_COMMIT=c5f0c619a28b9532388877096acb7568c34b9c4b

LABEL org.opencontainers.image.title="BioModStack governed Squigulator producer runtime" \
      org.opencontainers.image.description="Pinned offline one-record ideal-signal producer" \
      org.opencontainers.image.version="squigulator-${SQUIGULATOR_VERSION}" \
      org.opencontainers.image.revision="${SQUIGULATOR_COMMIT}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends zlib1g \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
COPY --from=build /usr/local/bin/squigulator /usr/local/bin/squigulator
COPY --from=build /licenses /usr/share/licenses/bms-ont-squigulator
COPY scripts/ont_squigulator_runtime.py /opt/bms/ont_squigulator_runtime.py
RUN set -eux; \
    squigulator --version; \
    python3 /opt/bms/ont_squigulator_runtime.py --help >/dev/null; \
    find /opt/bms -type d -name __pycache__ -prune -exec rm -rf '{}' +; \
    python3 -c 'import os; from pathlib import Path; epoch=int(os.environ["SOURCE_DATE_EPOCH"]); roots=[Path("/opt/bms"), Path("/usr/local/bin"), Path("/usr/share/licenses/bms-ont-squigulator")]; [os.utime(path, (epoch, epoch), follow_symlinks=False) for root in roots for path in [root, *root.rglob("*")]]'

USER 65534:65534
WORKDIR /work
ENTRYPOINT []
CMD ["python3", "/opt/bms/ont_squigulator_runtime.py", "--help"]
