import assert from 'node:assert/strict'
import test from 'node:test'

import { buildIdentity, resolveBuildIdentity } from '../src/lib/buildIdentity.js'

const REVISION = '0123456789abcdef0123456789abcdef01234567'

test('frontend resolves the immutable build tuple from Vite environment', () => {
  assert.deepEqual(
    resolveBuildIdentity({
      VITE_BMS_BUILD_SHA: REVISION,
      VITE_BMS_BUILD_ID: 'test-0123456789ab',
      VITE_BMS_BUILD_TIME: '2026-07-27T15:00:00Z',
    }),
    {
      layer: 'frontend',
      revision: REVISION,
      buildId: 'test-0123456789ab',
      buildTime: '2026-07-27T15:00:00Z',
    },
  )
})

test('frontend exposes the immutable cross-surface build tuple', () => {
  assert.equal(buildIdentity.layer, 'frontend')
  assert.equal(typeof buildIdentity.revision, 'string')
  assert.equal(typeof buildIdentity.buildId, 'string')
  assert.equal(typeof buildIdentity.buildTime, 'string')
})
