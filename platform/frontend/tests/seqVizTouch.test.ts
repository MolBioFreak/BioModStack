import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    buildSyntheticMouseEventInit,
    getSeqVizTouchRotationWheelDelta,
    resolveSeqVizTouchGestureMode,
    shouldEnableSeqVizTouchBridge,
} from '../src/components/MolBioToolkit/utils/seqVizTouch.js';

const SEQUENCE_VIEWER_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/SequenceViewer.tsx');

test('touch bridge enables only on touch-capable or coarse-pointer devices', () => {
    assert.equal(shouldEnableSeqVizTouchBridge({ maxTouchPoints: 0, coarsePointer: false }), false);
    assert.equal(shouldEnableSeqVizTouchBridge({ maxTouchPoints: 1, coarsePointer: false }), true);
    assert.equal(shouldEnableSeqVizTouchBridge({ maxTouchPoints: 0, coarsePointer: true }), true);
});

test('single-touch gestures synthesize mouse events while multi-touch gestures stay native', () => {
    assert.equal(resolveSeqVizTouchGestureMode(0), 'native');
    assert.equal(resolveSeqVizTouchGestureMode(1), 'synthetic-mouse');
    assert.equal(resolveSeqVizTouchGestureMode(2), 'native');
});

test('synthetic mouse events preserve coordinates and primary-button semantics', () => {
    const init = buildSyntheticMouseEventInit({ clientX: 42, clientY: 108 });

    assert.equal(init.clientX, 42);
    assert.equal(init.clientY, 108);
    assert.equal(init.button, 0);
    assert.equal(init.buttons, 1);
    assert.equal(init.bubbles, true);
    assert.equal(init.cancelable, true);
    assert.equal(init.composed, true);
});

test('touch rotation buttons emit predictable wheel deltas', () => {
    assert.equal(getSeqVizTouchRotationWheelDelta('left'), -240);
    assert.equal(getSeqVizTouchRotationWheelDelta('right'), 240);
    assert.equal(getSeqVizTouchRotationWheelDelta('left', 120), -120);
});

test('sequence viewer source wires the touch bridge and explicit plasmid rotation controls', () => {
    const source = readFileSync(SEQUENCE_VIEWER_PATH, 'utf8');

    assert.match(source, /installSeqVizTouchBridge/);
    assert.match(source, /data-seqviz-touch-control/);
    assert.match(source, /la-vz-viewer-circular/);
    assert.match(source, /Rotate plasmid left/i);
    assert.match(source, /Rotate plasmid right/i);
});

test('both plasmid rotation controls expose 48 px touch targets', () => {
    const source = readFileSync(SEQUENCE_VIEWER_PATH, 'utf8');
    assert.equal(source.match(/min-h-12 min-w-12/gu)?.length, 2);
});
