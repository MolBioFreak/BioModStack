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
{"id": "caliby_experimental", "name": "Caliby Standalone", "category": "generative_design", "modes": [{"id": "ensemble_design", "name": "Ensemble Sequence Design", "description": "Ordered conformers condition one ensemble; first state is primary.", "params": ["batch_size", "num_workers", "scn_num_steps", "scn_step_scale", "ensembles", "model_name", "num_seqs_per_pdb", "temperature", "omit_aas", "verbose", "use_primary_res_type", "ensemble_ignore_res_idx_mismatch", "gaussian_n_conformers", "gaussian_noise_std", "potts_regularization", "potts_sweeps", "potts_proposal", "potts_rejection_step", "potts_only_cond"]}, {"id": "sidechain_pack", "name": "Fixed-sequence Side-chain Packing", "description": "Pack the native input sequence without amino-acid redesign.", "params": ["batch_size", "num_workers", "scn_num_steps", "scn_step_scale", "structures", "packer_model_name"]}], "params": [{"name": "batch_size", "type": "integer", "description": "Batch Size. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 4, "minimum": 1}, {"name": "num_workers", "type": "integer", "description": "Num Workers. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 8, "minimum": 0}, {"name": "scn_num_steps", "type": "integer", "description": "Scn Num Steps. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 50, "minimum": 1}, {"name": "scn_step_scale", "type": "number", "description": "Scn Step Scale. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 1.5}, {"name": "ensembles", "type": "array", "description": "Ordered ensembles with ensemble_id and ordered states. Each state has state_id, structure path, and typed native positional constraints; first state is primary.", "required": true}, {"name": "model_name", "type": "string", "description": "Model Name. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": "soluble_caliby_v1", "enum": ["caliby", "soluble_caliby", "soluble_caliby_v1"]}, {"name": "num_seqs_per_pdb", "type": "integer", "description": "Num Seqs Per Pdb. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 4, "minimum": 1}, {"name": "temperature", "type": "number", "description": "Temperature. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 0.1}, {"name": "omit_aas", "type": "string_list", "description": "Omit Aas. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": ["C"]}, {"name": "verbose", "type": "boolean", "description": "Verbose. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": true}, {"name": "use_primary_res_type", "type": "boolean", "description": "Use Primary Res Type. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": true}, {"name": "ensemble_ignore_res_idx_mismatch", "type": "boolean", "description": "Ensemble Ignore Res Idx Mismatch. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": false}, {"name": "gaussian_n_conformers", "type": "integer", "description": "Gaussian N Conformers. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 0, "minimum": 0}, {"name": "gaussian_noise_std", "type": "number", "description": "Gaussian Noise Std. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 0.0, "minimum": 0}, {"name": "potts_regularization", "type": "string", "description": "Potts Regularization. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": "LCP", "enum": ["LCP", "none"]}, {"name": "potts_sweeps", "type": "integer", "description": "Potts Sweeps. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": 500, "minimum": 1}, {"name": "potts_proposal", "type": "string", "description": "Potts Proposal. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": "dlmc", "enum": ["dlmc", "chromatic"]}, {"name": "potts_rejection_step", "type": "boolean", "description": "Potts Rejection Step. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": false}, {"name": "potts_only_cond", "type": "boolean", "description": "Potts Only Cond. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": false}, {"name": "structures", "type": "array", "description": "Input structures with distinct state_id and structure path; sequence identities stay fixed.", "required": true}, {"name": "packer_model_name", "type": "string", "description": "Packer Model Name. Native typed contract: services.caliby_native.REQUEST.", "required": false, "default": "caliby_packer_010", "enum": ["caliby_packer_000", "caliby_packer_010", "caliby_packer_030"]}]},
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
    fetchModelById: vi.fn(async (id: string) => ({ data: fixtures.models.find(model => model.id === id) })),
    fetchTemplates: vi.fn(async () => ({ data: [] })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchExecutionTargets: fixtures.targets,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: { protein_design: { default_enabled: false } } }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';

