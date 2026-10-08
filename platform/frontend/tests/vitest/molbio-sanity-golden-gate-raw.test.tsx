import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { GoldenGateDesignEditor, type GoldenGateDesignEditorProps } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateDesignEditor';
import { GoldenGatePartsEditor } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGatePartsEditor';
import { GoldenGateScienceEditor } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateScienceEditor';
import { GoldenGateSearchResults } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateSearchResults';
import scoreFixture from '../fixtures/golden-gate/published_11.json';
import windowFixture from '../fixtures/golden-gate/circular_windows.json';
import searchFixture from '../fixtures/golden-gate/bounded_search.json';
import { GoldenGateDesignResults, GoldenGateFidelityResults, GoldenGateDomesticationResults, GoldenGateReactionResults } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateDesignResults';
import { GoldenGateReactionEditor } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateReactionEditor';
import { GoldenGateDomesticationEditor } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateDomesticationEditor';
import { GoldenGateFidelityEditor, GoldenGateOverhangSearchEditor, GoldenGateWindowSearchEditor } from '../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateFidelityEditor';
import { compileCoreRequest, type FidelityResult, type SearchResult, type GoldenGateDraft, type GoldenGateDesignResult, type DomesticationResult, type ReactionResult } from '../../src/lib/goldenGateDesign';
import { core, draft, nativeResult, preparationChoices, reactionTemplates } from '../fixtures/golden-gate/nativeDraft';
import sapiRequest from '../fixtures/golden-gate/SapI-request.json';
import sapiResult from '../fixtures/golden-gate/SapI-result.json';

let root: Root; let host: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root.unmount()); host?.remove(); vi.restoreAllMocks(); });
async function mount(node: React.ReactNode) { host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => root.render(node)); }
function field(label: string) { const node = Array.from(host.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('[aria-label]')).find(n => n.getAttribute('aria-label') === label); if (!node) throw new Error(`Missing field ${label}`); return node; }
async function change(label: string, value: string) { const node = field(label); await act(async () => { const proto = node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); }
async function click(text: string) { const node = Array.from(host.querySelectorAll('button')).find(n => n.textContent === text); if (!node) throw new Error(`Missing button ${text}: ${host.textContent}`); await act(async () => node.click()); }
async function check(label: string) { await act(async () => (field(label) as HTMLInputElement).click()); }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); }); }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (e: Error) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function evidence(name: string, payload: unknown) { if (process.env.BMS_GG_UI_EVIDENCE) { mkdirSync(process.env.BMS_GG_UI_EVIDENCE, { recursive: true }); writeFileSync(join(process.env.BMS_GG_UI_EVIDENCE, `${name}.json`), JSON.stringify(payload, null, 2)); } }
const baseProps = { sourceChoices: [], preparationChoices, enzymeChoices: [core.enzyme], tmOptions: null };
let latest: GoldenGateDraft;
function Harness({ initial = draft(), ...props }: Partial<GoldenGateDesignEditorProps> & { initial?: GoldenGateDraft }) {
  const [value, setValue] = useState(initial); latest = value;
  return <GoldenGateDesignEditor {...baseProps} value={value} onChange={setValue} onDesign={async () => ({ core: nativeResult })} {...props} />;
}
function PartsHarness({ choices = [] }: { choices?: GoldenGateDesignEditorProps['sourceChoices'] }) { const [value, setValue] = useState(draft()); latest = value; return <GoldenGatePartsEditor {...baseProps} sourceChoices={choices} value={value.core} onChange={core => setValue({ ...value, core })} digests={nativeResult.digests} />; }
function ScienceHarness() {
  const [value, setValue] = useState(draft()); latest = value; const s = value.science;
  const update = (science: typeof s) => setValue({ ...value, science });
  return <><GoldenGateFidelityEditor value={s.fidelity} onChange={fidelity => update({ ...s, fidelity })} datasets={[{ id: 'pryor2020-s005', label: 'SapI, 3-base', description: '37C/16C · T4 buffer' }]} /><GoldenGateOverhangSearchEditor value={s.overhang_search!} onChange={overhang_search => update({ ...s, overhang_search })} /><GoldenGateWindowSearchEditor value={s.window_search!} onChange={window_search => update({ ...s, window_search })} /><GoldenGateDomesticationEditor value={s.domestication[0].settings} onChange={settings => update({ ...s, domestication: [{ source_id: 'insert', settings }] })} featureIds={['cds']} siteChoices={s.domestication[0].settings.unwanted_sites} cdsTemplate={s.domestication[0].settings.cds[0]} /><GoldenGateReactionEditor value={s.reaction!} onChange={reaction => update({ ...s, reaction })} partIds={['backbone', 'insert']} templates={reactionTemplates} /></>;
}

