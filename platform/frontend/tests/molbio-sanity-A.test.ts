import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHistoryState, historyReducer, reconcileSavedHistory } from '../src/components/MolBioToolkit/hooks/useSequenceHistory';
import { EMPTY_SEQUENCE } from '../src/components/MolBioToolkit/sequenceViewerConstants';
import { findOpenReadingFrames } from '../src/components/MolBioToolkit/utils/orfs';
import { resolveAnnotationSequenceAlignment } from '../src/components/MolBioToolkit/utils/annotationTransfer';
import { featureSegments, featureOverlapLength } from '../src/components/MolBioToolkit/utils/features';
import { countPositionsInRange } from '../src/components/MolBioToolkit/utils/gcTrackPolicy';

test('functional history sees reducer-current state; batch additions/removals are one undo step', () => {
    const base = createHistoryState(EMPTY_SEQUENCE);
    const first = historyReducer(base, { type: 'SET', payload: current => ({ ...current, name: 'first' }) });
    const second = historyReducer(first, { type: 'SET', payload: current => ({ ...current, description: current.name }) });
    assert.equal(second.present.description, 'first');
    const pair = ['forward', 'reverse'].map(id => ({ id, name: id, sequence: 'ACGT', start: 0, end: 4, strand: 1 as const }));
    const added = historyReducer(base, { type: 'SET', payload: current => ({ ...current, primers: [...(current.primers || []), ...pair] }) });
    assert.equal(added.present.primers?.length, 2);
    assert.equal(added.past.length, 1);
    assert.equal(historyReducer(added, { type: 'UNDO' }).present, EMPTY_SEQUENCE);
    assert.equal(historyReducer(added, { type: 'SET', payload: current => current }), added);
});

test('late saved history preserves active or inactive edits and undo generation', () => {
    const submitted = createHistoryState({ ...EMPTY_SEQUENCE, sequence: 'ACGT', version: 1 });
    const edited = historyReducer(submitted, { type: 'SET', payload: current => ({ ...current, name: 'post-save edit' }) });
    const saved = { ...submitted.present, version: 2 };
    for (const current of [edited, historyReducer(edited, { type: 'UNDO' })]) {
        const reconciled = reconcileSavedHistory(submitted, current, saved);
        assert.equal(reconciled.present.name, current.present.name);
        assert.equal(reconciled.present.version, 2);
        assert.equal(reconciled.past, current.past);
        assert.equal(reconciled.future, current.future);
        assert.equal(reconciled.journal, current.journal);
    }
    assert.equal(reconcileSavedHistory(submitted, submitted, saved).present, saved);
});

test('ORF sweep preserves first stop and avoids repeated no-stop suffix scans', () => {
    const nativeSlice = String.prototype.slice;
    let slices = 0;
    String.prototype.slice = function (...args: Parameters<typeof nativeSlice>) { slices++; return nativeSlice.apply(this, args); };
    try {
        const sequence = 'ATG'.repeat(2000);
        assert.deepEqual(findOpenReadingFrames(sequence, 9, false), []);
        assert.ok(slices < sequence.length * 5, `slice count ${slices}`);
    } finally { String.prototype.slice = nativeSlice; }
    const result = findOpenReadingFrames('ATGAAATAAATGAAAAAATAG', 9, false);
    assert.ok(result.some(orf => orf.strand === 1 && orf.start === 0 && orf.end === 9));
});

test('ambiguous periodic annotation rotation stops after two matching searches', () => {
    const native = String.prototype.indexOf;
    let calls = 0;
    String.prototype.indexOf = function (...args: Parameters<typeof native>) { calls++; return native.apply(this, args); };
    try {
        assert.throws(() => resolveAnnotationSequenceAlignment('AC'.repeat(5000), 'CA'.repeat(5000), true), /ambiguous/);
        assert.equal(calls, 2);
    } finally { String.prototype.indexOf = native; }
    assert.equal(resolveAnnotationSequenceAlignment('ACAC', 'ACAC', true).mode, 'exact');
});

test('restriction density bounds match full scan with logarithmic element reads', () => {
    const sorted = Array.from({ length: 10000 }, (_, index) => Math.floor(index / 2));
    let reads = 0;
    const positions = new Proxy(sorted, { get(target, key, receiver) { if (/^\d+$/.test(String(key))) reads++; return Reflect.get(target, key, receiver); } });
    for (const includeEnd of [false, true]) {
        assert.equal(countPositionsInRange(positions, 4, 99, includeEnd), sorted.filter(p => p >= 4 && (p < 99 || includeEnd && p === 99)).length);
    }
    assert.ok(reads < 150, `element reads ${reads}`);
});

test('feature projections retain object isolation and ordered joined geometry', () => {
    const f = { id: 'f', name: 'f', type: 'misc_feature', start: 1, end: 10, strand: 1 as const, segments: [{ start: 8, end: 10 }, { start: 1, end: 3 }] };
    const result = featureSegments(f);
    result[0].start = 9;
    assert.equal(f.segments[0].start, 8);
    assert.equal(featureOverlapLength(f, f), 4);
    assert.deepEqual(f.segments, [{ start: 8, end: 10 }, { start: 1, end: 3 }]);
});

test('shell gates hidden work and wires gesture and completion authorities', () => {
    const source = readFileSync(new URL('../src/components/MolBioToolkit/MolBioToolkitV2.tsx', import.meta.url), 'utf8');
    assert.match(source, /visibility\.translations && sequenceData\.sequence/);
    assert.match(source, /restrictionConsumerVisible = visibility\.cutsites/);
    assert.match(source, /!isToolPanelCollapsed && activePanel === 'digest'/);
    assert.match(source, /if \(!demoRequested\) return/);
    assert.match(source, /onAddPrimers=\{handleAddPrimers\}/);
    assert.match(source, /onRemoveFeatures=\{handleRemoveFeatures\}/);
    assert.match(source, /const unchanged = latestHistory === targetHistory/);
    assert.match(source, /if \(stillActive\)/);
    assert.match(source, /if \(!ownsCompletion\(\)\)/);
});
