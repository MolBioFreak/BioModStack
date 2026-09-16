import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { PrimerPanel } from '../../src/components/MolBioToolkit/panels/PrimerPanel';
import { PCRPanel } from '../../src/components/MolBioToolkit/panels/PCRPanel';
import { AlignmentPanel } from '../../src/components/MolBioToolkit/panels/AlignmentPanel';
import { SearchPanel } from '../../src/components/MolBioToolkit/panels/SearchPanel';
import { FeaturePanel } from '../../src/components/MolBioToolkit/panels/FeaturePanel';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { useInputOwnership } from '../../src/components/MolBioToolkit/panels/useInputOwnership';
import * as api from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({
 calculatePrimerTm: vi.fn(), calculatePrimerQc: vi.fn(), fetchPrimers: vi.fn(), createPrimer: vi.fn(),
 deletePrimer: vi.fn(), togglePrimerFavorite: vi.fn(), designPrimers: vi.fn(),
 runPcrOperation: vi.fn(), fetchPcrExperimentRevision: vi.fn(), fetchPcrExperimentRevisions: vi.fn(),
 alignMolBioSequences: vi.fn(), fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(),
 simulateLigationAssembly: vi.fn(), saveLigationAssembly: vi.fn(), simulateGibsonAssembly: vi.fn(), saveGibsonAssembly: vi.fn(),
 simulateGoldenGateAssembly: vi.fn(), saveGoldenGateAssembly: vi.fn(), planDnaWeaverGibsonAssembly: vi.fn(), saveDnaWeaverGibsonAssembly: vi.fn(),
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
 useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }),
}));
vi.mock('../../src/components/MolBioToolkit/PrimerTmSettingsPanel', () => ({ PrimerTmSettingsPanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/panels/GibsonDesignWorkspace', () => ({ GibsonDesignWorkspace: () => null }));
let root: Root, host: HTMLDivElement;
const seq = { name: 'editable', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const settings = { algorithm: 'wallace', salt_correction: 'none' } as any;
const noop = () => {};
const primerProps = { sequenceData: seq, selection: null, onHighlight: noop, onAddPrimer: vi.fn(), onRemovePrimer: noop, tmOptions: null, tmSettings: settings, onTmSettingsChange: noop };
const alignment = { query_name: 'OBSOLETE', reference_aligned: 'ACGTACGT', query_aligned: 'ACGTACGT', midline: '||||||||', reference_start: 0, reference_end: 8, query_start: 0, query_end: 8, identity_pct: 100, query_coverage: 100, reference_coverage: 20, score: 16, variants: [], mode: 'placement', strand: 'forward' };
const assembly = { sequence: 'ACGT', length: 4, circular: false, mode: 'ligation', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions: [] };
const metric = (tm: number) => ({ tm, gc_percent: 50, algorithm: 'wallace', salt_correction: 'none', warnings: [] });
function deferred<T = any>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
async function render(node: React.ReactNode) { await act(async () => root.render(node)); }
async function click(label: string) { const b = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }
async function input(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
 const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
 await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); });
}
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(250); }); }
beforeEach(() => {
 vi.useFakeTimers(); vi.clearAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
 vi.mocked(api.calculatePrimerTm).mockResolvedValue({ data: [metric(40)] } as any);
 vi.mocked(api.calculatePrimerQc).mockResolvedValue({ data: { primers: [], pairwise: [] } } as any);
 vi.mocked(api.fetchPrimers).mockResolvedValue({ data: [] } as any);
 vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as any);
 vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [] } } as any);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('input ownership rejects changed, ABA and unmounted completions', async () => {
 let owner: ReturnType<typeof useInputOwnership>;
 function Harness({ value }: { value: string }) { owner = useInputOwnership([value]); return null; }
 await render(<Harness value="A" />); const original = owner!;
 await render(<Harness value="B" />); expect(original.isCurrent()).toBe(false);
 await render(<Harness value="A" />); expect(original.isCurrent()).toBe(false); expect(owner!.isCurrent()).toBe(true);
 await render(null); expect(owner!.isCurrent()).toBe(false);
});
it('chemistry changes calculate Tm but not unchanged QC; hidden drafts do no work', async () => {
 await render(<PrimerPanel {...primerProps} />);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!, 'ACGTACGTACGTACGT'); await tick();
 expect(api.calculatePrimerTm).toHaveBeenCalledTimes(1); expect(api.calculatePrimerQc).toHaveBeenCalledTimes(1);
 await render(<PrimerPanel {...primerProps} tmSettings={{ ...settings, sodium: 90 }} />); await tick();
 expect(api.calculatePrimerTm).toHaveBeenCalledTimes(2); expect(api.calculatePrimerQc).toHaveBeenCalledTimes(1);
 await click('Library'); await render(<PrimerPanel {...primerProps} tmSettings={{ ...settings, sodium: 100 }} />); await tick();
 expect(api.calculatePrimerTm).toHaveBeenCalledTimes(2); expect(api.calculatePrimerQc).toHaveBeenCalledTimes(1);
});
it('Add Primer never attaches preceding chemistry metrics to the current draft', async () => {
 await render(<PrimerPanel {...primerProps} />);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!, 'ACGTACGTACGTACGT'); await tick();
 vi.mocked(api.calculatePrimerTm).mockResolvedValue({ data: [metric(75)] } as any);
 const currentSettings = { ...settings, sodium: 90 };
 await render(<PrimerPanel {...primerProps} tmSettings={currentSettings} />);
 await click('Add Primer');
 expect(primerProps.onAddPrimer).toHaveBeenCalledTimes(1);
 expect(primerProps.onAddPrimer.mock.calls[0][0]).toMatchObject({ tm: 75, tm_settings: currentSettings });
});
it('primer lookup debounces actual typing, rejects stale results and pages on demand', async () => {
 const old = deferred(); vi.mocked(api.fetchPrimers).mockReturnValueOnce(old.promise);
 await render(<PrimerPanel {...primerProps} />); await click('Library'); await tick();
 const search = host.querySelector('input[placeholder="Search primers..."]')! as HTMLInputElement;
 await input(search, 'a'); await input(search, 'ab'); await input(search, 'abc');
 expect(api.fetchPrimers).toHaveBeenCalledTimes(1);
 vi.mocked(api.fetchPrimers).mockResolvedValueOnce({ data: Array.from({ length: 50 }, (_, i) => ({ id: String(i), name: `current-${i}`, sequence: 'ACGT', length: 4 })) } as any);
 await tick(); expect(api.fetchPrimers).toHaveBeenCalledTimes(2);
 expect(api.fetchPrimers).toHaveBeenLastCalledWith({ search: 'abc', favorites_only: false, limit: 50, offset: 0 });
 await act(async () => old.resolve({ data: [{ id: 'old', name: 'OBSOLETE', sequence: 'ACGT', length: 4 }] }));
 expect(host.textContent).not.toContain('OBSOLETE'); expect(host.textContent).toContain('current-49');
 await click('Load more primers'); expect(api.fetchPrimers).toHaveBeenLastCalledWith({ search: 'abc', favorites_only: false, limit: 50, offset: 50 });
 expect(host.textContent).not.toContain('Load more primers');
});
it('PCR uses editable inline bytes and topology even with a selected stored id', async () => {
 vi.mocked(api.runPcrOperation).mockResolvedValue({ product: { sequence: seq.sequence, length: seq.sequence.length }, experiment_id: 'e', experiment_revision_id: 'r' } as any);
 await render(<MemoryRouter><PCRPanel sequenceData={{ ...seq, circular: true }} sequenceId="stored-head" onHighlight={noop} tmOptions={null} tmSettings={settings} onTmSettingsChange={noop} /></MemoryRouter>);
 const inputs = host.querySelectorAll<HTMLInputElement>('input[placeholder="ATGC..."]');
 await input(inputs[0], 'ACGTACGTACGTACGT'); await input(inputs[1], 'ACGTACGTACGTACGT'); await click('Run PCR');
 expect(api.runPcrOperation).toHaveBeenCalledTimes(1);
 expect(api.runPcrOperation).toHaveBeenLastCalledWith(expect.objectContaining({ sequence: seq.sequence, name: 'editable', sequence_type: 'dna', is_circular: true, persist_experiment: true }));
 expect(vi.mocked(api.runPcrOperation).mock.calls[0][0]).not.toHaveProperty('sequence_id');
});
it('alignment rejects late results after a reference edit', async () => {
 const pending = deferred(); vi.mocked(api.alignMolBioSequences).mockReturnValue(pending.promise);
 const props = { sequenceData: seq, selection: null, onHighlight: noop, onAddFeatures: vi.fn() };
 await render(<AlignmentPanel {...props} />); await input(host.querySelector('textarea')!, 'ACGTACGT'); await click('Run Alignment');
 expect(api.alignMolBioSequences).toHaveBeenCalledTimes(1);
 await render(<AlignmentPanel {...props} sequenceData={{ ...seq, sequence: seq.sequence + 'A' }} />);
 await act(async () => pending.resolve({ data: alignment }));
 expect(host.textContent).not.toContain('OBSOLETE'); expect(host.textContent).not.toContain('Annotate Variants');
});
it('feature multi-delete invokes one batch callback with singular fallback', async () => {
 const features = [{ id: 'f1', name: 'one', type: 'misc_feature', start: 0, end: 4, strand: 1 }, { id: 'f2', name: 'two', type: 'misc_feature', start: 4, end: 8, strand: 1 }] as any;
 const singular = vi.fn(), batch = vi.fn();
 const props = { sequenceData: { ...seq, features }, selection: null, onHighlight: noop, onAddFeature: noop, onRemoveFeature: singular };
 await render(<FeaturePanel {...props} onRemoveFeatures={batch} />); await click('Select all shown'); await click('Delete 2');
 expect(batch).toHaveBeenCalledExactlyOnceWith(['f1', 'f2']); expect(singular).not.toHaveBeenCalled();
 await render(<FeaturePanel {...props} />); await click('Select all shown'); await click('Delete 2');
 expect(singular.mock.calls).toEqual([['f1'], ['f2']]);
});
it('designed pair adds once and metrics disappear after chemistry changes', async () => {
 const primer = { sequence: 'ACGTACGTACGTACGT', start: 0, end: 16, tm: 62, gc_percent: 50 };
 vi.mocked(api.designPrimers).mockResolvedValue({ data: { target_start: 0, target_end: 40, pair_count: 1, warnings: [], pairs: [{ rank: 1, forward: primer, reverse: { ...primer, start: 20, end: 36 }, tm_delta: 0, penalty: 0, warnings: [] }] } } as any);
 const batch = vi.fn(); await render(<PrimerPanel {...primerProps} onAddPrimers={batch} />); await click('Design');
 const run = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Design') && b.textContent?.trim() !== 'Design')!;
 await act(async () => run.click()); await click('Add Pair');
 expect(batch).toHaveBeenCalledTimes(1); expect(batch.mock.calls[0][0]).toHaveLength(2); expect(primerProps.onAddPrimer).not.toHaveBeenCalled();
 await render(<PrimerPanel {...primerProps} onAddPrimers={batch} tmSettings={{ ...settings, sodium: 90 }} />);
 expect(host.textContent).not.toContain('Add Pair');
});
it('library Tm failure never relabels stored metrics with active chemistry', async () => {
 vi.mocked(api.fetchPrimers).mockResolvedValue({ data: [{ id: 'lib', name: 'stored-primer', sequence: 'ACGTACGTACGTACGT', length: 16, tm: 99, tm_algorithm: 'old-model', tm_salt_correction: 'old-salt', sequence_type: 'dna' }] } as any);
 vi.mocked(api.calculatePrimerTm).mockResolvedValue({ data: [] } as any);
 await render(<PrimerPanel {...primerProps} />); await click('Library'); await tick();
 await act(async () => (host.querySelector('button[title="Add to sequence"]') as HTMLButtonElement).click());
 expect(primerProps.onAddPrimer).toHaveBeenCalledTimes(1);
 expect(primerProps.onAddPrimer.mock.calls[0][0].tm).toBeUndefined(); expect(primerProps.onAddPrimer.mock.calls[0][0].tm_algorithm).toBeUndefined();
});
it('completed selection alignment cannot annotate after moving the selection', async () => {
 const aligned = { ...alignment, variants: [{ type: 'substitution', start: 0, end: 1, query_start: 0, query_end: 1, label: 'A to G', reference: 'A', query: 'G' }] };
 vi.mocked(api.alignMolBioSequences).mockResolvedValue({ data: aligned } as any);
 const props = { sequenceData: seq, selection: { start: 0, end: 8 }, onHighlight: noop, onAddFeatures: vi.fn() };
 await render(<AlignmentPanel {...props} />);
 await act(async () => { const select = host.querySelector('select')!; select.value = 'selection'; select.dispatchEvent(new Event('change', { bubbles: true })); });
 await input(host.querySelector('textarea')!, 'ACGTACGT'); await click('Run Alignment'); await click('Annotate Variants');
 expect(props.onAddFeatures).toHaveBeenCalledTimes(1); expect(props.onAddFeatures.mock.calls[0][0][0].start).toBe(0);
 await render(<AlignmentPanel {...props} selection={{ start: 16, end: 24 }} />);
 expect(host.textContent).not.toContain('Annotate Variants'); expect(props.onAddFeatures).toHaveBeenCalledTimes(1);
});
it('completed assembly cannot load after editing fragment orientation', async () => {
 vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: assembly } } as any);
 const load = vi.fn(); await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={load} onLoadSavedWorkup={noop} />);
 await click('Add whole construct'); await click('Simulate'); expect(host.textContent).toContain('Load product');
 const orientation = [...host.querySelectorAll('select')].find(s => [...s.options].some(o => o.value === 'reverse'))!;
 await act(async () => { orientation.value = 'reverse'; orientation.dispatchEvent(new Event('change', { bubbles: true })); });
 expect(host.textContent).not.toContain('Load product'); expect(load).not.toHaveBeenCalled();
});
it('search mounts a debounced worker, cancels obsolete work, and reports timeout explicitly', async () => {
 const workers: any[] = [];
 class FakeWorker { onmessage: any; onerror: any; terminate = vi.fn(); postMessage = vi.fn(); constructor() { workers.push(this); } }
 vi.stubGlobal('Worker', FakeWorker);
 await render(<SearchPanel sequenceData={seq} onHighlight={noop} />);
 const field = host.querySelector('input[placeholder="Enter sequence or pattern..."]')! as HTMLInputElement;
 await input(field, 'AC'); await input(field, 'ACG'); expect(workers).toHaveLength(0); await tick(); expect(workers).toHaveLength(1);
 await input(field, 'GT'); expect(workers[0].terminate).toHaveBeenCalledTimes(1); await tick();
 await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
 expect(workers[1].terminate).toHaveBeenCalledTimes(1); expect(host.querySelector('[role="alert"]')?.textContent).toContain('timed out');
 expect(host.textContent).not.toContain('No matches found');
});
it('DNA Weaver preview survives plan-derived basket updates but not target edits', async () => {
 const plan = { ordered_fragments: [{ id: 'fragment', name: 'ordered', sequence: seq.sequence }], selected_product: { ...assembly, mode: 'gibson' }, quality_checks: [], order_ready: true, plan_checksum: 'plan' };
 vi.mocked(api.planDnaWeaverGibsonAssembly).mockResolvedValue({ data: plan } as any);
 const props = { sequenceData: seq, selection: null, selectedSequenceId: null, onLoadProduct: vi.fn(), onLoadSavedWorkup: noop };
 await render(<AssemblyPanel {...props} />); await click('Gibson'); await click('Plan vendor fragments');
 expect(host.textContent).toContain('Load product'); await click('Load product'); expect(props.onLoadProduct).toHaveBeenCalledTimes(1);
 await render(<AssemblyPanel {...props} sequenceData={{ ...seq, sequence: seq.sequence + 'A' }} />);
 expect(host.textContent).not.toContain('Load product');
});
it('assembly ignores late simulation after switching modes', async () => {
 const pending = deferred(); vi.mocked(api.simulateLigationAssembly).mockReturnValue(pending.promise);
 await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={vi.fn()} onLoadSavedWorkup={vi.fn()} />);
 const labels = [...host.querySelectorAll('button')].map(b => b.textContent?.trim());
 const add = labels.find(s => s?.includes('whole') || s?.includes('Whole'));
 expect(add, labels.join('|')).toBeDefined(); await click(add!);
 const simulate = [...host.querySelectorAll('button')].find(b => b.textContent?.trim().toLowerCase().includes('simulate'))!;
 await act(async () => simulate.click()); expect(api.simulateLigationAssembly).toHaveBeenCalledTimes(1);
 await click('Golden Gate'); await act(async () => pending.resolve({ data: { product: assembly } }));
 expect(host.textContent).not.toContain('Load product');
});
