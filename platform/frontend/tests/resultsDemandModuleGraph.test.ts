import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import { build } from 'vite';

test('Results and empty native Results have no static heavy-engine path in the actual Rollup graph', async () => {
    const entry = path.resolve('src/components/ResultsViewer.tsx');
    const native = path.resolve('src/components/NativeBinderGenerationResults.tsx');
    const graph = new Map<string, { imports: readonly string[]; dynamicImports: readonly string[] }>();
    await build({
        configFile: false, logLevel: 'error',
        build: { write: false, minify: false, rollupOptions: {
            input: entry,
            // Build all local owners; keep installed renderer packages external so
            // this focused graph test is fast and never modifies dependencies.
            external: id => !id.startsWith('.') && !path.isAbsolute(id),
        } },
        plugins: [{ name: 'results-demand-graph', generateBundle() {
            for (const id of this.getModuleIds()) {
                const info = this.getModuleInfo(id)!;
                graph.set(id, { imports: info.importedIds, dynamicImports: info.dynamicallyImportedIds });
            }
        } }],
    });
    function eager(root: string) {
        const seen = new Set<string>();
        function visit(id: string) { if (seen.has(id)) return; seen.add(id); graph.get(id)?.imports.forEach(visit); }
        visit(root); return [...seen];
    }
    for (const owner of [entry, native]) {
        const heavy = eager(owner).filter(id => /react-plotly|\/molstar\/|StructureWorkbench|StructureViewerHost|MolstarViewerImpl|CohortAnalytics/.test(id));
        assert.deepEqual(heavy, [], `${owner} eagerly imports heavy leaves`);
        console.log(`${path.basename(owner)}: ${eager(owner).length} eager modules, zero heavy leaves`);
    }
    assert.ok(graph.get(entry)?.dynamicImports.some(id => id.endsWith('/AnalyticsDashboard.tsx')));
    assert.ok(graph.get(entry)?.dynamicImports.some(id => id.endsWith('/StructureViewerPane.tsx')));
    assert.ok(graph.get(native)?.dynamicImports.some(id => id.endsWith('/StructureWorkbench.tsx')));
    assert.ok(graph.get(native)?.dynamicImports.some(id => id.endsWith('/CohortAnalytics.tsx')));
});
