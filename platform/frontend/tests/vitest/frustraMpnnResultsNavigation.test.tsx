import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { CANONICAL_AMINO_ACIDS } from '../../src/components/conformationalMapping/conformationalMappingSemantics';
import Viewer from '../../src/components/FrustraMpnnResultsViewer';

const state = vi.hoisted(() => ({ restore: null as any, reviewProps: null as any, workbench: null as any, calls: [] as number[], fills: 0 }));
vi.mock('../../src/components/FrustraMpnnPlotlyAnalytics', () => ({ default: () => <div data-testid="analytics" /> }));
vi.mock('../../src/components/FrustraMpnnCandidateHandoffPanel', () => ({ default: () => <button>Upload external candidates</button> }));
vi.mock('../../src/components/FrustraMpnnResultAuthoritySurface', () => ({ FrustraMpnnResultAuthoritySurface: () => null, FrustraMpnnStatisticsAnalysisPanel: () => null }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnSettingsPanel', () => ({ FrustraMpnnSettingsPanel: () => <div data-testid="editable-settings" /> }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnStructureSelector', () => ({ FrustraMpnnStructureSelector: ({ onSelect }: any) => <button onClick={() => onSelect('second')}>Second invocation</button> }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnReviewExportPanel', () => ({ default: (props: any) => { state.restore = props.onRestore; state.reviewProps = props; return <button>Export review</button>; } }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => { state.workbench = props; return <button onClick={() => props.onMetricWorkbenchVisibilityChange(true)}>Show metrics</button>; } }));
vi.mock('../../src/lib/frustraMpnnApi', async (original) => ({
    ...await original<any>(),
    listFrustraMpnnResults: async () => ({ items: [{ invocation_id: 'first' }], total: 1 }),
    fetchFrustraMpnnResult: async (_job: string, invocation: string) => detail(invocation),
    fetchFrustraMpnnStatistics: async () => ({ statistics: null }),
    fetchFrustraMpnnReceipt: async () => ({ status: 'succeeded', results: [] }),
    listFrustraMpnnArtifacts: async () => ({ items: [{ artifact_id: 'pdb', role: 'normalized_input', schema_name: null, schema_version: null, media_type: 'chemical/x-pdb', download_url: '/saved.pdb', content_sha256: 'a'.repeat(64), size_bytes: 12 }] }),
    fetchFrustraMpnnLandscape: async (_job: string, _invocation: string, offset: number, limit: number) => {
        state.calls.push(offset);
        return { candidate_id: 'candidate', offset, limit, total: rows.length, next_offset: offset + limit < rows.length ? offset + limit : null, rows: rows.slice(offset, offset + limit) };
    },
}));
const counts = { high: 0, neutral: 0, minimal: 0 };
const detail = (invocation: string) => ({
    invocation_id: invocation, parent_job_id: 'job', candidate_id: 'candidate', status: 'succeeded', statistics_available: true,
    terminal_result: { invocation_id: invocation, parent_job_id: 'job', candidate_id: 'candidate', status: 'succeeded' },
    summary: { schema_name: 'frustrampnn_summary', parent_job_id: 'job', candidate_id: 'candidate', landscape_sha256: 'a'.repeat(64),
        residue_support: { expected: 540, scoreable: 539 }, slot_support: { expected: 10800, scoreable: 10780 }, missingness_by_reason: {},
        native_slot_counts: counts, native_slot_fractions: counts, complete_landscape_counts: counts, complete_landscape_fractions: counts,
        threshold_policy: { high_max: -1, minimal_min: 0.58 } },
});
const rows = Array.from({ length: 540 }, (_, i) => CANONICAL_AMINO_ACIDS.map((mutation_aa) => ({
    candidate_id: 'candidate', entity_instance_id: 'A', auth_asym_id: 'A', auth_seq_id: String(i + 1), insertion_code: '', sequence_index: i + 1,
    source_entity_id: '1', label_asym_id: 'A', label_seq_id: i + 1, pdb_chain_id: 'A', pdb_residue_id: i + 1, pdb_insertion_code: '', model_position: i,
    residue_name: 'GLY', wt: 'G', mutation_aa, score: i === 539 ? null : 0.25, class: i === 539 ? null : 'neutral',
    scoreable: i !== 539, status: i === 539 ? 'missing' : 'ok', reason: i === 539 ? 'missing raw row' : null, provenance: {},
}))).flat();
const settle = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); }); };
const mount = async (modelId = 'conformational_mapping') => {
    state.calls = []; state.fills = 0;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => new Proxy({}, { get: (_t, key) => key === 'fillRect' ? () => { state.fills++; } : () => {}, set: () => true }) as any);
    const container = document.createElement('div'); document.body.append(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const root = createRoot(container);
    await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><Viewer job={{ id: 'job', model_id: modelId, status: 'completed', created_at: '' } as any} onBack={() => {}} onOpenJob={() => {}} /></QueryClientProvider></MemoryRouter>));
    await settle();
    return { container, close: async () => { await act(async () => root.unmount()); client.clear(); } };
};
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); });
const change = async (container: HTMLElement, label: string, value: string) => {
    const select = container.querySelector(`[aria-label="${label}"]`) as HTMLSelectElement;
    await act(async () => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); });
};
const restore = (invocation: string, offset: number) => ({ result_references: [{ parent_job_id: 'job', invocation_id: invocation }], filters: {}, viewer_state: { landscape_offset: offset }, selected_residues: [{ auth_asym_id: 'A', auth_seq_id: '540', insertion_code: '' }] });

