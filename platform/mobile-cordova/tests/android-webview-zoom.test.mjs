import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const MAIN_ACTIVITY_PATH = resolve(
  process.cwd(),
  'platforms/android/app/src/main/java/org/biomodstack/mobile/MainActivity.java',
);

const PATCH_SCRIPT_PATH = resolve(
  process.cwd(),
  'scripts/patch-android-main-activity.mjs',
);

test('android main activity patch initializes Cordova before touching webview settings and enables wide viewport zoom behavior', () => {
  const source = existsSync(MAIN_ACTIVITY_PATH)
    ? readFileSync(MAIN_ACTIVITY_PATH, 'utf8')
    : readFileSync(PATCH_SCRIPT_PATH, 'utf8');

  assert.match(source, /super\.onCreate\(savedInstanceState\)/);
  assert.match(source, /init\(\)/);
  assert.match(source, /appView != null && appView\.getView\(\) instanceof WebView/);
  assert.match(source, /getSettings\(\)/);
  assert.match(source, /setSupportZoom\(true\)/);
  assert.match(source, /setBuiltInZoomControls\(true\)/);
  assert.match(source, /setDisplayZoomControls\(false\)/);
  assert.match(source, /setUseWideViewPort\(true\)/);
  assert.match(source, /setLoadWithOverviewMode\(true\)/);

  const initIndex = source.indexOf('init();');
  const settingsIndex = source.indexOf('getSettings()');
  assert.notEqual(initIndex, -1);
  assert.notEqual(settingsIndex, -1);
  assert.ok(initIndex < settingsIndex, 'Cordova init must run before touching WebView settings');
});
