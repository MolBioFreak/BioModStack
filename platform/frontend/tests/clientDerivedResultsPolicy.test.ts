import assert from 'node:assert/strict';
import test from 'node:test';

import { getClientDerivedResultsPolicy } from '../src/lib/clientDerivedResultsPolicy.js';

test('rejects client-derived sorting or filtering when the bounded working set is incomplete', () => {
  assert.deepEqual(getClientDerivedResultsPolicy({ total: 501, loaded: 500, requiresClientDerivation: true }), {
    allowed: false,
    message: 'Client-side sorting and source/result-set filters require a result set of 500 designs or fewer. Narrow the server-side filters first.',
  });
});

test('permits client-derived sorting or filtering when the bounded working set is complete', () => {
  assert.deepEqual(getClientDerivedResultsPolicy({ total: 500, loaded: 500, requiresClientDerivation: true }), {
    allowed: true,
    message: null,
  });
});

test('does not restrict normal server-paginated results', () => {
  assert.deepEqual(getClientDerivedResultsPolicy({ total: 20_000, loaded: 50, requiresClientDerivation: false }), {
    allowed: true,
    message: null,
  });
});
