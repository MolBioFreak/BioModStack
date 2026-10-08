import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildSelectionPrimer,
    buildPrimerTmRequest,
    canonicalizePrimerPlacement,
    createSelectionSnapshot,
    getPrimerHighlightRegions,
    getPrimerRenderableSites,
    getSelectionRanges,
    mapSeqVizSelectionToSource,
    normalizeStoredPrimerPlacement,
    prepareSelectionPrimer,
    resolvePersistentSelection,
    selectionForPlotDisplay,
    selectionFromPlotRange,
    sequenceForPlotDisplay,
} from '../src/components/MolBioToolkit/utils/selectionActions.js';

test('completed drag range survives later cursor-only emissions from mouse-up or right-click', () => {
    const completed = { start: 12, end: 42, clockwise: true, type: 'SEQ' };

    assert.deepEqual(
        resolvePersistentSelection(completed, { start: 42, end: 42, clockwise: true, type: 'SEQ' }, 100),
        completed,
    );
});

test('a new non-empty drag replaces the previously persistent range', () => {
    const previous = { start: 12, end: 42, clockwise: true, type: 'SEQ' };
    const replacement = { start: 60, end: 84, clockwise: true, type: 'SEQ' };

    assert.deepEqual(resolvePersistentSelection(previous, replacement, 100), replacement);
});

test('cursor selection remains available when there is no completed range yet', () => {
    assert.deepEqual(
        resolvePersistentSelection(null, { start: 17, end: 17, type: 'SEQ' }, 100),
        { start: 17, end: 17, type: 'SEQ' },
    );
});

test('right-click snapshot preserves coordinates and selected sequence independently of later state changes', () => {
    const mutableSelection = { start: 4, end: 12, clockwise: true, type: 'SEQ' };
    const snapshot = createSelectionSnapshot(mutableSelection, 'AAAACCCCGGGGTTTT', false);
    assert.ok(snapshot);

    mutableSelection.start = 0;
    mutableSelection.end = 0;

    assert.deepEqual(snapshot.selection, { start: 4, end: 12, clockwise: true, type: 'SEQ' });
    assert.deepEqual(snapshot.ranges, [{ start: 4, end: 12 }]);
    assert.equal(snapshot.sequence, 'CCCCGGGG');
    assert.equal(snapshot.length, 8);
    assert.equal(snapshot.coordinateLabel, '5 - 12');
    assert.equal(snapshot.coordinateKey, '5_12');
    assert.deepEqual(snapshot.placement, { start: 4, end: 12, wrapsOrigin: false });
});

test('circular origin-spanning selection preserves both segments and biological sequence order', () => {
    const selection = { start: 12, end: 4, clockwise: true, type: 'SEQ' };

    assert.deepEqual(getSelectionRanges(selection, 16, true), [
        { start: 12, end: 16 },
        { start: 0, end: 4 },
    ]);

    const snapshot = createSelectionSnapshot(selection, 'AAAACCCCGGGGTTTT', true);
    assert.ok(snapshot);
    assert.equal(snapshot.sequence, 'TTTTAAAA');
    assert.equal(snapshot.length, 8);
    assert.equal(snapshot.coordinateLabel, '13 - 16 + 1 - 4');
    assert.deepEqual(snapshot.placement, { start: 12, end: 4, wrapsOrigin: true });
});

test('SeqViz circular direction is canonicalized before snapshot construction', () => {
    const sequence = 'AAAACCCCGGGGTTTT';
    const clockwise = mapSeqVizSelectionToSource(
        { start: 12, end: 4, clockwise: true, type: 'SEQ' },
        sequence.length,
        true,
        false,
        0,
    );
    const counterClockwise = mapSeqVizSelectionToSource(
        { start: 4, end: 12, clockwise: false, type: 'SEQ' },
        sequence.length,
        true,
        false,
        0,
    );

    assert.deepEqual(clockwise, counterClockwise);
    assert.deepEqual(clockwise, { start: 12, end: 4, clockwise: true, type: 'SEQ' });
    assert.equal(createSelectionSnapshot(clockwise, sequence, true)?.sequence, 'TTTTAAAA');
});

test('SeqViz circular selection maps back from reverse display without losing origin wrap', () => {
    assert.deepEqual(
        mapSeqVizSelectionToSource(
            { start: 15, end: 3, clockwise: true, type: 'SEQ' },
            20,
            true,
            true,
            0,
        ),
        { start: 17, end: 5, clockwise: true, type: 'SEQ' },
    );
});

test('right-button SeqViz emissions cannot replace a committed selection', () => {
    assert.equal(
        mapSeqVizSelectionToSource(
            { start: 60, end: 84, clockwise: true, type: 'ANNOTATION' },
            100,
            true,
            false,
            2,
        ),
        null,
    );
});

test('empty or out-of-bounds point selections cannot seed creation dialogs', () => {
    assert.equal(createSelectionSnapshot({ start: 5, end: 5 }, 'ACGTACGT', false), null);
    assert.equal(createSelectionSnapshot({ start: 99, end: 99 }, 'ACGTACGT', false), null);
});

test('primer preparation preserves RNA molecule identity when the selected bases contain no U', () => {
    const snapshot = createSelectionSnapshot({ start: 0, end: 4 }, 'CCGG', false);
    assert.ok(snapshot);

    const prepared = prepareSelectionPrimer(snapshot, 'forward', 'rna');
    assert.equal(prepared.sequenceType, 'rna');
    assert.equal(prepareSelectionPrimer(snapshot, 'reverse', 'rna').sequenceType, 'rna');
    assert.deepEqual(buildPrimerTmRequest(prepared, { sodium_mM: 50 }), {
        primers: [{ sequence: 'CCGG', sequence_type: 'rna' }],
        settings: { sodium_mM: 50 },
    });
});

