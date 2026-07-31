import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('connection is explicit, disables an already-connected link, and permits reconnect after a link error', () => {
  assert.match(source, /useConnectBioXp/);
  assert.match(source, /useDisconnectBioXp/);
  assert.match(source, /const linkConnected = active && connection\?\.reachable !== false/);
  assert.match(source, /disabled=\{!configured \|\| linkConnected \|\| connect\.isPending \|\| disconnect\.isPending\}/);
  assert.match(source, /linkConnected \? 'BMS Link Connected' : active \? 'Reconnect BMS Link' : 'Connect BMS Link'/);
  assert.match(source, />Disconnect<\/button>/);
  assert.doesNotMatch(source, />UNKNOWN</);
});