it('prediction handoff uses native author/model residue identity, not display-PDB chain truncation or nucleic acids', async () => {
    const source = { job_id: 'source', design_id: 'selected', output_format: 'native' as const };
    const retained = { ...source, path: 'inputs/retained.cif' };
    const residues = [
        { model_number: 2, auth_asym_id: 'AA', auth_seq_id: 1, insertion_code: '', residue_name: 'ALA' },
        { model_number: 2, auth_asym_id: 'AA', auth_seq_id: 1, insertion_code: 'B', residue_name: 'GLY' },
        { model_number: 2, auth_asym_id: 'DNA', auth_seq_id: 1, insertion_code: '', residue_name: 'DA' },
    ];
    api.defaults.adapter = async config => {
        expect(config.url).toBe('/api/files/materialize-structure');
        return { data: { path: retained.path, format: 'cif', source_structure: retained, author_residues: residues }, status: 200, statusText: 'OK', headers: {}, config };
    };
    const draft = await prepareDeNovoContinuation(source, 'prediction');
    expect(draft).toMatchObject({ source_structure: retained, sequence: '', _continuation_chains: [{ model_number: 2, id: 'AA', sequence: 'AG' }] });
    expect(draft).not.toHaveProperty('fixed_target_source_path');
    expect(captured).toHaveLength(0);
});



it('configured preparation serializes the currently selected model and edited native request', async () => {
    const previous = api.defaults.adapter as any;
    const requests: any[] = [];
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/provision/preview')) {
            requests.push(JSON.parse(config.data));
            return { data: { scope: 'managed_asset_activation', selection: requests.at(-1), preview_sha256: approvalDigest, total_bytes: 0, artifacts: [], blockers: [] }, status: 200, statusText: 'OK', headers: {}, config };
        }
        return previous(config);
    };
    await mount('fampnn', 'design');
    await change(field('fampnn_psce_threshold'), '0');
    await click('Preview artifact downloads');
    expect(requests[0]).toMatchObject({ kind: 'workflow', workflow_request: { model_id: 'fampnn', mode: 'design', params: { input_pdb: 'inputs/generic-sequence-fixture.pdb', fampnn_psce_threshold: 0 } } });
    await change(field('fampnn_psce_threshold'), '0.2');
    await click('Preview artifact downloads');
    expect(requests[1].workflow_request.params.fampnn_psce_threshold).toBe(0.2);
    expect(captured).toHaveLength(0);
});

it('a late initial source response cannot overwrite operator input', async () => {
    let finish!: () => void;
    const previous = api.defaults.adapter as any;
    api.defaults.adapter = async config => {
        if (config.url === '/api/files/materialize-structure') return new Promise(resolve => { finish = () => resolve({ data: nativeFixture, status: 200, statusText: 'OK', headers: {}, config }); });
        return previous(config);
    };
    await mountRoute(sequenceRoute);
    await click('📂 Browse');
    await change(field('input_pdb'), 'inputs/edited-before-source.pdb');
    await act(async () => finish()); await settle();
    expect(field('input_pdb').value).toBe('inputs/edited-before-source.pdb');
    await act(async () => { await client.refetchQueries({ queryKey: ['model', 'proteinmpnn'] }); });
    expect(field('input_pdb').value).toBe('inputs/edited-before-source.pdb');
});

async function mountRoute(route: string) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await settle();
}
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 35)); }); }
const sourceFixture = { job_id: 'source', design_id: 'design', output_format: 'native' };
const nativeFixture = { path: 'inputs/native.cif', format: 'cif', sha256: 'c'.repeat(64), model_number: 2, author_residues: [], source_structure: { ...sourceFixture, path: 'inputs/native.cif' } };
const sequenceRoute = '/submit?' + new URLSearchParams({ model: 'proteinmpnn', mode: 'design', continuation: 'sequence', source_structure: JSON.stringify(sourceFixture), return_to: '/designs/source' });

it('still reports invalid explicit Molecular Dynamics handoff metadata', async () => {
    await mountRoute('/submit?model=proteinmpnn&mode=design&md_draft_id=invalid');
    expect(document.body.textContent).toContain('Invalid Molecular Dynamics handoff route: md_draft_id must be one UUID.');
    expect(captured).toHaveLength(0);
});

