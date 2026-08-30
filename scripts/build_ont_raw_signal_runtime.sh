#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="${BMS_ONT_CONTAINER_RUNTIME:-docker}"
TAG="${BMS_ONT_SLOW5TOOLS_IMAGE_TAG:-biomodstack/ont-raw-signal:blue-crab-0.5.0-slow5tools-1.4.0}"
POLICY_PATH="$REPO_ROOT/platform/api/config/ont_signal_workbench/raw_signal_runtime_policy_v1.json"
EXPECTED_POLICY_SHA256="7d504d40b1022120911400f74872b4d038d65dbbafd01ee5a0e318e9ade82a58"
ACTUAL_POLICY_SHA256="$(sha256sum "$POLICY_PATH" | awk '{print $1}')"
if [ "$ACTUAL_POLICY_SHA256" != "$EXPECTED_POLICY_SHA256" ]; then
    echo "ONT raw-signal runtime policy bytes do not match the checked-in authority" >&2
    exit 69
fi

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
if ! POLICY_VALUES="$(python3 - "$POLICY_PATH" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    policy = json.load(handle)
runtime_id = policy.get("runtime_id")
oci_digest = policy.get("oci_digest")
if (
    not isinstance(runtime_id, str)
    or not isinstance(oci_digest, str)
    or runtime_id != oci_digest
    or not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_id)
):
    raise SystemExit(1)
print(runtime_id, oci_digest.removeprefix("sha256:"), sep="\t")
PY
)"; then
    echo "ONT raw-signal runtime policy cannot be read or is invalid: $POLICY_PATH" >&2
    exit 69
fi
IFS="$(printf '\t')" read -r APPROVED_RUNTIME_ID APPROVED_DIGEST <<< "$POLICY_VALUES"

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
if [ "$IMAGE_ID" != "$APPROVED_RUNTIME_ID" ] || [ "$DIGEST" != "$APPROVED_DIGEST" ]; then
    echo "Built image ID does not match the approved raw-signal runtime policy" >&2
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
