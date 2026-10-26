import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { afterEach, expect, it, vi } from 'vitest';
import { DiscoJobsInput } from '../../src/components/DiscoJobsInput';
import { api } from '../../src/lib/api';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_payload: any) => ({ data: {} })) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), submitJob: mocks.submit, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ workflowRequest }: any) => <output data-request>{JSON.stringify(workflowRequest)}</output> }));
vi.mock('../../src/components/ModelDocumentationLinks', () => ({ ModelDocumentationLinks: () => null }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/LaProteinaMotifInput', () => ({ LaProteinaMotifInput: () => null }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => null }));
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
const inventories = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', `import json
from scripts.lib.rfd3_scientific_settings import scientific_inventory
from scripts.lib.laproteina_contract import settings_schema, PROFILES, resolve_profile
from scripts.lib.disco_contract import NativeSettings
from pydantic import TypeAdapter
print(json.dumps({'rfd3':scientific_inventory(),'laproteina':{'parameter_schema':settings_schema(),'profiles':{k:resolve_profile(k) for k in PROFILES}},'disco':{'parameter_schema':TypeAdapter(NativeSettings).json_schema()}}))`], { cwd: '../..', encoding: 'utf8' }));
let root: Root; let client: QueryClient; const previous = api.defaults.adapter;
let requests: string[] = []; let unexpected: string[] = [];
let scratch = '';
function bridge(payload: any) { return JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['tests/disco_import_bridge.py'], { cwd: '../api', env: { ...process.env, PYTHONPATH: '.' }, input: JSON.stringify({ root: scratch, ...payload }), encoding: 'utf8' })); }
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); if (scratch) rmSync(scratch, { recursive: true, force: true }); scratch = ''; document.body.replaceChildren(); api.defaults.adapter = previous; vi.restoreAllMocks(); expect(unexpected).toEqual([]); requests = []; unexpected = []; });
async function render(node: React.ReactNode) {
    api.defaults.adapter = async config => { const name = String(config.url); requests.push(`${name}:${config.params?.generator}`); if (name.startsWith('/files/disco-jobs/')) { const result = bridge({ operation: 'import', path: decodeURIComponent(name.split('/disco-jobs/')[1]) }); if (result.status !== 200) throw Error(JSON.stringify(result)); return { data: result.data, config, status: 200, statusText: 'OK', headers: {} }; } if (!name.endsWith('/generation-settings')) { unexpected.push(name); throw Error(name); } return { data: inventories[config.params.generator], config, status: 200, statusText: 'OK', headers: {} }; };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function click(text: string) { const item = [...document.querySelectorAll('button,summary')].find(x => x.textContent === text); expect(item, text).toBeTruthy(); await act(async () => (item as HTMLElement).click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function edit(label: string, value: string) { const item = document.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(item).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(item), 'value')!.set!.call(item, value); item.dispatchEvent(new Event(item.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }

it('managed import/edit/save/cold reopen reaches real normalizer and native prep', async () => {
    scratch = mkdtempSync(join(process.env.TMPDIR!, 'disco-import-'));
    mkdirSync(join(scratch, 'inputs/native/ligands'), { recursive: true });
    const ligand = join(scratch, 'inputs/native/ligands/context.sdf');
    writeFileSync(ligand, 'retained ligand bytes\n');
    const original = JSON.stringify([{ name: 'first', future_native: { zero: 0, flag: false }, sequences: [
        { proteinChain: { sequence: 'A--C', count: 1, future: 'keep' } },
        { ligand: { ligand: 'FILE_ligands/context.sdf', count: 1 } },
        { dnaSequence: { sequence: 'ACGT', count: 1 } },
        { rnaSequence: { sequence: 'ACGU', count: 1 } }],
        covalent_bonds: [{ left_entity: 1, left_position: 1, left_atom: 'CA', right_entity: 2, right_position: 1, right_atom: 'C1', future: null }] },
        { name: 'second', sequences: [{ ion: { ion: 'MG', count: 2 } }] }], null, 2);
    const source = join(scratch, 'inputs/native/jobs.json'); writeFileSync(source, original);
    const draft = vi.fn();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'disco', design_task: 'custom_json', disco_input_json_path: 'inputs/native/jobs.json' }} />);
    expect(requests.some(x => x.startsWith('/files/'))).toBe(false);
    expect(document.body.textContent).toContain('Active input: Native JSON file');
    await click('Import into editor');
    expect(document.querySelector('[aria-label="DISCO job 1 name"]'), document.querySelector('[role="alert"]')?.textContent ?? document.body.textContent).toBeTruthy();
    await edit('DISCO job 1 name', 'edited');
    await edit('Job 1 entity 3 sequence', 'AGGT');
    const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
    expect(saved.pcad_disco_jobs_json[0].sequences[1].ligand.ligand).toBe('FILE_ligands/context.sdf');
    expect(saved.pcad_disco_jobs_source_path).toBe(source);
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={saved} />);
    // Selecting a different fallback must not re-root imported relative ligands.
    writeFileSync(join(scratch, 'inputs/other.json'), '[]');
    const pathInput = [...document.querySelectorAll('label')].find(label => label.textContent === 'Native input JSON path')!.querySelector('input')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(pathInput, 'inputs/other.json'); pathInput.dispatchEvent(new Event('input', { bubbles: true })); });
    const request = JSON.parse(document.querySelector('[data-request]')!.textContent!);
    expect(request.params.disco_input_json_path).toBe('inputs/other.json');
    expect(request.params.pcad_disco_jobs_source_path).toBe(source);
    expect(request.params.pcad_disco_jobs_json).toEqual(saved.pcad_disco_jobs_json);
    const prepared = bridge({ operation: 'prepare', request });
    const expected = JSON.parse(JSON.stringify(saved.pcad_disco_jobs_json));
    expected[0].sequences[1].ligand.ligand = `FILE_${ligand}`;
    expect(prepared.native).toEqual(expected);
    expect(prepared.params.pcad_disco_jobs_source_path).toBe(source);
    expect(prepared.refs.map((ref: any) => ref.source_path)).toContain(ligand);
    expect(prepared.original).toBe(original); expect(readFileSync(source, 'utf8')).toBe(original);
    await click('Clear explicit jobs override');
    expect(document.body.textContent).toContain('Active input: Native JSON file');
    expect(JSON.parse(document.querySelector('[data-request]')!.textContent!).params).not.toHaveProperty('pcad_disco_jobs_source_path');
}, 30000);

it.each([
    [{}, 'motif_idx_aa', true],
    [{ laproteina_preset: 'ucond_tri' }, 'ucond_tri', true],
    [{ laproteina_preset: 'motif_idx_aa', laproteina_contig_string: 'A9-10', pcad_laproteina_settings_json: { contig_string: 'A1-2' } }, 'motif_idx_aa', true],
    [{ laproteina_preset: 'motif_idx_aa', laproteina_contig_string: 'A1-2', pcad_laproteina_settings_json: { contig_string: '' } }, 'motif_idx_aa', false],
])('La parent keeps literal profile and effective sparse prerequisite %j', async (overrides, preset, active) => {
    const draft = vi.fn();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'laproteina', design_task: 'motif_scaffolding', laproteina_motif_pdb: '/source.pdb', pcad_laproteina_settings_json: { contig_string: 'A1-2' }, ...overrides }} />);
    const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
    expect(saved.laproteina_preset).toBe(preset);
    const request = JSON.parse(document.querySelector('[data-request]')!.textContent!);
    expect(Boolean(request)).toBe(active);
    if (active) expect(request.params.pcad_laproteina_settings_json).toEqual(saved.pcad_laproteina_settings_json);
    expect(document.body.textContent).not.toContain('native fallback');
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={saved} />);
    const cold = JSON.parse(document.querySelector('[data-request]')!.textContent!);
    expect(Boolean(cold)).toBe(active);
    if (active) expect(cold.params.laproteina_preset).toBe(preset);
});

it.each(['cancel', 'path', 'edit', 'unmount', 'malformed'])('%s during import preserves current input', async action => {
    let saved: any = [{ name: 'original', sequences: [] }];
    let setPath: (path: string) => void = () => {};
    function Harness() { const [value, setValue] = useState(saved); const [path, updatePath] = useState('/selected.json'); setPath = updatePath; saved = value; return <DiscoJobsInput value={value} path={path} onChange={setValue} />; }
    await render(<Harness />);
    let finish: (value: any) => void = () => {};
    api.defaults.adapter = config => new Promise(resolve => { finish = data => resolve({ data, config, status: 200, statusText: 'OK', headers: {} }); });
    await click('Import into editor');
    if (action === 'cancel') await click('Cancel import');
    if (action === 'path') await act(async () => setPath('/another.json'));
    if (action === 'edit') await edit('DISCO job 1 name', 'new edit');
    if (action === 'unmount') await act(async () => root.unmount());
    await act(async () => finish({ text: action === 'malformed' ? '{bad' : '[{"name":"stale","sequences":[]}]', source_path: '/selected.json' }));
    expect(saved).toEqual([{ name: action === 'edit' ? 'new edit' : 'original', sequences: [] }]);
    if (action === 'malformed') expect(document.body.textContent).toContain('Import failed; existing input unchanged');
});
