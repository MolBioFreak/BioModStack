import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { NativeBinderGeneration, NativeSetting } from '../../src/components/NativeBinderGeneration';
import { RFantibodyGeneration } from '../../src/components/RFantibodyGeneration';
import { BinderRoundSettings } from '../../src/components/BinderRoundSettings';
import { api } from '../../src/lib/api';
import { hydrateBinderRound, roundParameterIsBound, type BinderRoundDraft } from '../../src/lib/binderRound';
import { binderRoundModes, roundCatalogFields } from '../../src/lib/binderRoundControls';
import { nativeParameterDisplay, type NativeBinderParameter } from '../../src/lib/nativeBinderAuthoring';
import { PRESETS } from '../../src/components/qualitySettingsLogic';

// Only transport/WebGL is mocked. The real source acquisition and typed controls stay mounted.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: () => <output data-webgl-fixture /> }));
const nativeInventories: Record<string, { parameters: NativeBinderParameter[] }> = process.env.BMS_NATIVE_GENERATION_INVENTORY ? JSON.parse(readFileSync(process.env.BMS_NATIVE_GENERATION_INVENTORY, 'utf8')) : {};
const modelIds = ['proteinmpnn', 'fampnn', 'caliby_binder', 'protenix', 'boltz2', 'esmfold2'];
const catalogs = process.env.BMS_ROUND_CATALOG_INVENTORY
    ? JSON.parse(readFileSync(process.env.BMS_ROUND_CATALOG_INVENTORY, 'utf8'))
    : JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['proteinmpnn','fampnn','caliby_binder','protenix','boltz2','esmfold2']]))"], { encoding: 'utf8' }));
