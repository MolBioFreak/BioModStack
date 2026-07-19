import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const readSource = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8');

test('Data Viewer landing shares a centered shell with the job selector', () => {
    const viewer = readSource('src', 'components', 'ResultsViewer.tsx');
    const landing = readSource('src', 'components', 'DataViewerLanding.tsx');

    assert.match(viewer, /const viewerShellClassName = showDataHubLanding\s*\? 'mx-auto w-full max-w-\[1180px\]'/);
    assert.match(viewer, /className=\{`relative z-10[^`]*\$\{viewerShellClassName\}`\}/);
    assert.ok(landing.includes('data-testid="data-viewer-landing"'));
    assert.ok(landing.includes('className="mb-8 w-full"'));
    assert.ok(landing.includes('items-start'));
    assert.ok(!landing.includes('xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]'));
});

test('Data Viewer landing preserves preview-first ingestion and post-import cache handoff', () => {
    const landing = readSource('src', 'components', 'DataViewerLanding.tsx');

    for (const contract of [
        'inputs/data_imports/${Date.now()}_${slugify(selectedFile.name)}',
        'await uploadFile(uploadTarget, selectedFile)',
        'await importProteinBaseBundle({',
        'bundle_path: uploadResponse.data.path',
        "queryClient.invalidateQueries({ queryKey: ['jobs'] })",
        "queryClient.invalidateQueries({ queryKey: ['job', job.id] })",
        'onImportComplete(job)',
    ]) {
        assert.ok(landing.includes(contract), `missing ingestion contract: ${contract}`);
    }

    assert.ok(landing.includes("disabled={importMutation.isPending || !preview?.importable || !selectedFile}"));
    assert.ok(landing.includes('Preview before import'));
    assert.ok(landing.includes('Existing job pipeline'));
});
