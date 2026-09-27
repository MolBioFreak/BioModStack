import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { DeNovoModalityNotice } from '../src/components/DeNovoModalityNotice.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

test('mounted modality notice scopes legacy limits and preserves unknown-selector warning', async () => {
    let tree: ReturnType<typeof create> | undefined;
    await act(async () => { tree = create(<DeNovoModalityNotice generator={null} />); });
    const paragraphs = tree!.root.findAllByType('p');
    const copy = paragraphs[0].children.join('');
    for (const engine of ['BindCraft2', 'BoltzGen', 'RFantibody', 'PPIFlow']) assert.ok(copy.includes(engine));
    assert.match(copy, /Only the legacy BoltzGen wrapper here is VHH-only/);
    assert.match(copy, /only the retained PPIFlow partial-flow wrapper requires a seeded antibody-target complex/);
    assert.match(copy, /legacy limits do not apply to the native generation modes/);
    assert.doesNotMatch(copy, /RFD3/);
    assert.equal(tree!.root.findAllByProps({ role: 'alert' }).length, 1);
    await act(async () => { tree!.update(<DeNovoModalityNotice generator="ppiflow" />); });
    assert.equal(tree!.root.findAllByProps({ role: 'alert' }).length, 0);
    await act(async () => { tree!.unmount(); });
});
