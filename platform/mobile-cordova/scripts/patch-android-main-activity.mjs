#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const projectDir = resolve(new URL('..', import.meta.url).pathname);
const mainActivityPath = resolve(
  projectDir,
  'platforms/android/app/src/main/java/org/biomodstack/mobile/MainActivity.java',
);

const source = readFileSync(mainActivityPath, 'utf8');

let next = source;
if (!next.includes('import android.webkit.WebSettings;')) {
  next = next.replace('import android.os.Bundle;\n', 'import android.os.Bundle;\nimport android.webkit.WebSettings;\nimport android.webkit.WebView;\n');
}

if (!next.includes('webSettings.setSupportZoom(true);')) {
  next = next.replace(
    '        super.onCreate(savedInstanceState);\n\n',
    `        super.onCreate(savedInstanceState);\n        init();\n\n        if (appView != null && appView.getView() instanceof WebView) {\n            WebSettings webSettings = ((WebView) appView.getView()).getSettings();\n            webSettings.setSupportZoom(true);\n            webSettings.setBuiltInZoomControls(true);\n            webSettings.setDisplayZoomControls(false);\n            webSettings.setUseWideViewPort(true);\n            webSettings.setLoadWithOverviewMode(true);\n        }\n\n`,
  );
}

if (next !== source) {
  writeFileSync(mainActivityPath, next);
}

const required = [
  'init();',
  'appView != null && appView.getView() instanceof WebView',
  'getSettings()',
  'setSupportZoom(true)',
  'setBuiltInZoomControls(true)',
  'setDisplayZoomControls(false)',
  'setUseWideViewPort(true)',
  'setLoadWithOverviewMode(true)',
];

for (const needle of required) {
  if (!next.includes(needle)) {
    throw new Error(`MainActivity patch missing required snippet: ${needle}`);
  }
}

if (next.indexOf('init();') > next.indexOf('getSettings()')) {
  throw new Error('Cordova init must run before WebView settings are touched');
}

console.log(`patched_main_activity=${mainActivityPath}`);
