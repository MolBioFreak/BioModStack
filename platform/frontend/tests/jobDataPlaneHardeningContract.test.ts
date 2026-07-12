import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path: string) => readFileSync(path, 'utf8');

const consumers: Record<string, RegExp> = {
  'JobBrowser.tsx': /fetchJobs\(\{[\s\S]*?limit: PAGE_SIZE,[\s\S]*?summary: true[\s\S]*?\}\)/u,
  'ReferenceSelector.tsx': /fetchJobs\(\{ limit: 500, summary: true \}\)/u,
  'BatchComparePane.tsx': /fetchJobs\(\{ limit: 500, summary: true \}\)/u,
  'Dashboard.tsx': /fetchJobs\(\{ limit: 100, summary: true \}\)/u,
  'LigandSelector.tsx': /fetchJobs\(\{ status: 'completed', limit: 50, summary: true \}\)/u,
  'DesignBrowser.tsx': /fetchJobs\(\{ limit: 500, summary: true \}\)/u,
  'QuickViewer.tsx': /fetchJobs\(\{ status: 'completed', limit: 100, summary: true \}\)/u,
  'ResultsViewer.tsx': /fetchJobs\(\{ include_children: true, limit: 500, summary: true \}\)/u,
  'NGSToolkit.tsx': /fetchJobs\(\{ include_children: true, model_id: 'nanopore', limit: 100, summary: true \}\)/u,
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

test('job polling is centralized and stops while hidden or offline', () => {
  const polling = read('src/lib/queryPolling.ts');
  assert.match(polling, /document\.hidden/u);
  assert.match(polling, /navigator\.onLine === false/u);
  assert.match(polling, /return false/u);

  for (const filename of ['Dashboard.tsx', 'QuickViewer.tsx', 'NGSToolkit.tsx']) {
    const source = read(`src/components/${filename}`);
    assert.match(source, /jobPollingInterval\(/u, `${filename} must use centralized job polling`);
    assert.doesNotMatch(source, /refetchInterval:\s*(3000|5000)/u);
  }
});
