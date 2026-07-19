import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const MAIN_SOURCE = resolve(process.cwd(), 'src/main.ts');

test('initial Electron bootstrap waits for the selected runtime before creating the browser window', () => {
  const source = readFileSync(MAIN_SOURCE, 'utf8');
  const bootstrapStart = source.indexOf('async function bootstrap(): Promise<void>');
  const bootstrapEnd = source.indexOf("app.on('before-quit'", bootstrapStart);
  assert.ok(bootstrapStart >= 0 && bootstrapEnd > bootstrapStart);

  const bootstrap = source.slice(bootstrapStart, bootstrapEnd);
  const waitIndex = bootstrap.indexOf('await ensureRuntimeReady(context.runtimeMode, createServiceControl());');
  const createWindowIndex = bootstrap.indexOf('mainWindow = createMainWindow(context);');

  assert.ok(waitIndex >= 0, 'bootstrap must perform a full selected-runtime readiness wait');
  assert.ok(createWindowIndex > waitIndex, 'bootstrap must not create the shell window before readiness succeeds');
});
