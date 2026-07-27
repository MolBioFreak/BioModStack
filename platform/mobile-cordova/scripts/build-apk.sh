#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$PROJECT_DIR/cordova.runtime.json}"
ANDROID_PLATFORM_VERSION="${ANDROID_PLATFORM_VERSION:-13.0.0}"
LOCAL_UI_BUNDLE_PLUGIN_DIR="$PROJECT_DIR/local-plugins/cordova-plugin-bms-ui-bundle"
LOCAL_UI_BUNDLE_PLUGIN_ID="cordova-plugin-bms-ui-bundle"
UPDATER_ANDROID_TEST_SOURCE="$PROJECT_DIR/local-plugins/cordova-plugin-bms-apk-updater/src/androidTest/BmsPackageManagerIntegrationTest.kt"

cd "$PROJECT_DIR"

# Ignore inherited HOME and desktop/configstore scope. Long-lived agent shells
# can retain both variables from hostile build probes, so resolve the account
# home from passwd before selecting the durable internal-updater signer.
CANONICAL_HOME="$(python3 - <<'PY'
import os
import pwd

print(pwd.getpwuid(os.getuid()).pw_dir)
PY
)"
if [[ "$CANONICAL_HOME" != /* ]] || [[ ! -d "$CANONICAL_HOME" ]]; then
  echo "Could not resolve a canonical account home for Android signing" >&2
  exit 1
fi
HOME="$CANONICAL_HOME"
XDG_CONFIG_HOME="$HOME/.local/share/biomodstack/cordova-build-config"
export HOME XDG_CONFIG_HOME
mkdir -p "$XDG_CONFIG_HOME"
chmod 700 "$XDG_CONFIG_HOME"
INTERNAL_UPDATE_SIGNER_SHA256="43cce218275179b99aad810bfc246732226a9a408e616d9d5615d5b0709b595a"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required because the BioModStack frontend is a pnpm workspace package" >&2
  exit 1
fi

java_home_major_version() {
  local candidate="$1"
  local raw_version
  raw_version="$("$candidate/bin/java" -XshowSettings:properties -version 2>&1 | awk -F= '/java\.specification\.version/ { gsub(/[[:space:]]/, "", $2); print $2; exit }')"
  if [[ "$raw_version" == 1.* ]]; then
    raw_version="${raw_version#1.}"
  fi
  printf '%s\n' "${raw_version%%.*}"
}

java_home_is_working() {
  local candidate="$1"
  local major_version
  [[ -n "$candidate" ]] \
    && [[ -d "$candidate" ]] \
    && "$candidate/bin/java" -version >/dev/null 2>&1 \
    && "$candidate/bin/javac" -version >/dev/null 2>&1 \
    || return 1

  major_version="$(java_home_major_version "$candidate")"
  [[ -n "$major_version" ]] && (( major_version >= 17 ))
}

pick_working_java_home() {
  local candidate
  for candidate in \
    "$HOME/.local/jdks/temurin-17" \
    "$HOME/.local/openjdk-17-jdk-headless/usr/lib/jvm/java-17-openjdk-amd64" \
    /usr/lib/jvm/java-17-openjdk-amd64
  do
    if java_home_is_working "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [[ -n "${JAVA_HOME:-}" ]] && ! java_home_is_working "$JAVA_HOME"; then
  echo "JAVA_HOME is set but not usable; falling back to an auto-detected Java 17 runtime: $JAVA_HOME" >&2
  unset JAVA_HOME
fi

if [[ -z "${JAVA_HOME:-}" ]]; then
  AUTO_JAVA_HOME="$(pick_working_java_home || true)"
  if [[ -n "$AUTO_JAVA_HOME" ]]; then
    export JAVA_HOME="$AUTO_JAVA_HOME"
  fi
fi
if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi
if ! command -v java >/dev/null 2>&1; then
  echo "java is required" >&2
  exit 1
fi
if ! command -v javac >/dev/null 2>&1; then
  echo "javac is required" >&2
  exit 1
fi
if [[ -d "$HOME/.local/gradle/gradle-8.7/bin" ]]; then
  export PATH="$HOME/.local/gradle/gradle-8.7/bin:$PATH"
fi

JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Djava.util.concurrent.ForkJoinPool.common.parallelism=1 -XX:ActiveProcessorCount=2"
export JAVA_TOOL_OPTIONS

GRADLE_USER_HOME="${GRADLE_USER_HOME:-$PROJECT_DIR/.cache/gradle-user-home}"
export GRADLE_USER_HOME
mkdir -p "$GRADLE_USER_HOME"
cat > "$GRADLE_USER_HOME/gradle.properties" <<'EOF'
org.gradle.daemon=false
org.gradle.workers.max=1
org.gradle.jvmargs=-Xmx1024m -Dfile.encoding=UTF-8 -Djava.util.concurrent.ForkJoinPool.common.parallelism=1 -XX:ActiveProcessorCount=2
EOF

ensure_gradle_property() {
  local file="$1"
  local key="$2"
  local value="$3"
  mkdir -p "$(dirname "$file")"
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    python3 - "$file" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1:4]
lines = open(path, encoding='utf-8').read().splitlines()
with open(path, 'w', encoding='utf-8') as handle:
    for line in lines:
        if line.startswith(key + '='):
            handle.write(f'{key}={value}\n')
        else:
            handle.write(line + '\n')
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

echo "Using JAVA_HOME=${JAVA_HOME:-unset}"
if ! java -version; then
  echo "java failed to run from JAVA_HOME=${JAVA_HOME:-unset}" >&2
  exit 1
fi
if ! javac -version; then
  echo "javac failed to run from JAVA_HOME=${JAVA_HOME:-unset}" >&2
  exit 1
fi
if command -v gradle >/dev/null 2>&1; then
  gradle -v | sed -n '1,15p'
fi

pick_working_android_sdk_root() {
  local candidate
  for candidate in \
    "$HOME/Android/Sdk" \
    "$HOME/.local/android-sdk" \
    /opt/android-sdk
  do
    if [[ -d "$candidate/platform-tools" ]] && [[ -d "$candidate/build-tools" ]] && [[ -d "$candidate/platforms" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [[ -z "$SDK_ROOT" ]]; then
  SDK_ROOT="$(pick_working_android_sdk_root || true)"
fi
if [[ -z "$SDK_ROOT" ]]; then
  echo "Set ANDROID_SDK_ROOT (or ANDROID_HOME) to your Android SDK root before building." >&2
  exit 1
fi

export ANDROID_SDK_ROOT="$SDK_ROOT"
export ANDROID_HOME="$SDK_ROOT"
export PATH="$SDK_ROOT/platform-tools:$SDK_ROOT/cmdline-tools/latest/bin:$SDK_ROOT/emulator:$PATH"

if [[ ! -d node_modules ]]; then
  npm install
fi

# Cordova identifies a project only after its local web root exists.
node ./scripts/prepare-bms-assets.mjs --config "$CONFIG_PATH"

if [[ ! -d platforms/android ]]; then
  npx cordova platform add "android@$ANDROID_PLATFORM_VERSION"
fi
if [[ -f platforms/android/gradle.properties ]]; then
  ensure_gradle_property "$PROJECT_DIR/platforms/android/gradle.properties" "org.gradle.daemon" "false"
  ensure_gradle_property "$PROJECT_DIR/platforms/android/gradle.properties" "org.gradle.workers.max" "1"
  ensure_gradle_property "$PROJECT_DIR/platforms/android/gradle.properties" "org.gradle.jvmargs" "-Xmx1024m -Dfile.encoding=UTF-8 -Djava.util.concurrent.ForkJoinPool.common.parallelism=1 -XX:ActiveProcessorCount=2"
  ensure_gradle_property "$PROJECT_DIR/platforms/android/gradle.properties" "android.suppressUnsupportedCompileSdk" "35"
fi

if [[ -d "$LOCAL_UI_BUNDLE_PLUGIN_DIR" ]]; then
  INSTALLED_PLUGINS="$(npx cordova plugin list 2>/dev/null || true)"
  if [[ "$INSTALLED_PLUGINS" != *"$LOCAL_UI_BUNDLE_PLUGIN_ID"* ]]; then
    npx cordova plugin add "$LOCAL_UI_BUNDLE_PLUGIN_DIR"
  fi
fi

npx cordova prepare android
UPDATER_ANDROID_TEST_PACKAGE_DIR="$PROJECT_DIR/platforms/android/app/src/androidTest/java/org/biomodstack/mobile/apkupdate"
UPDATER_ANDROID_TEST_TARGET="$UPDATER_ANDROID_TEST_PACKAGE_DIR/BmsPackageManagerIntegrationTest.kt"
if [[ -f "$UPDATER_ANDROID_TEST_SOURCE" ]]; then
  # Cordova does not refresh androidTest sources from an already-installed
  # local plugin. Replace the generated package so stale, unversioned
  # acceptance probes cannot silently join the authoritative suite.
  rm -rf "$UPDATER_ANDROID_TEST_PACKAGE_DIR"
  install -D -m 0644 "$UPDATER_ANDROID_TEST_SOURCE" "$UPDATER_ANDROID_TEST_TARGET"
fi
node ./scripts/patch-android-main-activity.mjs
if ! npx cordova requirements android; then
  echo "cordova requirements reported issues; attempting build anyway because the Android platform ships a Gradle wrapper." >&2
fi
CORDOVA_ANDROID_GRADLE_ARGS="${CORDOVA_ANDROID_GRADLE_ARGS:---no-daemon --max-workers=1}"
echo "Using Cordova Gradle args: $CORDOVA_ANDROID_GRADLE_ARGS"
BUILD_VARIANT="${BMS_ANDROID_BUILD_VARIANT:-debug}"
case "$BUILD_VARIANT" in
  debug)
    npx cordova build android --debug -- --gradleArg="$CORDOVA_ANDROID_GRADLE_ARGS"
    APK_PATH="$PROJECT_DIR/platforms/android/app/build/outputs/apk/debug/app-debug.apk"
    ;;
  internal-update)
    (
      cd "$PROJECT_DIR/platforms/android"
      gradle assembleBmsInternalUpdate --no-daemon --max-workers=1
    )
    APK_PATH="$PROJECT_DIR/platforms/android/app/build/outputs/apk/bmsInternalUpdate/app-bmsInternalUpdate.apk"
    ;;
  *)
    echo "BMS_ANDROID_BUILD_VARIANT must be debug or internal-update" >&2
    exit 1
    ;;
esac

if [[ ! -f "$APK_PATH" ]]; then
  echo "Build completed but APK was not found at $APK_PATH" >&2
  exit 1
fi

if [[ "$BUILD_VARIANT" == "internal-update" ]]; then
  APKSIGNER="$SDK_ROOT/build-tools/35.0.0/apksigner"
  if [[ ! -x "$APKSIGNER" ]]; then
    echo "API-35 apksigner is required to verify the frozen internal-update signer" >&2
    exit 1
  fi
  ACTUAL_SIGNER_SHA256="$(
    "$APKSIGNER" verify --print-certs "$APK_PATH" \
      | awk -F': ' '/Signer #1 certificate SHA-256 digest/ {print tolower($2); exit}'
  )"
  if [[ "$ACTUAL_SIGNER_SHA256" != "$INTERNAL_UPDATE_SIGNER_SHA256" ]]; then
    echo "Internal-update signer mismatch: expected $INTERNAL_UPDATE_SIGNER_SHA256, got ${ACTUAL_SIGNER_SHA256:-missing}" >&2
    exit 1
  fi
fi

printf 'APK: %s\n' "$APK_PATH"
