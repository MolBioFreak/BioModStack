#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="${BMS_CONTAINER_RUNTIME:-docker}"
TAG="${BMS_ONT_SQUIGULATOR_IMAGE_TAG:-biomodstack/ont-squigulator:0.5.0}"

case "$RUNTIME" in docker|podman) ;; *) exit 64 ;; esac
command -v "$RUNTIME" >/dev/null 2>&1 || exit 69
"$RUNTIME" build --file "$REPO_ROOT/docker/ont-squigulator.Dockerfile" --tag "$TAG" "$REPO_ROOT"
IMAGE_ID="$($RUNTIME image inspect --format '{{.Id}}' "$TAG")"
[[ "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Invalid built image ID" >&2; exit 70; }
# Publish this measured ID through the checked-in policy after qualification.
printf '%s\n' "BMS_ONT_SQUIGULATOR_IMAGE=$IMAGE_ID" "BMS_ONT_SQUIGULATOR_IMAGE_DIGEST=${IMAGE_ID#sha256:}"
