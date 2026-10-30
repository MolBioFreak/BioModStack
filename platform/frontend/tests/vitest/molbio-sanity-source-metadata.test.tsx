import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import './setup';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';

const rendered = vi.hoisted(() => ({ annotations: [] as Array<{ id: string; name: string }> }));
vi.mock('seqviz', () => ({ SeqViz: (props: any) => { rendered.annotations = props.annotations; return null; } }));
vi.mock('../../src/components/MolBioToolkit/ExportDropdown', () => ({ ExportDropdown: () => null }));
import { SequenceViewer } from '../../src/components/MolBioToolkit/SequenceViewer';
import { SequenceHeader } from '../../src/components/MolBioToolkit/SequenceHeader';
import { FeaturePanel } from '../../src/components/MolBioToolkit/panels/FeaturePanel';

it.each(['linear', 'circular', 'both'] as const)('keeps source metadata out of %s maps and annotation controls without changing stored features', async (viewMode) => {
    const sequenceData: SequenceData = {
        name: 'Source metadata fixture', sequence: 'ACGT'.repeat(30), circular: true, sequenceType: 'dna',
        features: [
            { id: 'metadata', name: 'Imported origin metadata', type: ' Source ', start: 0, end: 120, strand: 1,
                qualifiers: { organism: ['synthetic construct'] } },
            // Name and full length alone must never hide a real annotation.
            { id: 'real', name: 'source', type: 'misc_feature', start: 0, end: 120, strand: 1 },
        ],
    };
    const original = structuredClone(sequenceData);
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    try {
        await act(async () => root.render(<>
            <SequenceViewer sequenceData={sequenceData} viewMode={viewMode}
                visibility={{ features: true, primers: false, cutsites: false, translations: false, complement: false }} />
            <SequenceHeader sequenceData={sequenceData} />
            <FeaturePanel sequenceData={sequenceData} selection={null} onHighlight={vi.fn()}
                onAddFeature={vi.fn()} onRemoveFeature={vi.fn()} />
        </>));
        expect(rendered.annotations.map(annotation => annotation.id)).toEqual(['real']);
        expect(host.textContent).toContain('1 features');
        expect(host.textContent).not.toContain('Imported origin metadata');
        expect([...host.querySelectorAll('option')].some(option => option.value === 'source')).toBe(false);
        expect(sequenceData).toEqual(original);
    } finally {
        await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals();
    }
});