it('a rejected PDB conversion leaves the chooser usable and Caliby submits the retained native CIF', async () => {
    const previous = api.defaults.adapter as any;
    const reads: any[] = [];
    api.defaults.adapter = async config => {
        if (config.url === '/api/files/materialize-structure') {
            const request = JSON.parse(config.data); reads.push(request);
            if (request.output_format === 'pdb') throw Error('Author chain cannot be represented in PDB');
            return { data: nativeFixture, status: 200, statusText: 'OK', headers: {}, config };
        }
        return previous(config);
    };
    await mountRoute(sequenceRoute);
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain('Author chain cannot be represented'); });
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'caliby_experimental:ensemble_design');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('input[aria-label="ensembles.0.states.0.path"]')).toBeTruthy(); });
    expect((document.querySelector('input[aria-label="ensembles.0.states.0.path"]') as HTMLInputElement).value).toBe(nativeFixture.path);
    expect(document.body.textContent).not.toContain('Invalid Molecular Dynamics handoff route');
    await change(document.querySelector('[aria-label="Sequence job name"]')!, 'native-cif-follow-on');
    await click('Local'); await click('Launch Experiment');
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ model_id: 'caliby_experimental', mode: 'ensemble_design', source_structure: nativeFixture.source_structure, params: { ensembles: [{ states: [{ path: nativeFixture.path }] }] } });
    expect(captured[0].params).not.toHaveProperty('input_pdb');
    expect(captured[0].params).not.toHaveProperty('_source_native_prepared');
    expect(reads.map(row => row.output_format)).toEqual(['native', 'pdb']);
});

it('late PDB preparation cannot replace an edited/cleared source or restore ancestry after switching models', async () => {
    let finish!: (value: any) => void;
    const previous = api.defaults.adapter as any;
    api.defaults.adapter = async config => {
        if (config.url !== '/api/files/materialize-structure') return previous(config);
        const request = JSON.parse(config.data);
        if (request.output_format === 'pdb') return new Promise(resolve => { finish = value => resolve({ data: value, status: 200, statusText: 'OK', headers: {}, config }); });
        return { data: nativeFixture, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mountRoute(sequenceRoute);
    await vi.waitFor(async () => { await settle(); expect(finish).toBeTypeOf('function'); });
    await click('📂 Browse');
    await change(field('input_pdb'), 'inputs/replacement.pdb');
    await change(field('input_pdb'), '');
    await act(async () => finish({ ...nativeFixture, format: 'pdb', path: 'inputs/late.pdb' }));
    await settle();
    await click('📂 Browse');
    expect(field('input_pdb').value).toBe('');
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'fampnn:design'); await settle();
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'proteinmpnn:design'); await settle();
    await click('📂 Browse');
    expect(field('input_pdb').value).toBe('');
    await change(field('input_pdb'), 'inputs/current.pdb');
    await change(document.querySelector('[aria-label="Sequence job name"]')!, 'replacement');
    await click('Local'); await click('Launch Experiment');
    expect(captured[0]).toMatchObject({ source_structure: { path: 'inputs/current.pdb' }, params: { input_pdb: 'inputs/current.pdb' } });
    expect(captured[0].source_structure).not.toHaveProperty('job_id');
});

