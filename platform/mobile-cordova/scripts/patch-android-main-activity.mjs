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

// Android 15 enforces edge-to-edge for target 35. Inset the native content
// container, not CSS in the shell document: the remote UI is a cross-origin
// iframe and does not inherit shell styles or its safe-area variables.
if (!next.includes('installBmsWindowInsets();')) {
  next = next.replace('        init();', '        init();\n        installBmsWindowInsets();');
  const end = next.lastIndexOf('}');
  next = next.slice(0, end) + `
    private void installBmsWindowInsets() {
        if (android.os.Build.VERSION.SDK_INT < 35) return;
        final android.view.View content = findViewById(android.R.id.content);
        final int left = content.getPaddingLeft();
        final int top = content.getPaddingTop();
        final int right = content.getPaddingRight();
        final int bottom = content.getPaddingBottom();
        content.setOnApplyWindowInsetsListener((view, insets) -> {
            final int types = android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout();
            final android.graphics.Insets safe = insets.getInsets(types);
            view.setPadding(left + safe.left, top + safe.top,
                    right + safe.right, bottom + safe.bottom);
            // The native container owns these insets; descendants must not
            // apply them again. Preserve IME and all other inset types.
            return new android.view.WindowInsets.Builder(insets)
                    .setInsets(types, android.graphics.Insets.NONE).build();
        });
        content.requestApplyInsets();
    }
` + next.slice(end);
}

if (next !== source) {
  writeFileSync(mainActivityPath, next);
}

const required = [
  'init();',
  'installBmsWindowInsets();',
  'content.setOnApplyWindowInsetsListener',
  'WindowInsets.Type.systemBars()',
  'WindowInsets.Type.displayCutout()',
  'content.requestApplyInsets();',
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
