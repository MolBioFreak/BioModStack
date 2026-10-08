#!/usr/bin/env bash
set -euo pipefail

SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-$HOME/Android/Sdk}}"
TOOLS_URL="${ANDROID_CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-14742923_latest.zip}"
TOOLS_ZIP="${ANDROID_CMDLINE_TOOLS_ZIP:-$HOME/Downloads/$(basename "$TOOLS_URL")}"
JAVA_DEFAULT="/usr/lib/jvm/java-17-openjdk-amd64"

mkdir -p "$SDK_ROOT/cmdline-tools" "$(dirname "$TOOLS_ZIP")"

if [[ -z "${JAVA_HOME:-}" && -d "$JAVA_DEFAULT" ]]; then
  export JAVA_HOME="$JAVA_DEFAULT"
fi
if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if [[ ! -f "$TOOLS_ZIP" ]]; then
  curl -L --fail --retry 3 -o "$TOOLS_ZIP" "$TOOLS_URL"
fi

TMP_DIR=$(mktemp -d)
unzip -q -o "$TOOLS_ZIP" -d "$TMP_DIR"
rm -rf "$SDK_ROOT/cmdline-tools/latest"
mv "$TMP_DIR/cmdline-tools" "$SDK_ROOT/cmdline-tools/latest"
rm -rf "$TMP_DIR"

export ANDROID_SDK_ROOT="$SDK_ROOT"
export ANDROID_HOME="$SDK_ROOT"
export PATH="$SDK_ROOT/cmdline-tools/latest/bin:$SDK_ROOT/platform-tools:$PATH"

(yes || true) | sdkmanager --licenses >/tmp/android-sdk-licenses.log || true
sdkmanager "platform-tools" "platforms;android-34" "platforms;android-35" "build-tools;34.0.0" "build-tools;35.0.0"

echo "Android SDK ready at $SDK_ROOT"
