import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path: string) => readFileSync(path, 'utf8');

const consumers: Record<string, RegExp> = {
  'JobBrowser.tsx': /fetchJobs\(\{[\s\S]*?limit: PAGE_SIZE,[\s\S]*?summary: true[\s\S]*?\}, undefined, signal\)/u,
  'ReferenceSelector.tsx': /fetchJobs\(\{ limit: 500, summary: true \}, undefined, signal\)/u,
  'BatchComparePane.tsx': /fetchJobs\(\{ limit: 500, summary: true \}, undefined, signal\)/u,
  'Dashboard.tsx': /fetchJobs\(\{ limit: 100, offset,[\s\S]*?summary: true \},\s*queryClient\.getQueryData/u,
  'LigandSelector.tsx': /fetchJobs\(\{ status: 'completed', limit: 50, summary: true \}\)/u,
  'DesignBrowser.tsx': /fetchJobs\(\{ limit: 500, summary: true \}, undefined, signal\)/u,
  'QuickViewer.tsx': /fetchJobs\(\{ status: 'completed', limit: 100, summary: true \}, queryClient\.getQueryData/u,
  'ResultsViewer.tsx': /fetchJobs\(\{\s*include_children: true,\s*limit: 100,\s*summary: true,\s*q: debouncedJobSelectorSearch \|\| undefined,/u,
  'NGSToolkit.tsx': /fetchJobs\(\{[\s\S]*?model_ids:[\s\S]*?include_children: true,[\s\S]*?summary: true,[\s\S]*?limit: pageSize,[\s\S]*?offset: page \* pageSize,[\s\S]*?\}, queryClient\.getQueryData\(queryKey\), signal\)/u,
};

test('every fetchJobs consumer requests a bounded SQL summary', () => {
  for (const [filename, expected] of Object.entries(consumers)) {
    const source = read(`src/components/${filename}`);
    assert.match(source, expected, `${filename} must request a bounded summary`);
  }

  const api = read('src/lib/api.ts');
  assert.match(api, /Math\.min\(500, Math\.max\(1, params\?\.limit \?\? 100\)\)/u);
  assert.match(api, /summary: params\?\.summary \?\? true/u);
});

test('job polling is centralized with bounded failure backoff and browser-managed foreground pausing', () => {
  const polling = read('src/lib/queryPolling.ts');
  assert.match(polling, /fetchFailureCount/u);
  assert.match(polling, /2 \*\* failures/u);
  assert.match(polling, /MAX_POLL_BACKOFF_MULTIPLIER/u);
  assert.doesNotMatch(polling, /return false/u);

  for (const filename of ['Dashboard.tsx', 'NGSToolkit.tsx']) {
    const source = read(`src/components/${filename}`);
    assert.match(source, /jobPollingInterval\(/u, `${filename} must use centralized job polling`);
    assert.doesNotMatch(source, /refetchInterval:\s*(3000|5000)/u);
  }
  const quickViewer = read('src/components/QuickViewer.tsx');
  assert.doesNotMatch(quickViewer, /refetchInterval:/u);
  assert.match(quickViewer, /enabled: chooserOpen/u);
});
