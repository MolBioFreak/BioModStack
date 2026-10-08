import assert from 'node:assert/strict';
import test from 'node:test';

import { electronAboutVersion, resolveElectronBuildIdentity } from '../src/buildIdentity.js';

test('electron build identity accepts only a full git SHA and includes app version', () => {
  const identity = resolveElectronBuildIdentity(
    {
      BMS_BUILD_SHA: '0123456789abcdef0123456789abcdef01234567',
      BMS_BUILD_ID: 'release-17',
      BMS_BUILD_TIME: '2026-07-18T04:00:00Z',
    },
    '0.2.0',
  );

  assert.deepEqual(identity, {
    layer: 'electron',
    revision: '0123456789abcdef0123456789abcdef01234567',
    buildId: 'release-17',
    buildTime: '2026-07-18T04:00:00Z',
    appVersion: '0.2.0',
  });
});

test('electron build identity fails closed for malformed provenance', () => {
  const identity = resolveElectronBuildIdentity({ BMS_BUILD_SHA: 'short' }, '0.2.0');
  assert.equal(identity.revision, 'unknown');
  assert.equal(identity.buildId, 'development');
});

test('electron About version includes the validated build revision', () => {
  const identity = resolveElectronBuildIdentity(
    { BMS_BUILD_SHA: '0123456789abcdef0123456789abcdef01234567' },
    '0.2.0',
  );
  assert.equal(
    electronAboutVersion(identity),
    '0.2.0 (revision 0123456789abcdef0123456789abcdef01234567)',
  );
});
