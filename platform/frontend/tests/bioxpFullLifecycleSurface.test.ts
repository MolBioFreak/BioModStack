import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('full lifecycle planning is not an operator control', () => {
  for (const value of ['Full OEM Lifecycle', 'Dry-run Contract', 'usePlanBioXpOemFullLifecycle', 'useCancelBioXpOemFullLifecycle']) assert.doesNotMatch(source, new RegExp(value));
});
