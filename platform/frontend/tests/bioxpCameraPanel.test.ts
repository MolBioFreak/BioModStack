import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const panel = readFileSync(resolve('src/components/BioXpCameraPanel.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
test('camera keeps the finite same-origin endpoint allowlist', () => {
  for (const endpoint of ['/api/bioxp/camera/status', '/api/bioxp/camera/frame/latest', '/api/bioxp/camera/snapshot']) assert.match(client, new RegExp(endpoint.replaceAll('/', '\/')));
});
test('camera UI is image plus refresh and capture only', () => {
  for (const label of ['>Camera<', "'Refresh'", "'Capture'"]) assert.match(panel, new RegExp(label));
  for (const rejected of ['frame sequence', 'provider generation', 'dropped frames', 'content sha256', 'Camera Observability']) assert.doesNotMatch(panel, new RegExp(rejected, 'i'));
});
