import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const readSource = (relativePath: string) => readFileSync(
    fileURLToPath(new URL(`../${relativePath}`, import.meta.url)),
    'utf8',
);

const resultsViewer = readSource('src/components/ResultsViewer.tsx');
const api = readSource('src/lib/api.ts');

test('Data Viewer job discovery sends the selector search to the server', () => {
    assert.match(resultsViewer, /debouncedJobSelectorSearch/u);
    assert.match(resultsViewer, /fetchJobs\(\{[\s\S]*q:\s*debouncedJobSelectorSearch\s*\|\|\s*undefined/u);
    assert.doesNotMatch(resultsViewer, /fetchJobs\(\{\s*include_children:\s*true,\s*limit:\s*500/u);
});

test('Data Viewer design requests ask for exact server aggregates', () => {
    assert.match(api, /export interface DesignAggregateSummary/u);
    assert.match(api, /summary\?:\s*DesignAggregateSummary\s*\|\s*null/u);
    assert.match(api, /include_summary\?:\s*boolean/u);
    assert.match(resultsViewer, /include_summary:\s*true/u);
    assert.match(resultsViewer, /applyAuthoritativeDesignSummary/u);
    assert.match(resultsViewer, /const statsDesigns = useClientSourcePagination \? sourceScopedDesigns : designs/u);
});

test('Data Viewer renders retryable job and design query failures', () => {
    assert.match(resultsViewer, /jobsError/u);
    assert.match(resultsViewer, /routedJobError/u);
    assert.match(resultsViewer, /designsError/u);
    assert.match(resultsViewer, /DataViewerQueryError/u);
    assert.match(resultsViewer, />Retry</u);
});