describe('Golden Gate controlled native authoring', () => {
  it('submits the real core example once, with separate untouched science and no mount/edit RPC', async () => {
    const design = vi.fn<GoldenGateDesignEditorProps['onDesign']>(async () => ({ core: nativeResult })); await mount(<Harness onDesign={design} />);
    expect(design).not.toHaveBeenCalled(); await change('insert name', 'Operator insert'); await change('Product display origin (bp)', '3'); expect(design).not.toHaveBeenCalled();
    await click('Design explicit parts'); await settle(); expect(design).toHaveBeenCalledTimes(1);
    const [request, science, signal] = design.mock.calls[0] as unknown as [typeof core, GoldenGateDraft['science'], AbortSignal];
    expect(request).toEqual({ ...core, target: { ...core.target, display_origin: 3 }, parts: core.parts.map(p => p.id === 'insert' ? { ...p, name: 'Operator insert' } : p) });
    expect(science).toEqual(draft().science); expect(science.reaction!.reagents[0].formulation).toBe('Requested CutSmart + ATP/DTT'); expect(signal.aborted).toBe(false);
    evidence('mounted-edited-callback', { core: request, science });
  });
  it('preserves immutable revision, editor snapshot annotations and unsaved inline identity independently', async () => {
    const editor = structuredClone(core.sources[1]);
    await mount(<PartsHarness choices={[{ label: 'Revision A', source: { id: 'r', source: { kind: 'molecular_revision', revision_id: 'revision-A' } } }, { label: 'Unsaved editor', source: editor }]} />);
    await change('New source ID', 'immutable'); await change('Source choice', '0'); await click('Add selected source');
    await change('New source ID', 'editor'); await change('Source choice', '1'); await click('Add selected source');
    await change('editor DNA', 'ACGT');
    expect(latest.core.sources.find(s => s.id === 'immutable')?.source).toEqual({ kind: 'molecular_revision', revision_id: 'revision-A' });
    expect(editor).toEqual(core.sources[1]); expect(latest.core.sources.find(s => s.id === 'editor')?.source).toEqual({ ...editor.source, sequence: 'ACGT' });
    expect(host.textContent).toContain('Immutable revision: revision-A'); expect(host.querySelector('[aria-label="immutable DNA"]')).toBeNull();
    await change('New source ID', 'raw'); await click('Add inline DNA source'); await change('raw DNA', 'AACCGG'); await change('raw source topology', 'circular');
    expect(latest.core.target.topology).toBe('circular'); expect(latest.core.sources.find(s => s.id === 'raw')?.source).toEqual({ kind: 'inline', sequence: 'AACCGG', topology: 'circular', features: [] });
    evidence('mounted-identities', latest.core);
  });
  it('keeps source and product topology independent and edits wrapped/reverse payloads', async () => {
    await mount(<PartsHarness />); await change('Product topology', 'linear'); await change('insert source topology', 'circular'); await change('insert orientation', 'reverse'); await change('insert payload region start', '45'); await change('insert payload region end', '12'); await check('insert payload region wraps origin');
    expect(latest.core.target.topology).toBe('linear'); expect(latest.core.sources[0].source).toEqual(core.sources[0].source);
    expect(latest.core.parts[1].preparation).toMatchObject({ region: { start: 45, end: 12, wraps_origin: true } }); expect(compileCoreRequest(latest.core).parts[1].orientation).toBe('reverse'); evidence('mounted-wrap', latest.core);
  });
  it('edits all preparation choices and retains explicit removal/physical end choices', async () => {
    await mount(<PartsHarness />); await click('Retain backbone fragment 1'); await check('Remove backbone fragment 0 from reaction');
    expect(latest.core.parts[0].preparation).toEqual({ kind: 'donor', retained_fragment_index: 1, removed_fragment_indices: [0] });
    await change('insert preparation', 'synthesis'); await change('insert left spacer', 'TG'); expect(latest.core.parts[1].preparation).toMatchObject({ kind: 'synthesis', left: { spacer: 'TG' } });
    await change('insert preparation', 'prepared'); await change('insert right end protruding strand', 'top'); await change('insert right end type', 'sticky_3'); await change('insert right end overhang', 'AGTC');
    expect(latest.core.parts[1].preparation).toMatchObject({ kind: 'prepared', right_end: { protruding_strand: 'top', type: 'sticky_3', overhang: 'AGTC' } });
    await change('insert preparation', 'pcr'); await change('insert forward anneal length', '22'); await change('insert reverse anneal length', '23'); await change('insert QC minimum binding length', '11');
    expect(latest.core.parts[1].preparation).toMatchObject({ forward_anneal_length: 22, reverse_anneal_length: 23, qc_min_binding_anneal_length: 11 }); evidence('mounted-preparations', latest.core);
  });
  it('keeps digest fragment pickers usable across retention/removal edits but invalidates them on source edits', async () => {
    const design = vi.fn<GoldenGateDesignEditorProps['onDesign']>(async () => ({ core: nativeResult }));
    await mount(<Harness onDesign={design} />); await click('Design explicit parts'); await settle();
    await click('Retain backbone fragment 1'); await check('Remove backbone fragment 0 from reaction');
    expect(latest.core.parts[0].preparation).toEqual({ kind: 'donor', retained_fragment_index: 1, removed_fragment_indices: [0] });
    expect(host.querySelector('[aria-label="Remove backbone fragment 0 from reaction"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Selected candidate"]')).toBeNull();
    await change('donor DNA', 'ACGT');
    expect(host.querySelector('[aria-label="Remove backbone fragment 0 from reaction"]')).toBeNull();
    expect(design).toHaveBeenCalledTimes(1);
  });
  it('reorders repeated source instances without duplicating source material', async () => {
    await mount(<PartsHarness />); await change('New part ID', 'insert-copy'); await change('New part source', 'insert'); await change('New part preparation', 'synthesis'); await click('Add part'); await click('Move insert-copy earlier');
    expect(latest.core.parts.map(p => p.id)).toEqual(['backbone', 'insert-copy', 'insert']); expect(latest.core.sources).toHaveLength(2); await click('Remove part insert-copy'); expect(latest.core.parts).toHaveLength(2);
  });
  it('edits feature segments and preserves untouched qualifiers through callbacks', async () => {
    await mount(<PartsHarness />); await change('Feature 1 name', 'Edited CDS display'); await change('Feature 1 strand', '-1'); await change('Feature 1 codon start', '3');
    const source = latest.core.sources[1].source; expect(source.kind).toBe('inline'); if (source.kind !== 'inline') throw new Error(); expect(source.features[0]).toMatchObject({ name: 'Edited CDS display', strand: -1, codon_start: 3, qualifiers: { transl_table: ['11'] } });
  });
  it('preserves every shared chemistry parameter and never hydrates over requested edits', async () => {
    const requested = draft(); requested.core.primer_settings = { ...requested.core.primer_settings, k_mM: 11, tris_mM: 17, mg_mM: 2, dmso_percent: 3, formamide_percent: 4, self_complementary: true };
    await mount(<Harness initial={requested} />); await click('Show Chemistry');
    const label = Array.from(host.querySelectorAll('label')).find(x => x.textContent?.startsWith('Tris mM'))!; const input = label.querySelector('input')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '19.25'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    const options = { algorithms: [], salt_corrections: [], defaults: { dna: core.primer_settings, rna: core.primer_settings } };
    await act(async () => root.render(<Harness initial={requested} tmOptions={options} />));
    expect(latest.core.primer_settings).toEqual({ ...requested.core.primer_settings, tris_mM: 19.25 }); evidence('mounted-chemistry', latest.core);
  });
  it('rejects malformed core syntax without making unavailable evidence a gate', async () => {
    const design = vi.fn<GoldenGateDesignEditorProps['onDesign']>(async () => ({ core: nativeResult })); await mount(<Harness onDesign={design} />); await change('insert left fusion', 'NOT-DNA!'); await click('Design explicit parts'); expect(design).not.toHaveBeenCalled(); expect(host.textContent).toContain('use IUPAC DNA'); await change('insert left fusion', 'GGAG'); await click('Design explicit parts'); expect(design).toHaveBeenCalledTimes(1);
  });
  it('emits SapI native fixture unchanged rather than forcing four-base ends', async () => {
    const initial = draft(); initial.core = structuredClone(sapiRequest) as typeof core; const design = vi.fn<GoldenGateDesignEditorProps['onDesign']>(async () => ({ core: sapiResult as unknown as GoldenGateDesignResult })); await mount(<Harness initial={initial} onDesign={design} />); await click('Design explicit parts'); expect(design.mock.calls[0][0]).toEqual(sapiRequest); evidence('mounted-sapi', design.mock.calls[0][0]);
  });
});

