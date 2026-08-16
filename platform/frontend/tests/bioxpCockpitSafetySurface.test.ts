import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('unproven physical emergency abort is explicit and cannot dispatch', () => {
  assert.match(source, /Physical Emergency Abort Unavailable/);
  assert.match(source, /emergency_stop\.reason/);
  assert.match(source, /<button[\s\S]*?disabled[\s\S]*?>Emergency Abort Unavailable<\/button>/);
  assert.doesNotMatch(source, /emergencyStop\.mutate/);
});

test('cockpit has no stale or unknown derived authorization model', () => {
  for (const value of ['UNKNOWN', 'STALE', 'deriveBioXpStatus', 'isBioXpControlPlaneFresh', 'maintenanceState']) assert.doesNotMatch(source, new RegExp(value));
});
