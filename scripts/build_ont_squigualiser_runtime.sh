#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="${BMS_CONTAINER_RUNTIME:-docker}"
TAG="${BMS_ONT_SQUIGUALISER_IMAGE_TAG:-biomodstack/ont-squigualiser:0.7.0}"
POLICY_PATH="$REPO_ROOT/platform/api/config/ont_signal_workbench/runtime_policy_v1.json"

case "$RUNTIME" in
    docker|podman) ;;
    *)
        echo "Unsupported BMS_CONTAINER_RUNTIME: $RUNTIME" >&2
        exit 64
        ;;
esac
if ! command -v "$RUNTIME" >/dev/null 2>&1; then
    echo "Container runtime is unavailable: $RUNTIME" >&2
    exit 69
fi
if ! POLICY_VALUES="$(python3 - "$POLICY_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    policy = json.load(handle)
print(policy["runtime_id"], policy["oci_digest"], sep="\t")
PY
)"; then
    echo "ONT Squigualiser runtime policy cannot be read: $POLICY_PATH" >&2
    exit 69
fi
IFS="$(printf '\t')" read -r APPROVED_RUNTIME_ID APPROVED_OCI_DIGEST <<< "$POLICY_VALUES"
if [ -z "$APPROVED_RUNTIME_ID" ] || [ -z "$APPROVED_OCI_DIGEST" ] || [ "$APPROVED_RUNTIME_ID" != "$APPROVED_OCI_DIGEST" ]; then
    echo "ONT Squigualiser runtime policy identities are invalid" >&2
    exit 70
fi

"$RUNTIME" build \
    --file "$REPO_ROOT/docker/ont-squigualiser.Dockerfile" \
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
if [ "$IMAGE_ID" != "$APPROVED_RUNTIME_ID" ] || [ "$IMAGE_ID" != "$APPROVED_OCI_DIGEST" ]; then
    echo "Built image ID does not match the approved runtime policy" >&2
    exit 70
fi

printf '%s\n' \
    "BMS_CONTAINER_RUNTIME=$RUNTIME" \
    "BMS_ONT_SQUIGUALISER_IMAGE=$IMAGE_ID" \
    "BMS_ONT_SQUIGUALISER_IMAGE_DIGEST=$DIGEST"
