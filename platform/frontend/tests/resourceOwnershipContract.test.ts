import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const results = readFileSync(join(process.cwd(), 'src', 'components', 'ResultsViewer.tsx'), 'utf8');
const main = readFileSync(join(process.cwd(), 'src', 'main.tsx'), 'utf8');


test('Results Viewer never requests a 50,000-row browser result set', () => {
  assert.doesNotMatch(results, /50000|50_000/);
  assert.match(results, /const MAX_BULK_SELECTION_DESIGNS = 500;/);
  assert.match(results, /forceBulkLoadForSorting \? MAX_BULK_SELECTION_DESIGNS : pageSize/);
});

test('React Query has explicit bounded retention and reconnect/refetch policy', () => {
  assert.match(main, /gcTime:\s*1000 \* 60 \* 10/);
  assert.match(main, /refetchOnWindowFocus:\s*false/);
  assert.match(main, /refetchOnReconnect:\s*true/);
  assert.match(main, /refetchIntervalInBackground:\s*false/);
  assert.match(main, /retryDelay:/);
});
