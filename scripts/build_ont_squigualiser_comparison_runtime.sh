#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="${BMS_CONTAINER_RUNTIME:-docker}"
TAG="${BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE_TAG:-biomodstack/ont-squigualiser-comparison:0.7.0}"
POLICY_PATH="$REPO_ROOT/platform/api/config/ont_signal_workbench/comparison_render_runtime_policy_v1.json"

case "$RUNTIME" in docker|podman) ;; *) exit 64 ;; esac
command -v "$RUNTIME" >/dev/null 2>&1 || exit 69
APPROVED_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_id"])' "$POLICY_PATH")"
"$RUNTIME" build --file "$REPO_ROOT/docker/ont-squigualiser-comparison.Dockerfile" --tag "$TAG" "$REPO_ROOT"
IMAGE_ID="$($RUNTIME image inspect --format '{{.Id}}' "$TAG")"
test "$IMAGE_ID" = "$APPROVED_ID" || { echo "Built comparison image does not match policy" >&2; exit 70; }
printf '%s\n' "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE=$IMAGE_ID" "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE_DIGEST=${IMAGE_ID#sha256:}"
