import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import type { StructureMaterialization } from '../../src/lib/api';
const materialize = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/api', () => ({ materializeExactStructure: materialize, materializeStructureTarget: vi.fn(), buildFileDownloadUrl: (path: string) => `/download?path=${path}` }));
vi.mock('../../src/components/TargetAntigenSelector', () => ({ TargetAntigenSelector: () => null }));
vi.mock('../../src/components/DeNovoNativeDocumentPicker', () => ({ DeNovoNativeDocumentPicker: () => null }));
vi.mock('../../src/components/FileBrowser', () => ({ FileBrowser: () => null }));
// Inert WebGL leaf: this suite checks transport and presentation ownership, not rendering biology.
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: unknown) => <output data-viewer>{JSON.stringify(props)}</output> }));
import { NativeSourceInput } from '../../src/components/DeNovoNativeSources';
let root: Root | undefined;
const receipt = (path: string) => ({ path, format: 'pdb', native_format: 'pdb', model_number: 2, model_numbers: [1, 2], author_residues: [], source_positions: [] }) as unknown as StructureMaterialization;
const acquired = vi.fn();
const inspected = vi.fn();
async function render(value: string, options: Partial<React.ComponentProps<typeof NativeSourceInput>> = {}) {
    if (!root) { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); }
    await act(async () => { root!.render(<NativeSourceInput integrated label="Target" value={value} bindings={{ a: { path: '/a.pdb', model_number: 2 }, b: { path: '/b.pdb', model_number: 2 } }} onChange={() => {}} onAcquire={acquired} onInspection={inspected} {...options} />); });
}
const view = () => JSON.parse(document.querySelector('[data-viewer]')!.textContent!);
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); vi.clearAllMocks(); vi.restoreAllMocks(); });
it('shows retained structure and sequence immediately without rebinding the source', async () => {
    materialize.mockImplementation(async ({ path }) => ({ ...receipt(path), source_structure: { path: '/canonical.pdb' } }));
    await render('a');
    expect(materialize).toHaveBeenCalledWith({ path: '/a.pdb', model_number: 2, output_format: 'native' });
    expect(acquired).not.toHaveBeenCalled();
    expect(inspected).toHaveBeenCalledWith('a', expect.anything(), { path: '/a.pdb', model_number: 2 });
    expect(view()).toMatchObject({ structureDocumentId: '/a.pdb', showSequenceTrack: false, height: 480, hideControls: true, showMetricWorkbench: false, showM6Workbench: false, showMeasurements: false, showComplexWorkbench: false });
    expect(document.querySelector('[data-viewer]')!.closest('details')).toBeNull();
    expect(document.querySelector('summary')!.textContent).toBe('Source details');
});
it('selecting another existing binding replaces the visible document without acquisition callbacks', async () => {
    materialize.mockImplementation(async ({ path }) => receipt(path));
    await render('a'); await render('b');
    expect(view().structureDocumentId).toBe('/b.pdb');
    expect(materialize).toHaveBeenCalledTimes(2); expect(acquired).not.toHaveBeenCalled();
});
it('late inspection of a previous binding cannot replace the current viewer', async () => {
    let finish!: (value: StructureMaterialization) => void;
    materialize.mockImplementation(({ path }) => path === '/a.pdb' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(receipt(path)));
    await render('a'); await render('b');
    await act(async () => finish(receipt('/a.pdb')));
    expect(view().structureDocumentId).toBe('/b.pdb'); expect(acquired).not.toHaveBeenCalled();
});
it('uses an existing inspection and leaves non-integrated callers opt-in', async () => {
    await render('a', { inspection: receipt('/a.pdb') });
    expect(view().structureDocumentId).toBe('/a.pdb'); expect(materialize).not.toHaveBeenCalled();
    await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren();
    await render('a', { integrated: false });
    expect(document.querySelector('[data-viewer]')).toBeNull(); expect(materialize).not.toHaveBeenCalled();
});
it('loads native-writer saved layers automatically without applying them as selection or rebinding', async () => {
    const cif = readFileSync('tests/fixtures/rfd4-saved-conditions.cif', 'utf8');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, text: async () => cif } as Response);
    const source = { ...receipt('/saved.cif'), format: 'cif', native_format: 'cif', model_number: 1, model_numbers: [1] } as StructureMaterialization;
    await render('a', { inspection: source });
    const layer = view().metricLayers.find((row: any) => row.descriptor.label === 'Saved C_CRDGRP');
    expect(layer.values[0]).toMatchObject({ value: 7, identity: { documentId: '/saved.cif', modelNumber: 1 } });
    expect(view().residueSelections).toEqual([]); expect(acquired).not.toHaveBeenCalled();
});
it('restores expert tools explicitly without changing source bindings', async () => {
    await render('a', { inspection: receipt('/a.pdb') });
    const toggle = Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Show expert viewer tools')!;
    await act(async () => toggle.click());
    expect(view()).toMatchObject({ hideControls: false, showMetricWorkbench: true, showM6Workbench: true, showMeasurements: true, showSequenceTrack: true });
    await act(async () => toggle.click());
    expect(view()).toMatchObject({ hideControls: true, showMetricWorkbench: false, showM6Workbench: false });
    expect(materialize).not.toHaveBeenCalled(); expect(acquired).not.toHaveBeenCalled();
});
it('selects exact receipt positions and shift ranges, synchronizing controlled highlights without reloading', async () => {
    const source = { ...receipt('/a.pdb'), source_positions: [
        { model_number: 1, label_asym_id: 'B', label_seq_id: 1, auth_asym_id: 'X', auth_seq_id: 12, label_comp_id: 'ALA' },
        { model_number: 2, label_asym_id: 'B', label_seq_id: 1, auth_asym_id: 'X', auth_seq_id: 12, label_comp_id: 'MSE', label_entity_id: '4' },
        { model_number: 2, label_asym_id: 'B', label_seq_id: 2, auth_asym_id: 'X', auth_seq_id: 12, pdbx_PDB_ins_code: 'A', label_comp_id: 'GLY', label_entity_id: '4' },
        { model_number: 2, label_asym_id: 'B', label_seq_id: 3, auth_asym_id: 'X', auth_seq_id: 90, label_entity_id: '4' },
    ] } as StructureMaterialization;
    const onSelection = vi.fn();
    await render('a', { inspection: source, onSelection });
    const viewer = document.querySelector('[data-viewer]');
    const buttons = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[aria-label="Source sequence for a"] button'));
    expect(buttons().map(button => button.getAttribute('aria-label'))).toEqual(['MSE · author X:12 · MODEL 2', 'GLY · author X:12A · MODEL 2', 'Unknown · author X:90 · MODEL 2']);
    await act(async () => buttons()[0].click());
    expect(onSelection).toHaveBeenLastCalledWith('a', source, expect.objectContaining({ metricId: 'native-source-sequence-selection', origin: 'sequence', identities: [expect.objectContaining({ documentId: '/a.pdb', modelNumber: 2, labelAsymId: 'B', labelSeqId: 1, authAsymId: 'X', authSeqId: 12, componentId: 'MSE', entityId: '4' })] }));
    await act(async () => buttons()[2].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
    const identities = onSelection.mock.lastCall![2].identities;
    expect(identities).toHaveLength(3); expect(identities[1].insertionCode).toBe('A'); expect(identities[2].componentId).toBeUndefined();
    await render('a', { inspection: source, onSelection, selectedIdentities: identities });
    expect(buttons().map(button => button.getAttribute('aria-pressed'))).toEqual(['true', 'true', 'true']);
    expect(view().residueSelections).toEqual(identities);
    expect(document.querySelector('[data-viewer]')).toBe(viewer);
    await render('a', { inspection: source, onSelection, selectedIdentities: [{ ...identities[1], labelAtomId: 'CA' }] });
    expect(buttons().map(button => button.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
    await render('a', { inspection: source, onSelection, selectedIdentities: [{ ...identities[1], documentId: '/wrong.pdb' }] });
    expect(buttons().every(button => button.getAttribute('aria-pressed') === 'false')).toBe(true);
    expect(materialize).not.toHaveBeenCalled(); expect(acquired).not.toHaveBeenCalled();
});