it('Project save/unmount/reopen preserves inactive model and mode drafts, including false, zero and empty values', async () => {
    let setup: any = {
        schema: 'bms.project-workflow-setup.detail.v1', project_id: 'project', setup_context_id: 'setup',
        global_experiment_id: 'experiment', domain_experiment_id: 'domain', relationship_kind: 'follow_up',
        capability_id: 'protein', state: 'open', validation_state: 'incomplete', setup_destination: '/submit', return_uri: '/projects/project',
        generation: 1, project_label: 'Project', experiment_label: 'Experiment', workflow_label: 'Sequence', field_errors: {}, diagnostics: {},
        draft: { sequence_continuation: true, model_id: 'proteinmpnn', mode: 'design', job_name: 'saved', input_pdb: 'inputs/saved.pdb', mpnn_omitAAs: '', mpnn_backbone_noise: 0 },
    };
    const previous = api.defaults.adapter as any;
    api.defaults.adapter = async config => {
        if (config.url?.startsWith('/api/projects/project/workflow-setups/setup')) {
            if (config.method === 'put') { setup = { ...setup, generation: setup.generation + 1, draft: JSON.parse(config.data).draft }; }
            return { data: setup, status: 200, statusText: 'OK', headers: {}, config };
        }
        return previous(config);
    };
    const route = '/submit?project_id=project&setup_context_id=setup';
    await mountRoute(route);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Sequence design model"]')).toBeTruthy(); });
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'fampnn:design'); await settle();
    await change(field('target_chain'), 'B');
    await editScience('fampnn', 'design');
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'fampnn:fixed_backbone'); await settle();
    await change(field('fixed_positions'), 'A:1');
    await change(field('fixed_positions'), '');
    await change(field('fampnn_psce_threshold'), '0');
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'caliby_experimental:ensemble_design'); await settle();
    await click('Save draft');
    expect(setup.draft.binder_native_drafts['fampnn:design']).toMatchObject({ target_chain: '', fampnn_psce_threshold: 0, fampnn_repack_last: false });
    expect(setup.draft.binder_native_drafts['fampnn:fixed_backbone']).toMatchObject({ fixed_positions: '', fampnn_psce_threshold: 0 });
    await act(async () => root!.unmount()); root = undefined; client.clear(); document.body.replaceChildren();
    await mountRoute(route);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Sequence design model"]')).toBeTruthy(); });
    for (const [model, mode] of [['proteinmpnn', 'design'], ['fampnn', 'design'], ['fampnn', 'fixed_backbone']]) {
        await change(document.querySelector('[aria-label="Sequence design model"]')!, `${model}:${mode}`); await settle();
        if (model === 'proteinmpnn') { expect(field('mpnn_omitAAs').value).toBe(''); expect(field('mpnn_backbone_noise').value).toBe('0'); }
        else { expect(field(mode === 'design' ? 'target_chain' : 'fixed_positions').value).toBe(''); expect(field('fampnn_psce_threshold').value).toBe('0'); }
        if (model === 'fampnn' && mode === 'design') expect(field('fampnn_repack_last').checked).toBe(false);
    }
});

import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
// Keep the ordinary remote review mounted; only its transport is a fixture.
import '../../src/components/ExecutionPlanApproval';