describe('Native supplemental settings', () => {
  it('authors reference/proxy, physical inventory, fixed/required/excluded domains and all search arguments', async () => {
    await mount(<ScienceHarness />); await change('Reference dataset', 'pryor2020-s005'); await change('Condition use', 'explicit_proxy'); await check('Include pair observations'); await click('Add physical end'); await change('End 1 instance ID', 'dropout'); await change('End 1 role', 'dropout'); await change('End 1 sequence (unknown if empty)', 'AAA'); await check('End 1 removed');
    await change('Candidate domain', 'AAA '); expect((field('Candidate domain') as HTMLInputElement).value).toBe('AAA '); await change('Candidate domain', 'AAA AAC AGC'); await change('Requested junction count', '2'); await change('Overhang length', '3'); await change('required overhangs', 'AAA'); await change('fixed overhangs', 'AAC'); await change('excluded overhangs', 'GCC');
    // There are two search editors; scope changes to the set-search section.
    const section = host.querySelector('[aria-label="Overhang set search"]')!;
    for (const [key, val] of Object.entries({ seed: 13, evaluation_budget: 231, restarts: 4, exact_limit: 17, alternatives: 2 })) { const n = section.querySelector<HTMLInputElement>(`[aria-label="Search ${key}"]`)!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(n, String(val)); n.dispatchEvent(new Event('input', { bubbles: true })); }); }
    expect(latest.science.fidelity).toMatchObject({ dataset_id: 'pryor2020-s005', condition_use: 'explicit_proxy', include_pair_observations: true, inventory: [{ instance_id: 'dropout', removed: true }] }); expect(latest.science.overhang_search).toMatchObject({ candidate_domain: ['AAA', 'AAC', 'AGC'], required: ['AAA'], fixed: ['AAC'], excluded: ['GCC'], settings: { seed: 13, evaluation_budget: 231, restarts: 4, exact_limit: 17, alternatives: 2 } }); evidence('mounted-fidelity', latest.science);
  });
  it('authors real split windows, fixed positions, protected and coding placement constraints', async () => {
    await mount(<ScienceHarness />); await change('Window 1 start', '8'); await change('Window 1 fixed position (optional)', ''); await click('Add protected split region'); await change('Protected split 1 start', '2'); await change('Protected split 1 end', '4'); await click('Add frame constraint'); await change('Frame 1 start', '0'); await change('Frame 1 end', '10'); await change('Frame 1 origin', '1'); await change('Frame 1 phase', '2'); await change('Split max_fragment_length (bp)', '8');
    expect(latest.science.window_search).toMatchObject({ windows: [[8, 2], [4, 5]], fixed_positions: [null, 4], protected_regions: [[2, 4]], frame_constraints: [[0, 10, 1, 2]], max_fragment_length: 8 }); evidence('mounted-windows', latest.science);
  });
  it('keeps domestication off until selected and authors every CDS/edit policy', async () => {
    await mount(<ScienceHarness />); expect(latest.science.domestication[0].settings.enabled).toBe(false); await check('Request domestication proposals'); await change('Domestication candidate budget', '317'); await change('Maximum substitutions (optional)', '2'); await check('BbsI remove all occurrences'); await change('BbsI selected site starts (0-based)', '3 9'); await change('CDS 1 strand', '-1'); await change('CDS 1 initiation policy', 'allowed'); await change('CDS 1 allowed start codons', 'GTG ATG'); await change('CDS 1 stop policy', 'synonymous'); await click('Add Protected regions'); await change('Protected regions 1 start', '5'); await change('Protected regions 1 end', '8');
    expect(latest.science.domestication[0].settings).toMatchObject({ enabled: true, candidate_budget: 317, max_edits: 2, unwanted_sites: [{ starts: [3, 9] }], protected_regions: [{ start: 5, end: 8 }], cds: [{ strand: -1, genetic_code: 11, initiation: 'allowed', allowed_start_codons: ['GTG', 'ATG'], stop_policy: 'synonymous' }] }); expect(latest.core.sources).toEqual(core.sources); evidence('mounted-domestication', latest.science);
  });
  it('retains exact requested buffers and edits quantities, units, dilution, rounding and cycling', async () => {
    await mount(<ScienceHarness />); await change('Reagent 1 formulation', 'Custom buffer pH 7.9 + 2 mM ATP'); await change('Reagent 1 final strength (X)', '0.75'); await change('Total reaction volume (uL)', '25'); await change('Reaction DNA 1 stock unit', 'nM'); await change('Reaction DNA 1 stock concentration', '200'); await change('Reaction DNA 1 dilution factor', '4'); await change('Reaction DNA 1 dilution preparation volume (uL)', '40'); await check('Reaction DNA 1 in mastermix'); await change('Reaction DNA 1 phosphorylation', 'unphosphorylated'); await change('Reaction DNA 1 purification', 'gel-purified retained insert'); await change('Block 1 step 1 temperature (C)', '42'); await change('Block 1 repetitions', '12'); await change('Mastermix overage (%)', '17.5'); await change('Minimum transfer (uL, diagnostic only)', '');
    expect(latest.science.reaction).toMatchObject({ settings: { total_volume_uL: 25, mastermix_overage_percent: 17.5, minimum_transfer_uL: null, cycling: { blocks: [{ repetitions: 12, steps: [{ temperature_C: 42 }, { temperature_C: 16 }] }] } }, parts: [{ stock: { value: 200, unit: 'nM' }, dilution: { factor: 4, preparation_volume_uL: 40 }, phosphorylation: 'unphosphorylated', in_mastermix: true }], reagents: [{ formulation: 'Custom buffer pH 7.9 + 2 mM ATP', final_multiple: 0.75 }] }); evidence('mounted-reaction', latest.science);
  });
});

