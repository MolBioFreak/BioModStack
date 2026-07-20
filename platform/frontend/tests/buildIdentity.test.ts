import assert from 'node:assert/strict'
import test from 'node:test'

import { buildIdentity } from '../src/lib/buildIdentity.js'

test('frontend exposes the immutable cross-surface build tuple', () => {
  assert.equal(buildIdentity.layer, 'frontend')
  assert.equal(typeof buildIdentity.revision, 'string')
  assert.equal(typeof buildIdentity.buildId, 'string')
  assert.equal(typeof buildIdentity.buildTime, 'string')
})