it('End reveals A540 with all missing slots under filters without duplicate page reads; hover does not repaint base', async () => {
    const { container, close } = await mount();
    try {
        expect(state.calls).toEqual(Array.from({ length: 22 }, (_, i) => i * 500));
        expect(state.workbench.showMetricWorkbench).toBe(false);
        expect(container.querySelector('[data-testid="editable-settings"]')).toBeNull();
        expect(container.textContent).not.toContain('Reanalyze persisted inputs');
        expect(container.querySelector('[data-testid="analytics"]')).not.toBeNull();
        expect(container.querySelector('a[href="/saved.pdb"]')).not.toBeNull();
        expect(container.textContent).toContain('Export review');
        expect(container.textContent).not.toContain('Future:');
        const optional = Array.from(container.querySelectorAll('details')).find(node => node.textContent?.includes('Upload external candidates'))!;
        expect(optional.open).toBe(false);
        expect(container.querySelector('#frustrampnn-landscape')!.compareDocumentPosition(optional) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        const canvas = container.querySelector('canvas[tabindex]')!;
        const before = state.fills;
        await act(async () => canvas.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 60, clientY: 65 })));
        expect(state.fills).toBe(before);
        await act(async () => canvas.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'End' })));
        expect(container.querySelector('[aria-label="Complete FrustraMPNN mutation landscape"] [aria-live]')?.textContent).toContain('A:540');
        const profile = () => container.querySelector('[aria-label="Exact 20-substitution residue profiles"] tbody tr')!;
        expect(profile().textContent).toContain('A:540');
        expect(container.textContent).toContain('1–500 of 10800');
        expect(profile().querySelectorAll('td')).toHaveLength(20);
        expect(profile().querySelectorAll('[title*="missing raw row"]')).toHaveLength(20);
        expect(state.workbench.residueSelections).toEqual([]); // unavailable spatial authority must not be guessed
        await change(container, 'Filter by mutation amino acid', 'A');
        await change(container, 'Filter by FrustraMPNN slot status', 'ok');
        expect(profile().textContent).toContain('A:540');
        expect(container.textContent).toContain('1–500 of 539');
        const next = Array.from(container.querySelectorAll('button')).filter(node => node.textContent === 'Next').at(-1)!;
        await act(async () => next.click());
        expect(container.textContent).toContain('501–539 of 539');
        await change(container, 'Filter by FrustraMPNN slot status', 'missing');
        expect(container.textContent).toContain('1–1 of 1');
        expect(state.calls).toHaveLength(22);
    } finally { await close(); }
});

it('scheduler-child settings remain available with the existing reanalysis action', async () => {
    const { container, close } = await mount('frustrampnn');
    try {
        expect(container.querySelector('[data-testid="editable-settings"]')).not.toBeNull();
        expect(container.textContent).toContain('Reanalyze persisted inputs');
        expect(container.textContent).toContain('Upload external candidates');
    } finally { await close(); }
});

it('same-invocation restore cannot leak offset into ordinary navigation; cross-invocation restore preserves selection and offset', async () => {
    const { container, close } = await mount();
    try {
        await act(async () => state.restore(restore('first', 500)));
        expect(state.reviewProps.viewerState.landscape_offset).toBe(500);
        await act(async () => Array.from(container.querySelectorAll('button')).find(node => node.textContent === 'Second invocation')!.click());
        await settle();
        expect(state.reviewProps.viewerState.landscape_offset).toBe(0);
        expect(state.reviewProps.selectedResidue).toBeNull();
        await act(async () => state.restore(restore('first', 10000)));
        await settle();
        expect(state.reviewProps.viewerState.landscape_offset).toBe(10000);
        expect(state.reviewProps.selectedResidue.authSeqId).toBe(540);
        expect(container.querySelector('[aria-label="Exact 20-substitution residue profiles"] tbody tr')?.textContent).toContain('A:540');
    } finally { await close(); }
});