describe('Result viewer and async ownership', () => {
  it('renders the published native score as a reference estimate and preserves an unavailable outcome', async () => {
    const score = scoreFixture as FidelityResult;
    await mount(<GoldenGateFidelityResults result={score} />);
    expect(host.textContent).toContain('0.8092502583687903');
    expect(host.textContent).toContain('42C/16C');
    expect(host.textContent).toContain('T4 buffer');
    expect(host.textContent).toContain('not yield');
    await act(async () => root.render(<GoldenGateFidelityResults result={{ ...score, status: 'unavailable', f_set: null, reasons: ['repeated_junction_classes'], repeated_classes: ['AATG'] }} />));
    expect(host.textContent).toContain('Unavailable / unscored');
    expect(host.textContent).toContain('repeated_junction_classes');
    expect(host.textContent).toContain('AATG');
  });
  it('shows bounded-search scope and returns the exact selected native candidate without inventing IDs', async () => {
    const selected = vi.fn();
    await mount(<GoldenGateSearchResults result={searchFixture as SearchResult} selectedIndex={null} onSelect={selected} />);
    expect(host.textContent).toContain('incomplete / bounded');
    expect(host.textContent).toContain('optimality not proven');
    await click('Select search candidate 2');
    expect(selected).toHaveBeenCalledWith(1, searchFixture.solutions[1]);
  });
  it('shows origin-wrapping native window choices and does not label segment lengths as amplicons', async () => {
    const selected = vi.fn();
    await mount(<GoldenGateSearchResults result={windowFixture as SearchResult} selectedIndex={null} onSelect={selected} />);
    expect(host.textContent).toContain('9: TAC; 4: TGC');
    expect(host.textContent).toContain('not prepared amplicon lengths');
    await click('Select search candidate 1'); expect(selected).toHaveBeenCalledWith(0, windowFixture.solutions[0]);
  });
  it('lazily composes science sections without edits or default hydration on reveal', async () => {
    const initial = draft(); const s = initial.science; const changeScience = vi.fn();
    await mount(<GoldenGateScienceEditor value={s} onChange={changeScience} core={initial.core} datasets={[]} siteChoices={s.domestication[0].settings.unwanted_sites} templates={{ overhang_search: s.overhang_search!, window_search: s.window_search!, domestication: s.domestication[0].settings, cds: s.domestication[0].settings.cds[0], reaction: s.reaction!, reactionPart: reactionTemplates.part, reagent: reactionTemplates.reagent, cycling: reactionTemplates.cycling }} />);
    expect(host.querySelector('[aria-label="Reaction worksheet settings"]')).toBeNull();
    await change('Scientific controls', 'reaction'); await settle();
    expect(field('Reagent 1 formulation').value).toBe('Requested CutSmart + ATP/DTT');
    expect(changeScience).not.toHaveBeenCalled();
    await change('Reagent 1 formulation', 'Operator-exact buffer');
    expect(changeScience.mock.calls[0][0].reaction.reagents[0].formulation).toBe('Operator-exact buffer');
  });
  it('does not expose an old save acknowledgement after restarting an identical draft', async () => {
    const pending = deferred<{ label: string }>(); const saved = vi.fn();
    await mount(<Harness onSave={() => pending.promise} onSaved={saved} />);
    await click('Design explicit parts'); await settle(); await click('Save selected design');
    await click('Design explicit parts'); await settle();
    await act(async () => pending.resolve({ label: 'earlier receipt' }));
    expect(saved).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('earlier receipt');
  });
  it('loads exact native product with mapped features, primers and all digest background without eager optional views', async () => {
    const load = vi.fn(); const view = vi.fn(() => <div>Shared sequence viewer</div>); await mount(<GoldenGateDesignResults result={nativeResult} selectedSolutionId={nativeResult.selected_solution_id} onSelect={() => {}} onLoadProduct={load} renderSequence={view} />);
    expect(view).not.toHaveBeenCalled(); expect(host.textContent).toContain('insert CDS'); await click('Load selected product with annotations'); expect(load).toHaveBeenCalledWith(nativeResult.solutions[0]); await click('Show sequence view'); expect(view).toHaveBeenCalledTimes(1); await click('Show primers and PCR verification'); expect((field(`${nativeResult.primers[0].id} full primer`) as HTMLTextAreaElement).value).toBe(nativeResult.primers[0].full_sequence); expect(host.textContent).toContain(nativeResult.primers[0].annealing_sequence); await click('Show all digest outcomes and background'); expect(host.textContent).toContain('Reactive background remains'); expect(host.textContent).toContain('physical 5′→3′');
  });
  it('allows fixed-selection Save unscored and suppresses a late Save after editing without aborting persistence', async () => {
    const pending = deferred<{ label: string }>(); const save = vi.fn(() => pending.promise); const saved = vi.fn(); await mount(<Harness onSave={save} onSaved={saved} />); await click('Design explicit parts'); await settle(); expect(host.textContent).toContain('Unavailable / unscored'); await click('Save selected design'); expect(save).toHaveBeenCalledWith(core, draft().science, nativeResult.selected_solution_id); await change('insert name', 'new owner'); await act(async () => pending.resolve({ label: 'old saved ACK' })); expect(host.textContent).not.toContain('old saved ACK'); expect(saved).not.toHaveBeenCalled(); expect(save.mock.calls[0]).toHaveLength(3);
  });
  it('aborts superseded preview and ignores late success even when the callback ignores abort', async () => {
    const first = deferred<{ core: GoldenGateDesignResult }>(); const second = deferred<{ core: GoldenGateDesignResult }>(); const design = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise); await mount(<Harness onDesign={design} />); await click('Design explicit parts'); await change('insert name', 'current owner'); expect(design.mock.calls[0][2].aborted).toBe(true); await click('Design explicit parts'); await act(async () => second.resolve({ core: { ...nativeResult, diagnostics: ['current result'] } })); await settle(); await act(async () => first.resolve({ core: { ...nativeResult, diagnostics: ['stale result'] } })); expect(host.textContent).toContain('current result'); expect(host.textContent).not.toContain('stale result');
  });
  it('ignores cancelled errors and aborts on unmount without a background design call', async () => {
    const pending = deferred<{ core: GoldenGateDesignResult }>(); const design = vi.fn<GoldenGateDesignEditorProps['onDesign']>(() => pending.promise); await mount(<Harness onDesign={design} />); await click('Design explicit parts'); await click('Cancel preview'); expect(design.mock.calls[0][2].aborted).toBe(true); await act(async () => pending.reject(new Error('stale failure'))); expect(host.textContent).not.toContain('stale failure'); await act(async () => root.unmount()); expect(design).toHaveBeenCalledTimes(1);
  });
  it('keeps candidate selection and annotations explicit and invalidates late save when the selection changes', async () => {
    const result = structuredClone(nativeResult); result.solutions.push({ ...structuredClone(result.solutions[0]), id: 'alternative' }); const pending = deferred<{ label: string }>(); const saved = vi.fn(); await mount(<Harness onDesign={async () => ({ core: result })} onSave={() => pending.promise} onSaved={saved} />); await click('Design explicit parts'); await settle(); await click('Save selected design'); await change('Selected candidate', 'alternative'); await act(async () => pending.resolve({ label: 'wrong selection ACK' })); expect(saved).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('wrong selection ACK'); expect((field('Selected candidate') as HTMLSelectElement).value).toBe('alternative');
  });
  it('requires explicit proposal acceptance and displays unknown reaction quantities honestly', async () => {
    const proposal: DomesticationResult = { settings: draft().science.domestication[0].settings, status: 'proposal', proposed_sequence: 'ACGT', edits: [{ position: 1, original: 'A', proposed: 'C', affected_feature_ids: ['cds'] }], translations: [], candidates_evaluated: 3, search_complete: false, minimal_edits_proven: true, diagnostics: ['Equivalent proposals not enumerated'], engine: 'DNA Chisel 3.2.16' }; const accept = vi.fn();
    const worksheet: ReactionResult = { request: draft().science.reaction!, rows: [], known_transfer_subtotal_uL: 2, total_transfer_uL: null, water_exact_uL: null, mastermix_total_uL: null, diagnostics: [{ code: 'unknown_dna_volume', component_id: 'insert', message: 'Stock unknown' }], mass_basis_description: 'Approximate dsDNA basis' };
    await mount(<><GoldenGateDomesticationResults result={proposal} onAccept={accept} /><GoldenGateReactionResults result={worksheet} /></>); expect(accept).not.toHaveBeenCalled(); expect(host.textContent).toContain('full total: unknown'); expect(host.textContent).toContain('Stock unknown'); await click('Accept this proposal as derived material'); expect(accept).toHaveBeenCalledWith(proposal);
  });
});
