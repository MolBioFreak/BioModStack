import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { expect, it } from 'vitest';
import { BoltzGenRankControls } from '../../src/components/BoltzGenRankControls';

it('edits native weights and disables criteria through typed controls without losing zero/native meaning', async () => {
    let value = '';
    let mounted: ReactTestRenderer;
    await act(async () => { mounted = create(<BoltzGenRankControls value={value} onChange={v => { value = v; }} />); });
    const ptm = mounted!.root.findByProps({ 'aria-label': 'design_ptm weight' });
    await act(async () => ptm.props.onChange({ target: { value: '2' } }));
    expect(value).toBe('design_ptm=2 affinity_probability=1 filter_rmsd=1');
    await act(async () => mounted!.update(<BoltzGenRankControls value={value} onChange={v => { value = v; }} />));
    await act(async () => mounted!.root.findByProps({ 'aria-label': 'filter_rmsd enabled' }).props.onChange({ target: { checked: false } }));
    expect(value).toBe('design_ptm=2 affinity_probability=1 filter_rmsd=none');
    await act(async () => mounted!.unmount());
});
