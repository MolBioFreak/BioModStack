import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(path, 'utf8');

test('direct and conformational-mapping results mount the same global FrustraMPNN workbench', () => {
    const direct = source('src/components/ResultsViewer.tsx');
    const detail = source('src/components/JobDetailPage.tsx');
    const cm = source('src/components/conformationalMapping/ConformationalMappingViewer.tsx');
    const shared = source('src/components/frustrampnn/FrustraMpnnWorkbench.tsx');
    const resultSurface = source('src/components/FrustraMpnnResultsViewer.tsx');
    const reviewExport = source('src/components/frustrampnn/FrustraMpnnReviewExportPanel.tsx');

    assert.match(shared, /data-frustrampnn-workbench="global"/);
    assert.match(shared, /lazy\(\(\) => import\('\.\.\/FrustraMpnnResultsViewer\.js'\)\)/);
    assert.match(shared, /<FrustraMpnnResultsViewer/);
    assert.match(direct, /<FrustraMpnnWorkbench[\s\S]*job=\{activeJob\}/);
    assert.match(direct, /<ConformationalMappingViewer[\s\S]*job=\{activeJob\}/);
    assert.match(detail, /<ConformationalMappingViewer[\s\S]*job=\{job\}/);
    assert.match(cm, /detailTab === 'landscape'[\s\S]*<FrustraWorkbench[\s\S]*job=\{frustraMpnnJob\}/);
    assert.doesNotMatch(cm, /detailTab === 'landscape'[\s\S]*Persisted exact-20 FrustraMPNN landscape/);
    assert.doesNotMatch(cm, /collectCompleteFrustraMpnnLandscape|createFrustraMpnnViewerMetrics|resolveFrustraMpnnResidueProfile|getCmLandscape/);
    assert.doesNotMatch(cm, /20 amino-acid substitution profile/);
    assert.match(resultSurface, /<StructureWorkbench/);
    assert.match(resultSurface, /<FrustraMpnnReviewExportPanel/);
    assert.match(resultSurface, /useSearchParams\(\)/);
    assert.match(resultSurface, /searchParams\.get\('invocation_id'\)/);
    assert.match(resultSurface, /listFrustraMpnnResults\(job\.id, 50, resultOffset/);
    assert.match(resultSurface, /Previous[\s\S]*Next/);
    assert.match(resultSurface, /metric_workbench_open: metricWorkbenchOpen/);
    assert.match(resultSurface, /landscape_offset: offset/);
    assert.match(resultSurface, /setMetricWorkbenchOpen\(review\.viewer_state\.metric_workbench_open !== false\)/);
    assert.doesNotMatch(resultSurface, /<MolstarViewer/);
    assert.match(reviewExport, /Save review/);
    assert.match(reviewExport, /createFrustraMpnnGovernedExport/);
    assert.match(reviewExport, /Create governed JSON/);
    assert.match(reviewExport, /Create governed CSV/);
    assert.match(reviewExport, /onRestore\(review\)/);
    assert.match(reviewExport, /listFrustraMpnnSavedReviewPage/);
    assert.match(reviewExport, /Next reviews/);
    assert.match(reviewExport, /viewer_state: viewerState/);
    assert.doesNotMatch(reviewExport, /URL\.createObjectURL|new Blob/);
});
