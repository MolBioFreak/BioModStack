import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { DeNovoModalityNotice } from '../src/components/DeNovoModalityNotice.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

test('mounted modality notice shows route limits and refuses an unknown saved selector', async () => {
    let tree: ReturnType<typeof create> | undefined;
    await act(async () => { tree = create(<DeNovoModalityNotice generator={null} />); });
    const paragraphs = tree!.root.findAllByType('p');
    assert.match(paragraphs[0].children.join(''), /BoltzGen route is VHH-only/);
    assert.match(paragraphs[0].children.join(''), /PPIFlow needs a seeded antibody-target complex/);
    assert.equal(tree!.root.findAllByProps({ role: 'alert' }).length, 1);
    await act(async () => { tree!.update(<DeNovoModalityNotice generator="ppiflow" />); });
    assert.equal(tree!.root.findAllByProps({ role: 'alert' }).length, 0);
    await act(async () => { tree!.unmount(); });
});