test('save/reload normalization preserves circular top-level placement and split sites', () => {
    assert.deepEqual(normalizeStoredPrimerPlacement({
        start: 12,
        end: 4,
        strand: -1,
        sites: [
            { start: 12, end: 16, strand: -1, tm: 61.5 },
            { start: 0, end: 4, strand: -1, tm: 61.5 },
        ],
    }), {
        start: 12,
        end: 4,
        strand: -1,
        sites: [
            { start: 12, end: 16, strand: -1, tm: 61.5 },
            { start: 0, end: 4, strand: -1, tm: 61.5 },
        ],
    });
});

test('compatibility payload infers origin-wrapping placement from all ordered split sites', () => {
    assert.deepEqual(normalizeStoredPrimerPlacement({
        sites: [
            { start: 12, end: 16, strand: 1, tm: 61.5 },
            { start: 0, end: 4, strand: 1, tm: 61.5 },
        ],
    }), {
        start: 12,
        end: 4,
        strand: 1,
        sites: [
            { start: 12, end: 16, strand: 1, tm: 61.5 },
            { start: 0, end: 4, strand: 1, tm: 61.5 },
        ],
    });
});

test('origin-wrapping forward primer exposes every selected range as a renderable binding site', () => {
    const snapshot = createSelectionSnapshot(
        { start: 12, end: 4, clockwise: true },
        'AAAACCCCGGGGTTTT',
        true,
    );
    assert.ok(snapshot);
    const prepared = prepareSelectionPrimer(snapshot, 'forward', 'dna');

    const primer = buildSelectionPrimer({
        id: 'primer-forward-wrap',
        name: 'Forward wrap',
        snapshot,
        prepared,
        tm: 61.5,
    });

    assert.ok('sites' in primer);
    assert.deepEqual(primer.sites, [
        { start: 12, end: 16, strand: 1, tm: 61.5 },
        { start: 0, end: 4, strand: 1, tm: 61.5 },
    ]);
    assert.deepEqual(getPrimerRenderableSites(primer), primer.sites);
    assert.deepEqual(getPrimerHighlightRegions(primer, '#22c55e', primer.name), [
        { start: 12, end: 16, color: '#22c55e', label: 'Forward wrap' },
        { start: 0, end: 4, color: '#22c55e', label: 'Forward wrap' },
    ]);
});

test('origin-wrapping reverse primer exposes every selected range with reverse strand semantics', () => {
    const snapshot = createSelectionSnapshot(
        { start: 12, end: 4, clockwise: true },
        'AAAACCCCGGGGTTTT',
        true,
    );
    assert.ok(snapshot);
    const prepared = prepareSelectionPrimer(snapshot, 'reverse', 'dna');

    const primer = buildSelectionPrimer({
        id: 'primer-reverse-wrap',
        name: 'Reverse wrap',
        snapshot,
        prepared,
        tm: 62,
    });

    assert.ok('sites' in primer);
    assert.deepEqual(primer.sites, [
        { start: 12, end: 16, strand: -1, tm: 62 },
        { start: 0, end: 4, strand: -1, tm: 62 },
    ]);
    assert.deepEqual(getPrimerRenderableSites(primer), primer.sites);
});

test('reverse-display analysis track uses the reverse-complemented molecule sequence', () => {
    assert.equal(sequenceForPlotDisplay('AACCGT', 'dna', true), 'ACGGTT');
    assert.equal(sequenceForPlotDisplay('AACCGU', 'rna', true), 'ACGGUU');
});

test('reverse-display analysis track transforms persistent source selections into display coordinates', () => {
    assert.deepEqual(
        selectionForPlotDisplay(
            { start: 10, end: 20, clockwise: true, type: 'SEQ' },
            100,
            false,
            true,
        ),
        { start: 80, end: 90, clockwise: true, type: 'SEQ' },
    );
    assert.deepEqual(
        selectionForPlotDisplay(
            { start: 80, end: 5, clockwise: true, type: 'SEQ' },
            100,
            true,
            true,
        ),
        { start: 95, end: 20, clockwise: true, type: 'SEQ' },
    );
});

test('reverse-display Plotly drag maps the displayed interval back to source coordinates', () => {
    assert.deepEqual(
        selectionFromPlotRange([10, 20], 100, true),
        { start: 80, end: 90, clockwise: true, type: 'TRACK' },
    );
});

test('circular library placement crossing the origin is canonicalized into bounded ordered sites', () => {
    const raw = { start: 14, end: 20, strand: 1 as const };
    assert.deepEqual(canonicalizePrimerPlacement(raw, 16, true), {
        start: 14,
        end: 4,
        strand: 1,
        sites: [
            { start: 14, end: 16, strand: 1 },
            { start: 0, end: 4, strand: 1 },
        ],
    });
    assert.deepEqual(getPrimerRenderableSites(raw, 16, true), [
        { start: 14, end: 16, strand: 1 },
        { start: 0, end: 4, strand: 1 },
    ]);
    assert.deepEqual(getPrimerHighlightRegions(raw, '#22c55e', 'wrap', 16, true), [
        { start: 14, end: 16, color: '#22c55e', label: 'wrap' },
        { start: 0, end: 4, color: '#22c55e', label: 'wrap' },
    ]);
});
