import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyShellGraphicsWorkarounds,
  shouldDisableGpuAcceleration,
} from '../src/graphicsWorkarounds.js';

test('shouldDisableGpuAcceleration stays off by default across platforms and session types', () => {
  assert.equal(shouldDisableGpuAcceleration({ platform: 'linux', env: { XDG_SESSION_TYPE: 'x11' } }), false);
  assert.equal(shouldDisableGpuAcceleration({ platform: 'linux', env: { XDG_SESSION_TYPE: 'wayland' } }), false);
  assert.equal(shouldDisableGpuAcceleration({ platform: 'darwin', env: { XDG_SESSION_TYPE: 'x11' } }), false);
  assert.equal(shouldDisableGpuAcceleration({ platform: 'win32', env: { XDG_SESSION_TYPE: 'x11' } }), false);
});

test('BMS_ELECTRON_DISABLE_GPU remains an explicit override', () => {
  assert.equal(
    shouldDisableGpuAcceleration({
      platform: 'linux',
      env: { XDG_SESSION_TYPE: 'x11', BMS_ELECTRON_DISABLE_GPU: '0' },
    }),
    false,
  );
  assert.equal(
    shouldDisableGpuAcceleration({
      platform: 'darwin',
      env: { BMS_ELECTRON_DISABLE_GPU: '1' },
    }),
    true,
  );
});

test('applyShellGraphicsWorkarounds disables hardware acceleration only when the explicit override is active', () => {
  let disabled = 0;

  const applied = applyShellGraphicsWorkarounds(
    {
      disableHardwareAcceleration: () => {
        disabled += 1;
      },
    },
    { platform: 'linux', env: { XDG_SESSION_TYPE: 'x11', BMS_ELECTRON_DISABLE_GPU: '1' } },
  );

  assert.equal(applied, true);
  assert.equal(disabled, 1);
});
