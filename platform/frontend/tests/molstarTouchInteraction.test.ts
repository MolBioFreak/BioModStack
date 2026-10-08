import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    MOLSTAR_TOUCH_INTERACTION_SELECTOR,
    resolveMolstarTouchAction,
    shouldEnableMolstarTouchInteractionOverride,
} from '../src/components/molstarTouchInteraction.js';

const MOLSTAR_VIEWER_PATH = resolve(process.cwd(), 'src/components/MolstarViewer.tsx');

test('touch-capable devices keep Mol* pinch gestures inside the viewer instead of delegating them to browser zoom', () => {
    assert.equal(shouldEnableMolstarTouchInteractionOverride({ maxTouchPoints: 2, coarsePointer: false }), true);
    assert.equal(shouldEnableMolstarTouchInteractionOverride({ maxTouchPoints: 0, coarsePointer: true }), true);
    assert.equal(resolveMolstarTouchAction({ maxTouchPoints: 2, coarsePointer: false }), 'none');
    assert.equal(resolveMolstarTouchAction({ maxTouchPoints: 0, coarsePointer: true }), 'none');
});

test('desktop Mol* viewers keep default browser touch-action semantics', () => {
    assert.equal(shouldEnableMolstarTouchInteractionOverride({ maxTouchPoints: 0, coarsePointer: false }), false);
    assert.equal(resolveMolstarTouchAction({ maxTouchPoints: 0, coarsePointer: false }), 'auto');
    assert.match(MOLSTAR_TOUCH_INTERACTION_SELECTOR, /canvas/);
    assert.match(MOLSTAR_TOUCH_INTERACTION_SELECTOR, /msp-plugin/);
});

test('MolstarViewer source wires the mobile touch-action override into host and shadow interaction surfaces', () => {
    const source = readFileSync(MOLSTAR_VIEWER_PATH, 'utf8');

    assert.match(source, /resolveMolstarTouchAction/);
    assert.match(source, /MOLSTAR_TOUCH_INTERACTION_SELECTOR/);
    assert.match(source, /host\.style\.touchAction = interactionTouchAction/);
    assert.match(source, /querySelectorAll<HTMLElement>\(MOLSTAR_TOUCH_INTERACTION_SELECTOR\)/);
});
