#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="${BMS_ONT_CONTAINER_RUNTIME:-docker}"
TAG="${BMS_ONT_SLOW5TOOLS_IMAGE_TAG:-biomodstack/ont-raw-signal:blue-crab-0.5.0-slow5tools-1.4.0}"

case "$RUNTIME" in
    docker|podman) ;;
    *)
        echo "Unsupported BMS_ONT_CONTAINER_RUNTIME: $RUNTIME" >&2
        exit 64
        ;;
esac
if ! command -v "$RUNTIME" >/dev/null 2>&1; then
    echo "Container runtime is unavailable: $RUNTIME" >&2
    exit 69
fi

"$RUNTIME" build \
    --file "$REPO_ROOT/docker/ont-raw-signal.Dockerfile" \
    --tag "$TAG" \
    "$REPO_ROOT"

IMAGE_ID="$($RUNTIME image inspect --format '{{.Id}}' "$TAG")"
case "$IMAGE_ID" in
    sha256:[0-9a-f][0-9a-f]*) ;;
    *)
        echo "Built image has an invalid immutable ID: $IMAGE_ID" >&2
        exit 70
        ;;
esac
DIGEST="${IMAGE_ID#sha256:}"
if [ "${#DIGEST}" -ne 64 ]; then
    echo "Built image digest has an invalid length" >&2
    exit 70
fi

printf '%s\n' \
    "BMS_ONT_CONTAINER_RUNTIME=$RUNTIME" \
    "BMS_ONT_SLOW5TOOLS_IMAGE=$IMAGE_ID" \
    "BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST=$DIGEST" \
    "BMS_ONT_RAW_SIGNAL_STAGING_ROOT=/mnt/BioModStack/ont-raw-signal-staging" \
    "BMS_ONT_RAW_SIGNAL_ACQUISITION_PRESSURE=unknown" \
    "BMS_ONT_BLOW5_CONVERSION_QUALIFIED=0" \
    "BMS_ONT_LIVE_CONVERSION_ENABLED=1" \
    "BMS_ONT_RAW_SIGNAL_RETENTION_POLICY=pod5_and_blow5"
