import { primerModelOptions } from '../fixtures/primerModelOptions';
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
const primerProps = { sequenceData: seq, selection: null, onHighlight: noop, onAddPrimer: vi.fn(), onRemovePrimer: noop, tmOptions: primerModelOptions, tmSettings: settings, onTmSettingsChange: noop };
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

it('direct duplex controls send native strand orientation and signed shift without changing saved primer Tm', async () => {
 const scientificSettings={...settings,algorithm:'nn_santalucia_hicks_2004',salt_correction:'owczarzy_2008'};
 vi.mocked(api.createPrimer).mockResolvedValue({data:{id:'saved'}} as any);
 await render(<PrimerPanel {...primerProps} tmSettings={scientificSettings}/>);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!,'ACGTACGTACGTACGT'); await tick();
 await input(host.querySelector('input[aria-label="Complementary strand (3′→5′)"]')!,'ATGCATGCATGCATGCA');
 await input(host.querySelector('input[aria-label="Duplex strand shift"]')!,'1');
 vi.mocked(api.calculatePrimerTm).mockResolvedValueOnce({data:[{...metric(71),algorithm_label:'NN',salt_correction_label:'Mixed salt',effective_input:{sequence:'ACGTACGTACGTACGT',sequence_type:'dna',complement_sequence:'ATGCATGCATGCATGCA',shift:1},effective_settings:scientificSettings}]} as any);
 await click('Calculate duplex Tm');
 expect(vi.mocked(api.calculatePrimerTm).mock.calls.at(-1)![0]).toMatchObject({primers:[{sequence:'ACGTACGTACGTACGT',sequence_type:'dna',complement_sequence:'ATGCATGCATGCATGCA',shift:1}],settings:scientificSettings});
 expect(host.textContent).toContain('Duplex Tm: 71.0°C');
 expect(host.textContent).toContain('does not change its template binding or saved primer Tm');
 await click('Add Primer');
 expect(vi.mocked(api.createPrimer).mock.calls[0][0]).not.toHaveProperty('complement_sequence');
 expect(primerProps.onAddPrimer.mock.calls[0][0].tm).toBe(40);
});

it.each(['absent', 'null'] as const)('receives historical duplex Tm with %s provenance without inventing a receipt', async provenance => {
 await render(<PrimerPanel {...primerProps}/>);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!, 'ACGTACGTACGTACGT'); await tick();
 const result = { ...metric(42), ...(provenance === 'null' ? { effective_input: null, effective_settings: null } : {}) };
 vi.mocked(api.calculatePrimerTm).mockResolvedValueOnce({ data: [result] } as any);
 await click('Calculate duplex Tm');
 expect(host.textContent).toContain('Duplex Tm: 42.0°C');
 expect(host.textContent).not.toContain('Calculated ACGTACGTACGTACGT');
 expect(vi.mocked(api.calculatePrimerTm).mock.calls).toHaveLength(2);
});

it('a changed direct-duplex input hides an earlier result and ignores late completion', async () => {
 await render(<PrimerPanel {...primerProps}/>);
 await input(host.querySelector('input[placeholder="Sequence (5\'→3\')"]')!,'ACGTACGTACGTACGT'); await tick();
 const pending=deferred(); vi.mocked(api.calculatePrimerTm).mockReturnValueOnce(pending.promise);
 await click('Calculate duplex Tm');
 await input(host.querySelector('input[aria-label="Duplex strand shift"]')!,'-1');
 await act(async () => pending.resolve({data:[metric(99)]}));
 expect(host.textContent).not.toContain('99.0°C');
 expect((host.querySelector('input[aria-label="Duplex strand shift"]')! as HTMLInputElement).value).toBe('-1');
});

it('QC submits the visible fixed profile from canonical discovery', async () => {
 const profile={version:'exact_match_v1',binding_core_bases:12,shorter_primer_uses_whole_sequence:true,hairpin_min_stem:3,hairpin_min_loop:3,hairpin_max_loop:12,moderate_complement_bases:6,strong_complement_bases:8,three_prime_warning_bases:4,hairpin_warning_stem_bases:5,three_prime_requires_both_ends:true,template_scope:'provided_template_only'} as const;
 const p={id:'p',name:'PRIMER',sequence:'ACGTACGTACGTACGT',start:0,end:16,strand:1} as any;
 await render(<PrimerPanel {...primerProps} sequenceData={{...seq,primers:[p]}} tmOptions={{qc_profile:profile} as any}/>);
 await click('QC'); expect(vi.mocked(api.calculatePrimerQc).mock.calls[0][0].profile).toEqual(profile);
 expect(host.textContent).toContain('Fixed QC profile: exact_match_v1');
});


it.each(['ACGU'.repeat(100),'ACGC'.repeat(100)])('RNA PCR-design UI requests explicit cDNA and does not submit: %s', async sequence => {
 await render(<PrimerPanel {...primerProps} sequenceData={{...seq,sequence,sequenceType:'rna'}}/>);
 await click('Design');
 expect(host.textContent).toContain('Supply the intended cDNA sequence');
 const button=[...host.querySelectorAll('button')].find(button=>button.textContent?.trim()==='Design Primer Pairs');
 expect(button?.disabled).toBe(true); expect(api.designPrimers).not.toHaveBeenCalled();
});