let root: Root | undefined; let client: QueryClient;
async function mount(node: React.ReactNode) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
    await settle();
}
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => {
    vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
        const model = catalogs.find((item: any) => url === `/api/models/${item.id}`);
        if (model) return { data: structuredClone(model) };
        if (url.includes('/input-presets')) return { data: [] };
        return { data: [] };
    });
});
const input = (label: string) => document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
async function edit(label: string, value: string) { const node = input(label); expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function choose(label: string, value: string) { const node = document.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!; await act(async () => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); }
async function click(text: string) { const node = [...document.querySelectorAll('button')].find(node => node.textContent?.trim() === text)!; expect(node, text).toBeTruthy(); await act(async () => node.click()); }

it('independent RF sampling shows precise saved noise and synchronizes slider/number without downstream flags', async () => {
    let saved: any;
    function Form({ initial = { ...PRESETS.balanced, rfantibody_noise_scale_ca: 0.25 } }: { initial?: any }) {
        const [settings, setSettings] = useState(initial); saved = settings;
        return <RFantibodyGeneration settings={settings} onSettingsChange={setSettings} />;
    }
    await mount(<Form />);
    for (const key of ['diffusion_steps', 'guide_scale', 'noise_scale_ca', 'noise_scale_frame']) expect(input(`rfantibody_${key}`)).toBeTruthy();
    expect(input('rfantibody_noise_scale_ca').value).toBe('0.25');
    await edit('rfantibody_noise_scale_ca', '0.713456');
    expect(saved.rfantibody_noise_scale_ca).toBe(0.713456);
    await edit('rfantibody_noise_scale_frame slider', '1.4'); expect(input('rfantibody_noise_scale_frame').value).toBe('1.4');
    expect(input('rfantibody_diffusion_steps').step).toBe('1');
    expect(input('rfantibody_guide_scale').step).toBe('any');
    const reopened = JSON.parse(JSON.stringify(saved)); await unmount(); await mount(<Form initial={reopened} />);
    expect(input('rfantibody_noise_scale_ca').value).toBe('0.713456');
    await edit('rfantibody_noise_scale_ca', ''); expect(saved.rfantibody_noise_scale_ca).toBe('');
    const cleared = JSON.parse(JSON.stringify(saved)); await unmount(); await mount(<Form initial={cleared} />);
    expect(input('rfantibody_noise_scale_ca').value).toBe('');
});

it('native alpha precision, unavailable threshold inspection/explicit clear and scaffold union stay lossless', async () => {
    let saved: any;
    const parameters: NativeBinderParameter[] = [
        { name: 'alpha', type: 'number', minimum: 0, maximum: 1, step: 0.01, ui_control: 'slider', label: 'Selection diversity' },
        { name: 'min_plddt', type: 'number', default: null, read_only: true, unavailable_reason: 'Native producer does not support a pLDDT threshold.' },
        { name: 'nanobody_scaffold_specs', type: 'string', accepted_types: ['string', 'array'] },
    ];
    function Form({ initial = { alpha: 0.01234567, min_plddt: 88, nanobody_scaffold_specs: ['scaffold:a', 'scaffold:b'] } }: { initial?: any }) {
        const [values, setValues] = useState(initial); saved = values;
        return <>{parameters.map(parameter => <NativeSetting key={parameter.name} parameter={parameter} values={values} onPatch={patch => setValues({ ...values, ...patch })} chains={[]} />)}</>;
    }
    await mount(<Form />); expect(input('alpha').value).toBe('0.01234567'); expect(input('alpha slider').value).toBe('0.01234567');
    expect(input('alpha').step).toBe('any'); expect(input('min_plddt')).toBeNull(); expect(document.body.textContent).toContain('Historical requested value: 88');
    expect(saved.min_plddt).toBe(88); expect(input('nanobody_scaffold_specs 1').value).toBe('scaffold:a');
    expect(document.body.textContent).not.toContain('Use text representation');
    await edit('alpha slider', '0.2'); expect(input('alpha').value).toBe('0.2');
    await edit('alpha', '0.333333333'); await edit('nanobody_scaffold_specs 2', 'scaffold:exact');
    await click('Clear incompatible min plddt'); expect(saved.min_plddt).toBeNull();
    const reopened = JSON.parse(JSON.stringify(saved)); await unmount(); await mount(<Form initial={reopened} />);
    expect(saved).toEqual(reopened); expect(input('alpha').value).toBe('0.333333333'); expect(input('nanobody_scaffold_specs 2').value).toBe('scaffold:exact');
    await unmount(); await mount(<Form initial={{ alpha: 0, min_plddt: null, nanobody_scaffold_specs: 'native,syntax:unchanged' }} />);
    expect(input('nanobody_scaffold_specs').value).toBe('native,syntax:unchanged'); expect(saved.nanobody_scaffold_specs).toBe('native,syntax:unchanged');
});

it('conditional requiredness/null applicability is display-only and respects native source alternatives', async () => {
    let saved: any;
    const parameter: NativeBinderParameter = { name: 'binder_chain', type: 'string', default: null, nullable: true, required_when: { field: 'input_csv', operator: 'falsy' } };
    function Form() { const [values, setValues] = useState<any>({ binder_chain: null, input_csv: '' }); saved = values;
        return <><NativeSetting parameter={parameter} values={values} onPatch={patch => setValues({ ...values, ...patch })} chains={[]} /><button onClick={() => setValues({ ...values, input_csv: 'targets.csv' })}>Choose CSV alternative</button></>; }
    await mount(<Form />); expect(document.body.textContent).toContain('(required)'); expect(document.body.textContent).not.toContain('Use native null'); expect(saved.binder_chain).toBeNull();
    await click('Choose CSV alternative'); expect(document.body.textContent).not.toContain('(required)'); expect(document.body.textContent).toContain('Use native null'); expect(saved.binder_chain).toBeNull();
    expect(nativeParameterDisplay({ name: 'light_chain', type: 'string', display_applicable: false }, {})).toMatchObject({ applicable: false });
});

it('Generation slot and populated source owner remain mounted across actual section navigation', async () => {
    let mounts = 0; let latest: any;
    function Slot() { const [value, setValue] = useState('round draft'); React.useEffect(() => { mounts++; }, []); return <input aria-label="round slot value" value={value} onChange={event => setValue(event.target.value)} />; }
    function Form() { const [values, setValues] = useState<any>({ target_pdb: '', num_designs: 4 }); latest = values;
        return <NativeBinderGeneration model="boltzgen" mode="protein_binder" parameters={[{ name: 'target_pdb', type: 'file' }, { name: 'num_designs', type: 'integer' }]} values={values} onPatch={patch => setValues({ ...values, ...patch })} generationSlot={<Slot />} />; }
    await mount(<Form />); const source = document.querySelector('[aria-label="Target source"]'); const slot = input('round slot value');
    expect(slot.closest('[hidden]')).not.toBeNull(); await edit('target_pdb', 'inputs/operator-source.pdb');
    await click('Generation'); expect(slot.closest('[hidden]')).toBeNull(); await edit('round slot value', 'retained exact');
    await click('Sources'); expect(document.querySelector('[aria-label="Target source"]')).toBe(source); expect(input('target_pdb').value).toBe('inputs/operator-source.pdb');
    await click('Generation'); expect(input('round slot value')).toBe(slot); expect(slot.value).toBe('retained exact'); expect(mounts).toBe(1); expect(latest.num_designs).toBe(4);
});

it('all six round modes retain applicable metadata fields with principal sampling and MSA before collapsed expert groups', async () => {
    let latest: BinderRoundDraft;
    function Form() { const [values, setValues] = useState(hydrateBinderRound()); latest = values; return <BinderRoundSettings values={values} onChange={setValues} />; }
    await mount(<Form />); await vi.waitFor(async () => { await settle(); expect(input('protenix_n_sample')).toBeTruthy(); });
    expect(binderRoundModes.esmfold2).toBe('predict');
    for (const id of modelIds) {
        const designer = ['proteinmpnn', 'fampnn', 'caliby_binder'].includes(id);
        await choose(designer ? 'Round sequence designer' : 'Round complex validator', id);
        const group = document.querySelector(`[aria-label="${designer ? 'Round sequence design' : 'Round complex prediction'}"]`)!;
        const fields = roundCatalogFields(catalogs.find((item: any) => item.id === id));
        for (const field of fields.filter(field => !roundParameterIsBound(field.name))) {
            expect(group.querySelector(`[data-native-setting="${field.name}"], [data-round-fixed="${field.name}"]`), `${id}:${field.name}`).toBeTruthy();
        }
        expect([...group.querySelectorAll<HTMLDetailsElement>('details[data-round-group]')].every(details => !details.open)).toBe(true);
        expect(group.textContent).not.toContain('Full settings');
        const count = designer ? id === 'caliby_binder' ? 'caliby_num_seqs_per_pdb' : 'seqs_per_design' : id === 'protenix' ? 'protenix_n_sample' : id === 'boltz2' ? 'boltz_diffusion_samples' : 'num_diffusion_samples';
        expect(group.querySelector(`[data-round-primary] [aria-label="${count}"]`), count).toBeTruthy();
    }
    await choose('Round complex validator', 'protenix');
    expect(document.querySelector('[data-round-msa-summary]')?.textContent).toContain('ColabFold');
    await choose('msa_provider', 'neurosnap_api');
    expect(document.querySelector('[data-round-group="Inactive ColabFold MSA options (retained)"]')).toBeTruthy();
    const providerDraft = structuredClone(latest!.binder_round.prediction.params);
    await act(async () => input('protenix_use_msa').click());
    expect(document.querySelector('[data-round-msa-summary]')?.textContent).toContain('MSA off');
    expect(latest!.binder_round.prediction.params).toMatchObject({ ...providerDraft, protenix_use_msa: false });
    await act(async () => input('Automatic blind complex prediction').click());
    expect(document.querySelector<HTMLDetailsElement>('[data-round-settings]')!.open).toBe(false);
    expect(input('Round complex validator')).toBeNull(); // model selector is a select, not a duplicate scalar input.
    const saved = JSON.parse(JSON.stringify(latest!)); await unmount();
    function Reopened() { const [values, setValues] = useState(saved); latest = values; return <BinderRoundSettings values={values} onChange={setValues} />; }
    await mount(<Reopened />); await settle(); expect(latest!.binder_round).toEqual(saved.binder_round);
});

it('serialized slider metadata is consumed without inventing bounds or duplicating native scalar controls', async () => {
    let saved: any;
    function Form() { const [values, setValues] = useState({ bounded: 0, unbounded: 0.123456789 }); saved = values; return <>{[
        { name: 'bounded', type: 'number', minimum: 0, maximum: 1, step: 0.01, ui_control: 'slider', units: 'fraction' },
        { name: 'unbounded', type: 'number', ui_control: 'slider' },
    ].map(parameter => <NativeSetting key={parameter.name} parameter={parameter} values={values} onPatch={patch => setValues({ ...values, ...patch })} chains={[]} />)}</>; }
    await mount(<Form />); expect(input('bounded slider').value).toBe('0'); expect(input('unbounded slider')).toBeNull();
    await edit('bounded', '0.123456789'); expect(input('bounded slider').value).toBe('0.123456789'); expect(saved.bounded).toBe(0.123456789);
    expect(document.querySelectorAll('input[type="number"][aria-label="bounded"]')).toHaveLength(1);
});

for (const [identity, inventory] of Object.entries(nativeInventories)) {
    it(`model-owned discovery ${identity}: required help and native sampling consume exact saved values`, async () => {
        const [model, mode] = identity.split(':'); let saved: Record<string, any>;
        const initial = model === 'ppiflow'
            ? { target_pdb: '', input_csv: '', binder_chain: null, specified_hotspots: null }
            : { boltzgen_alpha: 0.01234567, boltzgen_min_plddt: 88, ...(mode === 'nanobody_binder' ? { boltzgen_nanobody_scaffold_specs: ['native-spec-one', 'native-spec-two'] } : {}) };
        function Form() { const [values, setValues] = useState<any>(initial); saved = values; return <NativeBinderGeneration model={model as 'ppiflow' | 'boltzgen'} mode={mode} parameters={inventory.parameters} values={values} onPatch={patch => setValues({ ...values, ...patch })} />; }
        await mount(<Form />); expect(saved!).toEqual(initial); // no display projection injected into requests
        for (const parameter of inventory.parameters) expect(document.querySelector(`[data-native-setting="${parameter.name}"]`), parameter.name).toBeTruthy();
        if (model === 'ppiflow') {
            const source = document.querySelector('[data-native-setting="target_pdb"]')!;
            expect(source.textContent).toContain('(required)');
            if (mode === 'protein_binder') {
                expect(source.textContent).toContain('exactly one target PDB or input CSV');
                await edit('target_pdb', 'inputs/operator-target.pdb');
                expect(document.querySelector('[data-native-setting="binder_chain"]')!.textContent).toContain('(required)');
                expect(document.querySelector('[data-native-setting="binder_chain"]')!.textContent).not.toContain('Use native null');
                await edit('target_pdb', ''); await edit('input_csv', 'inputs/operator-targets.csv');
                expect(source.textContent).not.toContain('(required)');
                expect(document.querySelector('[data-native-setting="binder_chain"]')!.textContent).not.toContain('(required)');
                await click('Design');
                const hotspots = document.querySelector('[data-native-setting="specified_hotspots"]')!;
                expect(hotspots.textContent).not.toContain('(required)');
                await edit('sample_hotspot_rate_min', '0.3');
                expect(hotspots.textContent).toContain('(required)'); expect(hotspots.textContent).not.toContain('Use native null');
                expect(saved!.specified_hotspots).toBeNull(); // show native requirement, do not normalize or refuse the draft
            } else {
                for (const key of ['framework_pdb', 'antigen_chain', 'heavy_chain', 'specified_hotspots']) expect(document.querySelector(`[data-native-setting="${key}"]`)!.textContent).toContain('(required)');
                if (mode === 'antibody_binder') expect(document.querySelector('[data-native-setting="light_chain"]')!.textContent).toContain('(required)');
                else if (document.querySelector('[data-native-setting="light_chain"]')) expect(document.querySelector('[data-native-setting="light_chain"]')!.getAttribute('data-native-applicable')).toBe('false');
            }
        } else {
            await click('Native selection');
            expect(input('boltzgen_alpha').value).toBe('0.01234567'); expect(input('boltzgen_alpha slider')).toBeTruthy();
            expect(input('boltzgen_min_plddt')).toBeNull(); expect(document.querySelector('[data-native-setting="boltzgen_min_plddt"]')!.textContent).toContain('88');
            expect(saved!.boltzgen_min_plddt).toBe(88);
            if (mode === 'nanobody_binder') { await click('Binder & protocol'); expect(input('boltzgen_nanobody_scaffold_specs 1').value).toBe('native-spec-one'); }
        }
    });
}
