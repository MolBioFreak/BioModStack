import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const apiSource = readFileSync('src/lib/api.ts', 'utf8');
const dashboardSource = readFileSync('src/components/Dashboard.tsx', 'utf8');
const quickViewerSource = readFileSync('src/components/QuickViewer.tsx', 'utf8');
const resultsViewerSource = readFileSync('src/components/ResultsViewer.tsx', 'utf8');

test('mobile/recent job list calls use lightweight summaries instead of full job payloads', () => {
  assert.match(apiSource, /summary\?: boolean;/u);
  assert.match(dashboardSource, /fetchJobs\(\{ limit: 100, summary: true \}\)/u);
  assert.match(quickViewerSource, /fetchJobs\(\{ status: 'completed', limit: 100, summary: true \}\)/u);
  assert.match(resultsViewerSource, /fetchJobs\(\{ include_children: true, limit: 500, summary: true \}\)/u);
});

test('summary lists hydrate full job detail before using params-heavy dashboard actions', () => {
  assert.match(dashboardSource, /const hydrateJobForDetail = async \(job: Job\): Promise<Job> =>/u);
  assert.match(dashboardSource, /fetchJobById\(job\.id\)/u);
  assert.match(dashboardSource, /const handleResume = async \(job: Job\) =>/u);
  assert.match(dashboardSource, /const detailedJob = await hydrateJobForDetail\(job\);/u);
  assert.match(dashboardSource, /const handleClone = async \(job: Job\) =>/u);
  assert.match(resultsViewerSource, /enabled: Boolean\(jobId\)/u);
  assert.match(resultsViewerSource, /baseJobs\.map\(\(job: Job\) => job\.id === routedJob\.id \? routedJob : job\)/u);
});
