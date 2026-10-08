#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK_PATH="${1:-$PROJECT_DIR/platforms/android/app/build/outputs/apk/debug/app-debug.apk}"

if [[ ! -f "$APK_PATH" ]]; then
  echo "APK not found: $APK_PATH" >&2
  exit 1
fi

python3 - "$APK_PATH" <<'PY'
import hashlib
import os
import sys
import zipfile

apk = sys.argv[1]
required = [
    'AndroidManifest.xml',
    'classes.dex',
    'assets/www/index.html',
    'assets/www/bms-runtime-config.js',
    'assets/www/bms-cordova-shim.js',
]

print(f'apk={apk}')
print(f'size_bytes={os.path.getsize(apk)}')
sha = hashlib.sha256()
with open(apk, 'rb') as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        sha.update(chunk)
print(f'sha256={sha.hexdigest()}')

with zipfile.ZipFile(apk) as archive:
    names = set(archive.namelist())
missing = [name for name in required if name not in names]
if missing:
    print('missing_entries=' + ','.join(missing))
    raise SystemExit(1)
print('zip_entries_ok=true')
PY

if command -v apksigner >/dev/null 2>&1; then
  apksigner verify --print-certs "$APK_PATH"
else
  echo "apksigner not found on PATH; skipping signature dump"
fi

PACKAGE_ID="$(python3 - "$PROJECT_DIR/config.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

tree = ET.parse(sys.argv[1])
print(tree.getroot().attrib['id'])
PY
)"
echo "package_id=$PACKAGE_ID"

if command -v adb >/dev/null 2>&1 && adb get-state >/dev/null 2>&1; then
  adb install -r "$APK_PATH"
  adb shell monkey -p "$PACKAGE_ID" -c android.intent.category.LAUNCHER 1
else
  echo "adb not connected; skipping install/launch smoke test"
fi
