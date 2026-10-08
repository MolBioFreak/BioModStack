import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

const root = resolve(import.meta.dirname, '..');
function imports(path: string) {
    const source = ts.createSourceFile(path, readFileSync(resolve(root, path), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const eager: string[] = [], lazy: string[] = [];
    function visit(node: ts.Node) {
        if (ts.isImportDeclaration(node) && !node.importClause?.isTypeOnly && ts.isStringLiteral(node.moduleSpecifier)) eager.push(node.moduleSpecifier.text);
        if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword && ts.isStringLiteral(node.arguments[0])) lazy.push(node.arguments[0].text);
        ts.forEachChild(node, visit);
    }
    visit(source); return { eager, lazy };
}
test('optional toolkit chart and both parser actions are dynamic edges, not static imports', () => {
    const toolkit = imports('src/components/MolBioToolkit/MolBioToolkitV2.tsx');
    const exports = imports('src/components/MolBioToolkit/ExportDropdown.tsx');
    assert(!toolkit.eager.includes('./GCContentTrack')); assert(toolkit.lazy.includes('./GCContentTrack'));
    assert(!toolkit.eager.includes('@teselagen/bio-parsers')); assert.equal(toolkit.lazy.filter(p => p === '@teselagen/bio-parsers').length, 2);
    assert(!exports.eager.includes('@teselagen/bio-parsers')); assert(exports.lazy.includes('@teselagen/bio-parsers'));
    assert(toolkit.eager.includes('./SequenceViewer'), 'keep main scientific viewer');
    assert(imports('src/components/MolBioToolkit/GCContentTrack.tsx').eager.includes('react-plotly.js'), 'chart capability still exists in the deferred module');
});
test('cold index has no external Google font requirement and keeps its system fallback', () => {
    assert(!/fonts\.(?:googleapis|gstatic)\.com/.test(readFileSync(resolve(root, 'index.html'), 'utf8')));
    assert.match(readFileSync(resolve(root, 'src/index.css'), 'utf8'), /-apple-system, BlinkMacSystemFont/);
});
test('actual production graph separates Plotly and parser from entry and basic toolkit closure', { skip: !process.env.BMS_TRAFFIC_BUILD_MANIFEST }, () => {
    const manifest = JSON.parse(readFileSync(process.env.BMS_TRAFFIC_BUILD_MANIFEST!, 'utf8')) as Record<string, { imports?: string[]; dynamicImports?: string[]; file: string }>;
    function closure(key: string) {
        const seen = new Set<string>(), pending = [key];
        while (pending.length) {
            const next = pending.pop()!; if (seen.has(next)) continue;
            assert(manifest[next], `missing manifest dependency ${next}`); seen.add(next); pending.push(...manifest[next].imports ?? []);
        }
        return seen;
    }
    const graphKeys = Object.keys(manifest);
    const toolkit = graphKeys.find(k => manifest[k].dynamicImports?.includes('src/components/MolBioToolkit/GCContentTrack.tsx'))!;
    assert(toolkit, 'actual toolkit chunk exists');
    const parser = graphKeys.find(k => /bio-parsers\/src\/index/.test(k))!; assert(parser);
    const plotly = graphKeys.filter(k => /vendor-plotly/.test(k)); assert(plotly.length > 0);
    for (const entry of ['index.html', toolkit]) {
        const eager = closure(entry); assert(!eager.has(parser)); assert(plotly.every(k => !eager.has(k)));
    }
    const gc = closure('src/components/MolBioToolkit/GCContentTrack.tsx'); assert(plotly.some(k => gc.has(k)));
    assert(manifest[toolkit].dynamicImports?.includes(parser));
});
