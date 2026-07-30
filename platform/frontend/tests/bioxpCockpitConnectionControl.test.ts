import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('connection is explicit and reconnectable in one click', () => {
  assert.match(source, /useConnectBioXp/);
  assert.match(source, /useDisconnectBioXp/);
  assert.match(source, /active \? 'Reconnect BMS Link' : 'Connect BMS Link'/);
  assert.match(source, />Disconnect<\/button>/);
  assert.doesNotMatch(source, />UNKNOWN</);
});
