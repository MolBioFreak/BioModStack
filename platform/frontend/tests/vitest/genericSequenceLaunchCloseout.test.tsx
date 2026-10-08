import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Catalog fixture copied from the canonical API model YAMLs. Only discovery and
// unrelated modal/integration chrome are mocked; the form, placement/policy,
// analysis controls, submission closure and Axios serialization remain real.
const fixtures = vi.hoisted(() => ({
    models: [
    {
        "id": "fampnn",
        "name": "Full-Atom MPNN",
        "category": "sequence_design",
        "modes": [
            {
                "id": "design",
                "name": "Full-Atom Sequence Design",
                "description": "Design sequences from a PDB backbone structure",
                "params": [
                    "input_pdb",
                    "design_chain",
                    "target_chain",
                    "fampnn_temperature",
                    "fampnn_exclude_cys",
                    "fampnn_fix_target_sidechains",
                    "fampnn_psce_threshold",
                    "fampnn_seq_only",
                    "fampnn_num_steps",
                    "fampnn_batch_size",
                    "fampnn_repack_last",
                    "fampnn_extra_config"
                ]
            },
            {
                "id": "fixed_backbone",
                "name": "Fixed Backbone Redesign",
                "description": "Redesign sequence while keeping specific residues fixed",
                "params": [
                    "input_pdb",
                    "fixed_positions",
                    "fampnn_temperature",
                    "fampnn_exclude_cys",
                    "fampnn_psce_threshold",
                    "fampnn_seq_only",
                    "fampnn_num_steps",
                    "fampnn_extra_config"
                ]
            },
            {
                "id": "binder_design",
                "name": "Binder Sequence Design",
                "description": "Design binder sequences against a fixed target",
                "params": [
                    "input_pdb",
                    "design_chain",
                    "target_chain",
                    "fampnn_temperature",
                    "fampnn_exclude_cys",
                    "fampnn_fix_target_sidechains",
                    "fampnn_psce_threshold",
                    "fampnn_seq_only",
                    "fampnn_num_steps",
                    "fampnn_batch_size",
                    "fampnn_repack_last",
                    "fampnn_extra_config"
                ]
            }
        ],
        "params": [
            {
                "name": "input_pdb",
                "type": "file",
                "description": "Input PDB structure for sequence design",
                "required": true,
                "file_type": "pdb",
                "preset_type": "pdb"
            },
            {
                "name": "design_chain",
                "type": "string",
                "description": "Chain ID(s) to redesign (e.g., 'A' or 'A,B')",
                "required": false,
                "default": "A"
            },
            {
                "name": "target_chain",
                "type": "string",
                "description": "Target chain ID(s) to keep fixed (e.g., 'B' for binder design)",
                "required": false
            },
            {
                "name": "fixed_positions",
                "type": "string",
                "description": "Residue positions to keep fixed (e.g., 'A:1-10,A:50-60' or 'A:1,A:5,A:10')",
                "required": false
            },
            {
                "name": "fampnn_temperature",
                "type": "number",
                "description": "Sampling temperature (0.1=conservative, 1.0=diverse, 2.0=creative)",
                "required": false,
                "default": 0.1,
                "minimum": 0.01,
                "maximum": 2.0
            },
            {
                "name": "fampnn_exclude_cys",
                "type": "boolean",
                "description": "Exclude cysteine residues from design (prevents disulfide issues)",
                "required": false,
                "default": true
            },
            {
                "name": "fampnn_fix_target_sidechains",
                "type": "boolean",
                "description": "Fix target sidechain positions during binder design",
                "required": false,
                "default": false
            },
            {
                "name": "fampnn_constraint_mode",
                "type": "string",
                "description": "Constraint mode: 'generic' (no fixed residues) or 'antibody' (CDR-aware constraints)",
                "required": false,
                "default": "generic",
                "enum": [
                    "generic",
                    "antibody"
                ]
            },
            {
                "name": "fampnn_psce_threshold",
                "type": "number",
                "description": "PSCE confidence threshold for sidechain filtering (0.0-1.0)",
                "required": false,
                "default": 0.3,
                "minimum": 0.0,
                "maximum": 1.0
            },
            {
                "name": "fampnn_mutation_top_n",
                "type": "integer",
                "description": "Top FA-MPNN model-favored single-substitution log-odds deltas to keep per sampled design",
                "required": false,
                "default": 25,
                "minimum": 0,
                "maximum": 500
            },
            {
                "name": "fampnn_mutation_min_log_odds_delta",
                "type": "number",
                "description": "Minimum log(P_alt)-log(P_sampled) delta for reporting FA-MPNN model-favored single substitutions",
                "required": false,
                "default": 0.0,
                "minimum": -20.0,
                "maximum": 20.0
            },
            {
                "name": "fampnn_seq_only",
                "type": "boolean",
                "description": "Sequence-only mode (skip sidechain diffusion, faster but less accurate)",
                "required": false,
                "default": false
            },
            {
                "name": "fampnn_num_steps",
                "type": "integer",
                "description": "Number of denoising timesteps (higher = better quality, slower)",
                "required": false,
                "default": 100,
                "minimum": 10,
                "maximum": 500
            },
            {
                "name": "fampnn_batch_size",
                "type": "integer",
                "description": "Batch size for inference (reduce if OOM errors)",
                "required": false,
                "default": 16,
                "minimum": 1,
                "maximum": 64
            },
            {
                "name": "fampnn_repack_last",
                "type": "boolean",
                "description": "Repack sidechains after final denoising step",
                "required": false,
                "default": true
            },
            {
                "name": "fampnn_extra_config",
                "type": "string",
                "description": "Raw config passthrough for debugging (e.g., 'seed=42 scn_diffusion.num_steps=50')",
                "required": false,
                "hidden": false
            }
        ]
    },
    {
        "id": "proteinmpnn",
        "name": "ProteinMPNN",
        "category": "sequence_design",
        "modes": [
            {
                "id": "design",
                "name": "Sequence Design",
                "description": "Design sequences for a given backbone structure",
                "params": [
                    "input_pdb",
                    "mpnn_temperature",
                    "mpnn_omitAAs",
                    "mpnn_checkpoint_type",
                    "mpnn_checkpoint_model",
                    "mpnn_backbone_noise",
                    "mpnn_relax_max_cycles",
                    "mpnn_extra_config"
                ]
            }
        ],
        "params": [
            {
                "name": "input_pdb",
                "type": "file",
                "description": "Input backbone PDB structure for sequence design",
                "required": true,
                "file_type": "pdb",
                "preset_type": "pdb"
            },
            {
                "name": "mpnn_temperature",
                "type": "number",
                "description": "Sampling temperature. Higher = more diverse sequences.",
                "required": false,
                "default": 0.1,
                "minimum": 0.01,
                "maximum": 2.0
            },
            {
                "name": "mpnn_omitAAs",
                "type": "string",
                "description": "Amino acids to exclude from design (e.g., 'CX' excludes Cys)",
                "required": false,
                "default": "CX"
            },
            {
                "name": "mpnn_checkpoint_type",
                "type": "string",
                "description": "Model checkpoint - 'soluble' optimizes for solubility",
                "required": false,
                "default": "soluble",
                "enum": [
                    "vanilla",
                    "soluble"
                ]
            },
            {
                "name": "mpnn_checkpoint_model",
                "type": "string",
                "description": "Noise level model (higher noise = more robust to backbone errors)",
                "required": false,
                "default": "v_48_020",
                "enum": [
                    "v_48_002",
                    "v_48_010",
                    "v_48_020",
                    "v_48_030"
                ]
            },
            {
                "name": "mpnn_backbone_noise",
                "type": "number",
                "description": "Add Gaussian noise to backbone (\u00c5) for robustness",
                "required": false,
                "default": 0,
                "minimum": 0,
                "maximum": 1
            },
            {
                "name": "mpnn_relax_max_cycles",
                "type": "integer",
                "description": "Maximum FastRelax refinement cycles (0 = disabled)",
                "required": false,
                "default": 0,
                "minimum": 0,
                "maximum": 10
            },
            {
                "name": "mpnn_extra_config",
                "type": "string",
                "description": "Additional ProteinMPNN CLI arguments (e.g., '-protein_features=full')",
                "required": false
            }
        ]
    }
],
    targets: vi.fn(async () => ({ data: [{ id: 'vast:sequence-fixture', name: 'Sequence fixture', active: true, state: 'ready', capabilities: {} }] })),
}));
vi.mock('../../src/lib/api', async original => ({
    ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: fixtures.models })),
    fetchTemplates: vi.fn(async () => ({ data: [] })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchExecutionTargets: fixtures.targets,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: { protein_design: { default_enabled: false } } }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';

