import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  DEFAULT_ZOOM_FACTOR,
  adjustZoomFactor,
  clampZoomFactor,
  formatZoomPercentage,
  readPersistedZoomFactor,
  writePersistedZoomFactor,
} from '../src/zoom.js';

const MAIN_SOURCE_PATH = path.resolve(process.cwd(), 'src/main.ts');

test('clampZoomFactor normalizes invalid and out-of-range values', () => {
  assert.equal(clampZoomFactor(Number.NaN), DEFAULT_ZOOM_FACTOR);
  assert.equal(clampZoomFactor(0.2), 0.8);
  assert.equal(clampZoomFactor(3), 1.5);
  assert.equal(clampZoomFactor(1.13), 1.13);
});

test('adjustZoomFactor applies 10% shell zoom steps within the supported range', () => {
  assert.equal(adjustZoomFactor(1, 1), 1.1);
  assert.equal(adjustZoomFactor(1, -1), 0.9);
  assert.equal(adjustZoomFactor(1.48, 1), 1.5);
  assert.equal(adjustZoomFactor(0.81, -1), 0.8);
});

test('zoom factor persistence round-trips through the shell zoom settings file', () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'bms-shell-zoom-'));
  const zoomPath = path.join(tempDir, 'shell-zoom.json');

  try {
    assert.equal(readPersistedZoomFactor(zoomPath), DEFAULT_ZOOM_FACTOR);

    writePersistedZoomFactor(zoomPath, 1.24);

    assert.equal(readPersistedZoomFactor(zoomPath), 1.24);
    assert.equal(JSON.parse(readFileSync(zoomPath, 'utf8')).zoomFactor, 1.24);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
});

test('zoom persistence falls back cleanly when the settings file is corrupt', () => {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'bms-shell-zoom-'));
  const zoomPath = path.join(tempDir, 'shell-zoom.json');

  try {
    writeFileSync(zoomPath, '{ not json }', 'utf8');
    assert.equal(readPersistedZoomFactor(zoomPath), DEFAULT_ZOOM_FACTOR);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
});

test('formatZoomPercentage renders the current shell zoom as a human-friendly percentage', () => {
  assert.equal(formatZoomPercentage(1), '100%');
  assert.equal(formatZoomPercentage(1.25), '125%');
  assert.equal(formatZoomPercentage(0.83), '83%');
});

test('zoom IPC handlers refresh the application menu after renderer-driven shell zoom changes', () => {
  const source = readFileSync(MAIN_SOURCE_PATH, 'utf8');

  assert.match(source, /ipcMain\.handle\(SET_ZOOM_FACTOR_CHANNEL[\s\S]*refreshApplicationMenuState\(\)/);
  assert.match(source, /ipcMain\.handle\(ADJUST_ZOOM_CHANNEL[\s\S]*refreshApplicationMenuState\(\)/);
  assert.match(source, /ipcMain\.handle\(RESET_ZOOM_CHANNEL[\s\S]*refreshApplicationMenuState\(\)/);
});