let root: Root | undefined;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
const originalUrl = window.location.href;
let captured: Array<Record<string, any>>;
let previews: Array<Record<string, any>>;
const approvalDigest = 'a'.repeat(64);
beforeEach(() => {
    window.history.replaceState({}, '', '/submit');
    captured = []; previews = [];
    // Reject every unexpected transport operation, rather than opening a socket.
    api.defaults.adapter = async config => {
        if (config.method === 'post' && config.url === '/api/jobs/execution-plan/preview') {
            const payload = JSON.parse(config.data);
            previews.push(payload);
            // Transport/UI evidence only, not server or scientific admission.
            return { data: {
                schema: 'bms.job.execution-preview.v1', approval_digest: approvalDigest,
                admissible: true, request: payload,
                plan: { requested_json: payload.params, effective_json: payload.params,
                    source_identity: { revision: 'fixture', tree: 'fixture' },
                    metadata: { static_components: [], dynamic_templates: [], external_services: [] } },
                deferred_preparation: [], blockers: [],
            }, status: 200, statusText: 'OK', headers: {}, config };
        }
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
    if (target) {
        await vi.waitFor(async () => {
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
            expect(button('Approve and submit').disabled).toBe(false);
        });
        expect(captured).toHaveLength(0);
        expect(previews).toHaveLength(1);
        expect(previews[0]).not.toHaveProperty('execution_plan_approval');
        expect(document.body.textContent).toContain('Review remote execution plan');
        expect(document.body.textContent).toContain(target);
        await click('Show all settings');
        const settings = [...document.querySelectorAll('tr')].map(row => [...row.querySelectorAll('td')].map(cell => cell.textContent));
        expect(settings).toContainEqual(['fampnn_psce_threshold', '0', '0']);
        expect(settings).toContainEqual([mode === 'fixed_backbone' ? 'fixed_positions' : 'target_chain', '', '']);
        if (mode !== 'fixed_backbone') expect(settings).toContainEqual(['fampnn_repack_last', 'false', 'false']);
        await click('Approve and submit');
    } else {
        expect(previews).toHaveLength(0);
    }
    expect(captured).toHaveLength(1);
    const payload = captured[0];
    if (target) expect(payload).toEqual({ ...previews[0], execution_plan_approval: approvalDigest });
    else expect(payload).not.toHaveProperty('execution_plan_approval');
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
    await vi.waitFor(async () => {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
        expect(button('Approve and submit').disabled).toBe(false);
    });
    expect(captured).toHaveLength(0);
    expect(previews).toHaveLength(1);
    expect(previews[0]).toMatchObject({ model_id: 'proteinmpnn', mode: 'design', execution_target_id: 'vast:sequence-fixture' });
    expect(previews[0]).not.toHaveProperty('execution_plan_approval');
    expect(document.body.textContent).toContain('Review remote execution plan');
    await click('Show all settings');
    const settings = [...document.querySelectorAll('tr')].map(row => [...row.querySelectorAll('td')].map(cell => cell.textContent));
    expect(settings).toContainEqual(['mpnn_checkpoint_type', 'soluble', 'soluble']);
    expect(settings).toContainEqual(['mpnn_checkpoint_model', 'v_48_020', 'v_48_020']);
    expect(settings).toContainEqual(['mpnn_backbone_noise', '0', '0']);
    expect(settings).toContainEqual(['mpnn_relax_max_cycles', '0', '0']);
    await click('Approve and submit');
    expect(captured).toHaveLength(1);
    expect(captured[0]).toEqual({ ...previews[0], execution_plan_approval: approvalDigest });
    expect(captured[0].execution_target_id).toBe('vast:sequence-fixture');
    expect(captured[0].params).toMatchObject({ mpnn_checkpoint_type: 'soluble', mpnn_checkpoint_model: 'v_48_020', mpnn_backbone_noise: 0, mpnn_relax_max_cycles: 0 });
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe('vast:sequence-fixture');
    // Fixture records intent only: server readiness/admission is not mocked as accepted science.
});

it('selected native result opens the real sequence editor without a Job, retains the source through mode edits, and emits it outside native params', async () => {
    const source = { job_id: 'native-source', request_id: 'request', candidate_id: 'candidate', document: { artifact_id: 'artifact' }, output_format: 'native' };
    const retained = { ...source, path: 'inputs/retained-native.pdb', expected_sha256: 'b'.repeat(64) };
    const prepared = { path: retained.path, native_path: retained.path, format: 'pdb', native_format: 'pdb', sha256: retained.expected_sha256, native_sha256: retained.expected_sha256, model_number: 2, model_numbers: [2], author_residues: [], source_identity: source, source_path: 'original.cif.gz', source_structure: retained };
    const previousAdapter = api.defaults.adapter as any;
    const reads: any[] = [];
    api.defaults.adapter = async config => {
        if (config.url === '/api/files/materialize-structure') {
            reads.push(JSON.parse(config.data));
            return { data: prepared, status: 200, statusText: 'OK', headers: {}, config };
        }
        return previousAdapter(config);
    };
    const query = new URLSearchParams({ model: 'proteinmpnn', mode: 'design', continuation: 'sequence', source_structure: JSON.stringify(source) });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/submit?${query}`]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await vi.waitFor(async () => {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
        expect(document.querySelector('[aria-label="Sequence design model"]')).toBeTruthy();
    });
    expect(captured).toHaveLength(0); expect(reads).toHaveLength(1);
    await change(document.querySelector('[aria-label="Sequence design model"]')!, 'fampnn:fixed_backbone');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    await click('📂 Browse');
    expect(field('input_pdb').value).toBe(retained.path);
    await change(document.querySelector('[aria-label="Sequence job name"]')!, 'selected-result-follow-on');
    await change(field('fixed_positions'), 'A:1');
    await click('Local');
    await click('Launch Experiment');
    expect(reads).toHaveLength(1);
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ model_id: 'fampnn', mode: 'fixed_backbone', execution_target_id: null, source_structure: retained, params: { input_pdb: retained.path, fixed_positions: 'A:1' } });
    expect(captured[0]).not.toHaveProperty('parent_job_id');
    expect(captured[0].params).not.toHaveProperty('source_structure');
    expect(captured[0].params).not.toHaveProperty('_source_prepared');
});