let root: Root | undefined;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
const originalUrl = window.location.href;
let captured: Array<Record<string, any>>;
beforeEach(() => {
    window.history.replaceState({}, '', '/submit');
    captured = [];
    // Reject every unexpected transport operation, rather than opening a socket.
    api.defaults.adapter = async config => {
        if (config.method !== 'post' || config.url !== '/api/jobs') throw Error(`Unexpected fixture request: ${config.method} ${config.url}`);
        const payload = JSON.parse(config.data);
        captured.push(payload);
        console.info('GENERIC_SEQUENCE_CAPTURE ' + JSON.stringify(payload));
        return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(() => { throw Error('Unexpected fixture fetch'); }));
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = undefined;
    client?.clear();
    api.defaults.adapter = originalAdapter;
    window.history.replaceState({}, '', originalUrl);
    document.body.replaceChildren();
    localStorage.clear(); sessionStorage.clear();
    vi.unstubAllGlobals(); vi.restoreAllMocks(); fixtures.targets.mockClear();
});
const button = (text: string) => {
    const found = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === text);
    expect(found, `button ${text}`).toBeTruthy();
    return found!;
};
async function click(text: string) { await act(async () => button(text).click()); }
async function change(input: HTMLInputElement | HTMLSelectElement, value: string) {
    expect(input).toBeTruthy();
    await act(async () => {
        Object.getOwnPropertyDescriptor(input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(input, value);
        input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
}
function field(name: string) {
    const label = [...document.querySelectorAll('label')].find(item => item.firstChild?.textContent === name);
    expect(label, `field ${name}`).toBeTruthy();
    const input = label!.parentElement!.querySelector<HTMLInputElement>('input:not([type="range"])');
    expect(input, `input ${name}`).toBeTruthy();
    return input!;
}
async function mount(model: string, mode: string, variant = 'edge') {
    // Existing saved-job hydration enters the actual manual model/mode picker.
    // Deliberately retained unrelated values must be filtered by the real mode.
    localStorage.setItem('clonedJobData', JSON.stringify({ name: `generic-${variant}-${model}-${mode}`, model_id: model, mode: 'design', execution_target_id: 'vast:sequence-fixture', params: {
        unrelated_fixture_setting: 'must-not-reach-native', target_chain: 'B', fixed_positions: 'A:1',
        fampnn_repack_last: true, fampnn_analysis_declaration: { forged: true },
        fampnn_analysis_policy: { forged: true }, core_protein_scientific_contract: { forged: true },
    } }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(localStorage.getItem('clonedJobData')).toBeNull();
    const modePicker = [...document.querySelectorAll('select')].find(item => [...item.options].some(option => option.textContent === 'Select a mode...'))!;
    await change(modePicker, mode);
    expect(modePicker.value).toBe(mode);
    await click('📂 Browse');
    await change(field('input_pdb'), 'inputs/generic-sequence-fixture.pdb');
    if ([...document.querySelectorAll('button')].some(item => item.textContent?.includes('Advanced Settings'))) {
        const advanced = [...document.querySelectorAll('button')].find(item => item.textContent?.includes('Advanced Settings'))!;
        await act(async () => advanced.click());
    }
}
async function editScience(model: string, mode: string, variant = 'edge') {
    if (model === 'proteinmpnn') {
        await change(field('mpnn_omitAAs'), '');
        for (const key of ['mpnn_backbone_noise', 'mpnn_relax_max_cycles']) {
            await change(field(key), '1'); await change(field(key), '0');
        }
    } else {
        await change(field(mode === 'fixed_backbone' ? 'fixed_positions' : 'target_chain'), '');
        await change(field('fampnn_psce_threshold'), '0');
        if (mode !== 'fixed_backbone') {
            expect(field('fampnn_repack_last').checked).toBe(true);
            await act(async () => field('fampnn_repack_last').click());
            expect(field('fampnn_repack_last').checked).toBe(false);
        }
        const override = document.querySelector<HTMLInputElement>('[aria-label="Override mutation scope"]')!;
        await act(async () => override.click());
        await change(document.querySelector('[aria-label="mutation chain"]')!, 'A');
        await change(document.querySelector('[aria-label="mutation author residue"]')!, variant === 'native' ? '1' : '0');
        await change(document.querySelector('[aria-label="mutation insertion code"]')!, variant === 'native' ? '' : 'B');
        await click('Add mutation residue');
    }
}
const rows = [
    { model: 'fampnn', mode: 'design', target: null, policy: 'manual' },
    { model: 'fampnn', mode: 'fixed_backbone', target: 'vast:sequence-fixture', policy: 'manual' },
    { model: 'fampnn', mode: 'binder_design', target: 'vast:sequence-fixture', policy: 'automatic' },
    { model: 'proteinmpnn', mode: 'design', target: null, policy: 'automatic' },
];
it.each(rows.flatMap(row => [{ ...row, variant: 'edge' }, { ...row, target: null, variant: 'native' }]))('manual $model/$mode $variant edits reach serialized request ($target, $policy)', async ({ model, mode, target, policy, variant }) => {
    // Explicit Local must clear a previously saved worker, not merely omit target.
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:sequence-fixture');
    await mount(model, mode, variant);
    await editScience(model, mode, variant);
    await click(target ? 'Vast · Sequence fixture' : 'Local');
    await change(document.querySelector('[aria-label="Successful remote results"]')!, policy);
    expect(button(target ? 'Vast · Sequence fixture' : 'Local').getAttribute('aria-pressed')).toBe('true');
    expect(button('Launch Experiment').disabled).toBe(false);
    await click('Launch Experiment');
    expect(captured).toHaveLength(1);
    const payload = captured[0];
    expect(payload).toMatchObject({ model_id: model, mode, execution_target_id: target, execution_policy: { remote_result_policy: policy }, params: { input_pdb: 'inputs/generic-sequence-fixture.pdb' } });
    const selected = fixtures.models.find(item => item.id === model)!.modes.find(item => item.id === mode)!;
    expect(Object.keys(payload.params).every(key => selected.params.includes(key))).toBe(true);
    for (const forbidden of ['unrelated_fixture_setting', 'execution_target_id', 'remote_result_policy', 'sequence_design_engine', 'sequence_design_mode', 'fampnn_analysis_declaration', 'fampnn_analysis_policy', 'core_protein_scientific_contract', 'fampnn_analysis_overrides']) expect(payload.params).not.toHaveProperty(forbidden);
    if (model === 'proteinmpnn') {
        expect(payload.params).toMatchObject({ mpnn_omitAAs: '', mpnn_backbone_noise: 0, mpnn_relax_max_cycles: 0 });
        expect(payload).not.toHaveProperty('fampnn_analysis_overrides');
    } else {
        expect(payload.params).toMatchObject({ [mode === 'fixed_backbone' ? 'fixed_positions' : 'target_chain']: '', fampnn_psce_threshold: 0 });
        if (mode !== 'fixed_backbone') expect(payload.params.fampnn_repack_last).toBe(false);
        else expect(payload.params).not.toHaveProperty('fampnn_repack_last');
        expect(payload.fampnn_analysis_overrides).toEqual({ mutation: [{ chain_id: 'A', author_number: variant === 'native' ? 1 : 0, insertion_code: variant === 'native' ? '' : 'B' }] });
    }
});
it('failed target refresh preserves the saved remote placement in the real submit helper, never implicit Local', async () => {
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:sequence-fixture');
    await mount('proteinmpnn', 'design');
    fixtures.targets.mockRejectedValueOnce(Error('fixture inventory refresh failed'));
    await act(async () => { await client.refetchQueries({ queryKey: ['execution-targets'] }); await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(document.body.textContent).toContain('The selected placement is preserved');
    expect(document.body.textContent).toContain('Selected worker vast:sequence-fixture is unavailable');
    expect(button('Local').getAttribute('aria-pressed')).toBe('false');
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe('vast:sequence-fixture');
    await click('Launch Experiment');
    expect(captured).toHaveLength(1);
    expect(captured[0].execution_target_id).toBe('vast:sequence-fixture');
    // Fixture records intent only: server readiness/admission is not mocked as accepted science.
});
