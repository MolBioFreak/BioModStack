import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
let root: Root, host: HTMLDivElement, client: QueryClient;
const seq = { name: 'editable', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const settings: api.PrimerTmSettings = { algorithm: 'wallace', salt_correction: 'none', primer_concentration_nM: 250, template_concentration_nM: 0, na_mM: 50, k_mM: 0, tris_mM: 0, mg_mM: 0, dntps_mM: 0, dmso_percent: 0, formamide_percent: 0, self_complementary: false };
const noop = () => {};
const primerProps = { sequenceData: seq, selection: null, onHighlight: noop, onAddPrimer: vi.fn(), onRemovePrimer: noop, tmOptions: null, tmSettings: settings, onTmSettingsChange: noop };
const alignment = { query_name: 'OBSOLETE', reference_aligned: 'ACGTACGT', query_aligned: 'ACGTACGT', midline: '||||||||', reference_start: 0, reference_end: 8, query_start: 0, query_end: 8, identity_pct: 100, query_coverage: 100, reference_coverage: 20, score: 16, variants: [], mode: 'placement', strand: 'forward' };
const assembly = { sequence: 'ACGT', length: 4, circular: false, mode: 'ligation', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions: [] };
const metric = (tm: number) => ({ tm, gc_percent: 50, algorithm: 'wallace', salt_correction: 'none', warnings: [] });
function deferred<T = any>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
async function render(node: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); }
async function click(label: string) { const b = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }
async function input(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
 const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
 await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); });
}
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(250); }); }
beforeEach(() => {
 client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
 vi.useFakeTimers(); vi.clearAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
 vi.mocked(api.calculatePrimerTm).mockResolvedValue({ data: [metric(40)] } as any);
 vi.mocked(api.calculatePrimerQc).mockResolvedValue({ data: { primers: [], pairwise: [] } } as any);
 vi.mocked(api.fetchPrimers).mockResolvedValue({ data: [] } as any);
 vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as any);
 vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [] } } as any);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });


it('saves a reusable library oligo without invalid construct geometry', async () => {
 vi.mocked(api.createPrimer).mockResolvedValue({ data: {id:'saved'} } as any);
 await render(<PrimerPanel {...primerProps}/>);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!, 'ACGTACGTACGTACGT'); await tick(); await click('Add Primer');
 const payload=vi.mocked(api.createPrimer).mock.calls[0][0];
 expect(payload.sequence).toBe('ACGTACGTACGTACGT');
 expect(payload).not.toHaveProperty('binding_start'); expect(payload).not.toHaveProperty('binding_end');
 expect(primerProps.onAddPrimer).toHaveBeenCalledTimes(1);
});
it('library failure stays visible and retry adds exactly one primer', async () => {
 vi.mocked(api.createPrimer).mockRejectedValueOnce(new Error('Library offline')).mockResolvedValueOnce({data:{id:'saved'}} as any);
 await render(<PrimerPanel {...primerProps}/>);
 const field=host.querySelector('input[placeholder="Sequence (5\'→3\')"]')! as HTMLInputElement;
 await input(field,'ACGTACGTACGTACGT'); await tick(); await click('Add Primer');
 expect(host.textContent).toContain('library save failed'); expect(field.value).toBe('ACGTACGTACGTACGT');
 expect(primerProps.onAddPrimer).not.toHaveBeenCalled();
 await click('Add Primer'); expect(primerProps.onAddPrimer).toHaveBeenCalledTimes(1); expect(field.value).toBe('');
});
it('QC failure after a template change cannot display prior site results', async () => {
 const p={id:'p',name:'PRIMER',sequence:'ACGTACGTACGTACGT',start:0,end:16,strand:1} as any;
 vi.mocked(api.calculatePrimerQc).mockResolvedValueOnce({data:{primers:[{id:'p',name:'PRIMER',qc:{sequence:p.sequence,max_self_complement:0,three_prime_self_complement:0,max_hairpin_stem:0,binding_site_count:1,off_target_site_count:0,warnings:[]}}],pairwise:[]}} as any);
 await render(<PrimerPanel {...primerProps} sequenceData={{...seq,primers:[p]}}/>); await click('QC');
 expect(host.textContent).toContain('sites 1');
 vi.mocked(api.calculatePrimerQc).mockRejectedValueOnce(new Error('QC unavailable'));
 await render(<PrimerPanel {...primerProps} sequenceData={{...seq,sequence:'T'.repeat(seq.sequence.length),primers:[p]}}/>);
 expect(host.textContent).toContain('Current QC unavailable'); expect(host.textContent).not.toContain('sites 1');
 expect(host.textContent).not.toContain('off-target 0');
});
it('pinned selection cannot attach an old primer to an edited nonbinding template', async () => {
 await render(<PrimerPanel {...primerProps} selection={{start:0,end:16}}/>);
 await click('Use as Forward'); await tick();
 await render(<PrimerPanel {...primerProps} selection={{start:0,end:16}} sequenceData={{...seq,sequence:'T'.repeat(seq.sequence.length)}}/>); await tick();
 expect(host.textContent).not.toContain('Pinned to selected locus'); await click('Add Primer');
 expect(primerProps.onAddPrimer).not.toHaveBeenCalled(); expect(host.textContent).toContain('No primer annealing site');
});
it('unverified binding does not supply an intended site to QC', async () => {
 const p={id:'p',name:'PRIMER',sequence:'ACGTACGTACGTACGT',start:0,end:0,strand:1,binding_status:'needs_review',binding_warning:'Binding needs review'} as any;
 await render(<PrimerPanel {...primerProps} sequenceData={{...seq,primers:[p]}}/>);
 expect(host.textContent).toContain('Binding needs review'); await click('QC');
 expect(vi.mocked(api.calculatePrimerQc).mock.calls[0][0].primers[0]).not.toHaveProperty('intended_binding_start');
});
