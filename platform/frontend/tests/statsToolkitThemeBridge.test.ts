import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BMS_STATS_THEME_MESSAGE_TYPE,
  BMS_STATS_THEME_TOKEN_NAMES,
  buildStatsThemePayload,
  resolveExactTargetOrigin,
} from '../src/runtime/statsToolkitThemeBridge.js';

test('stats theme payload carries every canonical semantic token', () => {
  const payload = buildStatsThemePayload('nord', (name) => ` value-${name.slice(2)} `);
  assert.equal(payload.type, BMS_STATS_THEME_MESSAGE_TYPE);
  assert.equal(payload.theme, 'nord');
  assert.deepEqual(Object.keys(payload.tokens), [...BMS_STATS_THEME_TOKEN_NAMES]);
  assert.equal(payload.tokens['--accent-primary'], 'value-accent-primary');
});

test('theme payload refuses incomplete computed theme state', () => {
  assert.throws(
    () => buildStatsThemePayload('midnight', (name) => (name === '--warning' ? '' : '#123456')),
    /--warning is empty/,
  );
});

test('iframe messages target the exact discovered add-on origin', () => {
  assert.equal(
    resolveExactTargetOrigin('/stats/embed/', 'https://compute-node.example/bms/'),
    'https://compute-node.example',
  );
  assert.equal(
    resolveExactTargetOrigin('https://stats.internal.example/embed/', 'https://bms.example/'),
    'https://stats.internal.example',
  );
});
