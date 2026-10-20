import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import test from 'node:test';

test('native content owns Android 15 bar, cutout and keyboard insets without changing zoom or older Android', () => {
  const root = mkdtempSync(join(tmpdir(), 'bms-window-insets-'));
  try {
    mkdirSync(join(root, 'scripts'));
    const activityDir = join(root, 'platforms/android/app/src/main/java/org/biomodstack/mobile');
    mkdirSync(activityDir, { recursive: true });
    const activity = join(activityDir, 'MainActivity.java');
    const script = join(root, 'scripts/patch-android-main-activity.mjs');
    copyFileSync(new URL('../scripts/patch-android-main-activity.mjs', import.meta.url), script);
    writeFileSync(activity, `import android.os.Bundle;
public class MainActivity extends CordovaActivity {
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        loadUrl(launchUrl);
    }
}
`);
    execFileSync(process.execPath, [script]);
    const patched = readFileSync(activity, 'utf8');
    assert.match(patched, /init\(\);\s+installBmsWindowInsets\(\);/);
    assert.match(patched, /SDK_INT < 35\) return/);
    assert.match(patched, /findViewById\(android.R.id.content\)/);
    assert.match(patched, /Type.systemBars\(\)[\s\S]*Type.displayCutout\(\)/);
    assert.match(patched, /view.setPadding\(left \+ safe.left, top \+ safe.top,/);
    assert.match(patched, /right \+ safe.right, bottom \+ safe.bottom\)/);
    assert.match(patched, /new android.view.WindowInsets.Builder\(insets\)/);
    assert.match(patched, /setInsets\(types, android.graphics.Insets.NONE\)/);
    assert.match(patched, /Type.ime\(\)/);
    assert.doesNotMatch(patched, /WindowInsets.CONSUMED/);
    assert.match(patched, /content.requestApplyInsets\(\)/);
    assert.match(patched, /setSupportZoom\(true\)/);
    execFileSync(process.execPath, [script]);
    assert.equal(readFileSync(activity, 'utf8'), patched, 'repeated prepare must not stack padding/listeners');
    writeFileSync(activity, patched.replace('\n                    | android.view.WindowInsets.Type.ime()', ''));
    execFileSync(process.execPath, [script]);
    assert.equal(readFileSync(activity, 'utf8'), patched, 'reused platform source receives the current keyboard policy');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
