import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('emergency stop remains separate and capability-backed', () => {
  assert.match(source, /useBioXpEmergencyStop/);
  assert.match(source, /emergency_stop\.delivery_available/);
  assert.match(source, />Emergency Stop<\/button>/);
});
test('cockpit has no stale or unknown derived authorization model', () => {
  for (const value of ['UNKNOWN', 'STALE', 'deriveBioXpStatus', 'isBioXpControlPlaneFresh', 'maintenanceState']) assert.doesNotMatch(source, new RegExp(value));
});
