import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    CANONICAL_FRUSTRAMPNN_SETTINGS,
    getFrustraMpnnSelectionModeOptions,
    hydrateFrustraMpnnSettings,
    parseFrustraMpnnRequestedSettings,
    selectFrustraMpnnProteinSelectionMode,
    type FrustraMpnnRequestedSettings,
} from '../src/components/frustrampnn/frustraMpnnSettingsState.js';
import {
    inspectFrustraMpnnOwnedSource,
    inspectFrustraMpnnUploadedSource,
    parseFrustraMpnnSourceInspection,
    parseFrustraMpnnSettingsValidationPreview,
    validateFrustraMpnnOwnedSettings,
    validateFrustraMpnnUploadedSettings,
} from '../src/lib/frustraMpnnApi.js';
import * as frustraMpnnApi from '../src/lib/frustraMpnnApi.js';
import { api } from '../src/lib/api.js';
import { buildStructureFrustraMpnnSubmitParams } from '../src/components/structurePredictionUiState.js';

const entityA = {
    entity_instance_id: 'entity-1',
    source_entity_id: '1',
    label_asym_id: 'AA',
    auth_asym_id: 'A',
};
const entityB = {
    entity_instance_id: 'entity-2',
    source_entity_id: null,
    label_asym_id: 'BB',
    auth_asym_id: 'B',
};
const residueA = {
    ...entityA,
    auth_seq_id: 10,
    insertion_code: '',
    sequence_index: 1,
};
const residueB = {
    ...entityB,
    auth_seq_id: 20,
    insertion_code: 'A',
    sequence_index: 2,
};

const sourceInspection = {
    source_models: [1, 2],
    selected_source_model: 2,
    observed_altlocs: ['', 'A'],
    selected_altloc: 'A',
    protein_entities: [
        { ...entityA, pdb_chain_id: 'A' },
        { ...entityB, pdb_chain_id: 'B' },
    ],
    mapped_residues: [
        { ...residueA, wt: 'M' },
        { ...residueB, wt: 'G' },
    ],
};

const customSettings = (): FrustraMpnnRequestedSettings => ({
    schema_name: 'frustrampnn_settings',
    schema_version: 1,
    protein_selection: {
        mode: 'selected_residues',
        entities: [],
        regions: [],
        residues: [residueB, residueA],
    },
    source_structure: {
        selected_model_number: 2,
        preferred_altloc: 'A',
    },
    classification_policy: {
        mode: 'custom',
        high_max: -0.75,
        minimal_min: 0.25,
    },
});

const validationWire = () => {
    const requestedSettings = { ...customSettings(), settings_value_origin: 'operator_request' };
    const effectiveSettings = {
        schema_name: 'frustrampnn_effective_settings',
        schema_version: 1,
        requested_settings: requestedSettings,
        settings_value_origin: 'operator_request',
        resolved_chains: [],
        normalization_policy_id: 'frustrampnn_structure_normalizer',
        normalization_policy_version: 1,
        threshold_policy_id: 'frustrampnn_class_v1',
        threshold_policy_sha256: '1'.repeat(64),
        settings_sha256: '2'.repeat(64),
        capability_inventory_byte_sha256: '3'.repeat(64),
        resolution_identity: {
            source_artifact_sha256: '4'.repeat(64),
            structure_map_schema_name: 'frustrampnn_structure_map',
            structure_map_schema_version: 1,
            structure_map_sha256: '5'.repeat(64),
            normalized_pdb_sha256: '6'.repeat(64),
        },
        value_sources: {
            protein_selection: { mode: 'operator_request', entities: 'operator_request', regions: 'operator_request', residues: 'operator_request' },
            source_structure: { selected_model_number: 'operator_request', preferred_altloc: 'operator_request' },
            classification_policy: { mode: 'operator_request', high_max: 'operator_request', minimal_min: 'operator_request' },
        },
        effective_settings_sha256: '7'.repeat(64),
    };
    return {
        validation_scope: 'preview_only',
        queue_resolution_requirement: 'submission_must_re_resolve_governed_source',
        normalized_requested_settings: requestedSettings,
        effective_settings: effectiveSettings,
        execution_configuration: {
            configuration_id: 'frustrampnn_execution_configuration_v2',
            schema_name: 'frustrampnn_execution_configuration',
            schema_version: 2,
            tool_id: 'frustrampnn',
            tool_version: 'MegaScale',
            effective_settings: effectiveSettings,
            settings_value_origin: 'operator_request',
            requested_settings_sha256: '2'.repeat(64),
            effective_settings_sha256: '7'.repeat(64),
            capability_inventory_byte_sha256: '3'.repeat(64),
            classification_policy_sha256: '8'.repeat(64),
            runtime: {
                sif_name: 'frustrampnn.sif',
                sif_sha256: '9'.repeat(64),
                executable_sha256: 'a'.repeat(64),
                checkpoint_id: 'MegaScale',
                checkpoint_sha256: 'b'.repeat(64),
                package_version: '1.0',
                source_commit: 'deadbeef',
                python_version: '3.11',
                pytorch_version: '2.7',
                image_version: '1',
            },
            runtime_identity_sha256: 'c'.repeat(64),
            normalization_policy_id: 'frustrampnn_structure_normalizer',
            normalization_policy_version: 1,
            threshold_policy_id: 'frustrampnn_class_v1',
            source_artifact_sha256: '4'.repeat(64),
            structure_map_sha256: '5'.repeat(64),
            normalized_pdb_sha256: '6'.repeat(64),
            configuration_sha256: 'd'.repeat(64),
        },
        hashes: {
            settings_sha256: '2'.repeat(64),
            effective_settings_sha256: '7'.repeat(64),
            configuration_sha256: 'd'.repeat(64),
            capability_inventory_byte_sha256: '3'.repeat(64),
            structure_map_sha256: '5'.repeat(64),
        },
    };
};

const formKeys = (form: FormData): string[] => {
    const keys: string[] = [];
    form.forEach((_value, key) => keys.push(key));
    return keys;
};

test('canonical FrustraMPNN settings hydrate as a complete closed v1 object', () => {
    assert.deepEqual(hydrateFrustraMpnnSettings(undefined), CANONICAL_FRUSTRAMPNN_SETTINGS);
    assert.deepEqual(CANONICAL_FRUSTRAMPNN_SETTINGS, {
        schema_name: 'frustrampnn_settings',
        schema_version: 1,
        protein_selection: {
            mode: 'all_protein_entities',
            entities: [],
            regions: [],
            residues: [],
        },
        source_structure: {
            selected_model_number: 1,
            preferred_altloc: '',
        },
        classification_policy: {
            mode: 'canonical',
            high_max: -1,
            minimal_min: 0.58,
        },
    });
    assert.notStrictEqual(hydrateFrustraMpnnSettings(undefined), hydrateFrustraMpnnSettings(undefined));
});

test('persisted settings hydrate through Structure Prediction and antibody clone state without exposing server origin', () => {
    for (const settingsValueOrigin of ['bms_default', 'operator_request'] as const) {
        const persisted = {
            ...customSettings(),
            settings_value_origin: settingsValueOrigin,
        };
        const hydrated = hydrateFrustraMpnnSettings(persisted);
        assert.deepEqual(hydrated, parseFrustraMpnnRequestedSettings(customSettings()));
        assert.equal('settings_value_origin' in hydrated, false);
    }

    assert.throws(
        () => hydrateFrustraMpnnSettings({
            ...customSettings(),
            settings_value_origin: 'operator_forged',
        }),
        /settings_value_origin/i,
    );
    assert.throws(
        () => hydrateFrustraMpnnSettings({
            ...customSettings(),
            settings_value_origin: 'operator_request',
            command: 'frustrampnn --forged',
        }),
        /unknown|keys/i,
    );
});

test('global workflow launch params normalize complete settings only when FrustraMPNN is enabled', async () => {
    const settingsState = await import('../src/components/frustrampnn/frustraMpnnSettingsState.js');
    const buildLaunchParams = (settingsState as Record<string, unknown>).buildFrustraMpnnLaunchParams;
    assert.equal(typeof buildLaunchParams, 'function');

    const build = buildLaunchParams as (
        enabled: boolean,
        settings: FrustraMpnnRequestedSettings,
    ) => Record<string, unknown>;
    assert.deepEqual(build(true, customSettings()), {
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: parseFrustraMpnnRequestedSettings(customSettings()),
    });
    assert.deepEqual(build(false, customSettings()), {
        run_frustrampnn: false,
    });
});

test('generic launch resolution and parameter merge preserve the governed FrustraMPNN contract', async () => {
    const settingsState = await import('../src/components/frustrampnn/frustraMpnnSettingsState.js');
    const resolveWorkflow = (settingsState as Record<string, unknown>).resolveFrustraMpnnWorkflowId;
    const mergeLaunchParams = (settingsState as Record<string, unknown>).mergeFrustraMpnnLaunchParams;
    assert.equal(typeof resolveWorkflow, 'function');
    assert.equal(typeof mergeLaunchParams, 'function');

    const resolve = resolveWorkflow as (modelId: unknown, modeId: unknown) => string | null;
    assert.equal(resolve('rfdiffusion', 'binder_denovo'), 'protein_design');
    assert.equal(resolve('rfdiffusion', 'monomer_partialdiff'), 'protein_design');
    assert.equal(resolve('boltz2', 'complex'), 'complex_prediction');
    assert.equal(resolve('boltz2', 'predict'), null);
    assert.equal(resolve('proteinmpnn', 'design'), null);

    const merge = mergeLaunchParams as (
        params: Record<string, unknown>,
        enabled: boolean,
        settings: FrustraMpnnRequestedSettings,
    ) => Record<string, unknown>;
    const forgedBase = {
        sequence: 'ACDE',
        run_frustrampnn: false,
        frustrampnn_requiredness: 'optional',
        frustrampnn_settings: {
            ...customSettings(),
            settings_value_origin: 'operator_request',
        },
        frustrampnn_settings_value_origin: 'operator_request',
    };
    assert.deepEqual(merge(forgedBase, true, customSettings()), {
        sequence: 'ACDE',
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: parseFrustraMpnnRequestedSettings(customSettings()),
    });
    assert.deepEqual(merge(forgedBase, false, customSettings()), {
        sequence: 'ACDE',
        run_frustrampnn: false,
    });
});

test('strict settings parsing preserves custom thresholds and canonicalizes stable selector order', () => {
    const parsed = parseFrustraMpnnRequestedSettings(customSettings());
    assert.deepEqual(parsed.classification_policy, {
        mode: 'custom',
        high_max: -0.75,
        minimal_min: 0.25,
    });
    assert.deepEqual(parsed.protein_selection.residues, [residueA, residueB]);
    assert.deepEqual(hydrateFrustraMpnnSettings(customSettings()), parsed);
});

test('strict settings parsing rejects partial, unknown, non-finite, and unordered objects', () => {
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            schema_name: 'frustrampnn_settings',
            schema_version: 1,
        }),
        /missing|keys/i,
    );
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            ...CANONICAL_FRUSTRAMPNN_SETTINGS,
            command: 'frustrampnn --help',
        }),
        /unknown|keys/i,
    );
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            ...customSettings(),
            classification_policy: { mode: 'custom', high_max: Number.NaN, minimal_min: 0.25 },
        }),
        /finite/i,
    );
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            ...customSettings(),
            classification_policy: { mode: 'custom', high_max: 0.25, minimal_min: 0.25 },
        }),
        /high_max.*minimal_min/i,
    );
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            ...CANONICAL_FRUSTRAMPNN_SETTINGS,
            classification_policy: { mode: 'canonical', high_max: -0.75, minimal_min: 0.25 },
        }),
        /canonical/i,
    );
});

test('source inspection parser admits only bounded source models, observed altlocs, and stable identities', () => {
    const parsed = parseFrustraMpnnSourceInspection(sourceInspection);
    assert.deepEqual(parsed.source_models, [1, 2]);
    assert.deepEqual(parsed.observed_altlocs, ['', 'A']);
    assert.deepEqual(parsed.protein_entities.map((entity) => entity.entity_instance_id), ['entity-1', 'entity-2']);
    assert.deepEqual(parsed.mapped_residues.map((residue) => residue.sequence_index), [1, 2]);
    assert.equal('model_position' in parsed.mapped_residues[0], false);

    const sourceOnly = parseFrustraMpnnSourceInspection({
        ...sourceInspection,
        protein_entities: [{
            entity_instance_id: 'protein-instance-1',
            source_entity_id: 'source-protein',
            label_asym_id: null,
            auth_asym_id: null,
            pdb_chain_id: null,
        }],
        protein_sequence_spans: [{
            entity_instance_id: 'protein-instance-1',
            source_entity_id: 'source-protein',
            label_asym_id: null,
            auth_asym_id: null,
            sequence_start: 1,
            sequence_end: 25,
        }],
        mapped_residues: [],
    });
    assert.equal(sourceOnly.protein_entities[0]?.pdb_chain_id, null);
    assert.equal(sourceOnly.protein_entities[0]?.auth_asym_id, null);

    const multiCharacterChain = parseFrustraMpnnSourceInspection({
        ...sourceInspection,
        protein_entities: [{
            ...entityA,
            auth_asym_id: 'CHAIN_ALPHA',
            pdb_chain_id: 'CHAIN_ALPHA',
        }],
    });
    assert.equal(multiCharacterChain.protein_entities[0]?.pdb_chain_id, 'CHAIN_ALPHA');

    assert.throws(
        () => parseFrustraMpnnSourceInspection({ ...sourceInspection, source_path: '/private/source.cif' }),
        /unknown|forbidden/i,
    );
    assert.throws(
        () => parseFrustraMpnnSourceInspection({ ...sourceInspection, observed_altlocs: ['', 'AA'] }),
        /altloc/i,
    );
});

test('selection modes are unavailable without exact inspection metadata and use inspected stable identity when available', () => {
    assert.deepEqual(getFrustraMpnnSelectionModeOptions(undefined), [
        { mode: 'all_protein_entities', available: true },
        { mode: 'selected_entities', available: false, reason: 'Exact source entity identity is unavailable until source inspection is produced.' },
        { mode: 'selected_regions', available: false, reason: 'Exact source sequence identity is unavailable until source inspection is produced.' },
        { mode: 'selected_residues', available: false, reason: 'Exact source residue identity is unavailable until source inspection is produced.' },
    ]);

    assert.deepEqual(
        getFrustraMpnnSelectionModeOptions(parseFrustraMpnnSourceInspection(sourceInspection)).map((option) => [option.mode, option.available]),
        [
            ['all_protein_entities', true],
            ['selected_entities', true],
            ['selected_regions', true],
            ['selected_residues', true],
        ],
    );

    const inspection = parseFrustraMpnnSourceInspection(sourceInspection);
    const entities = selectFrustraMpnnProteinSelectionMode(CANONICAL_FRUSTRAMPNN_SETTINGS, 'selected_entities', inspection);
    assert.deepEqual(entities.protein_selection, {
        mode: 'selected_entities',
        entities: [entityA],
        regions: [],
        residues: [],
    });
    const regions = selectFrustraMpnnProteinSelectionMode(CANONICAL_FRUSTRAMPNN_SETTINGS, 'selected_regions', inspection);
    assert.deepEqual(regions.protein_selection, {
        mode: 'selected_regions',
        entities: [],
        regions: [{ ...entityA, sequence_start: 1, sequence_end: 1 }],
        residues: [],
    });
    const residues = selectFrustraMpnnProteinSelectionMode(CANONICAL_FRUSTRAMPNN_SETTINGS, 'selected_residues', inspection);
    assert.deepEqual(residues.protein_selection, {
        mode: 'selected_residues',
        entities: [],
        regions: [],
        residues: [residueA],
    });
    assert.throws(
        () => selectFrustraMpnnProteinSelectionMode(CANONICAL_FRUSTRAMPNN_SETTINGS, 'selected_entities', undefined),
        /unavailable/i,
    );
});

test('sequence-span inspection enables regions without fabricating mapped author residues', () => {
    const inspection = parseFrustraMpnnSourceInspection({
        source_models: [1],
        selected_source_model: 1,
        observed_altlocs: [''],
        selected_altloc: '',
        protein_entities: [{ ...entityA, pdb_chain_id: 'A' }],
        protein_sequence_spans: [{ ...entityA, sequence_start: 1, sequence_end: 50 }],
        mapped_residues: [],
    });

    const options = getFrustraMpnnSelectionModeOptions(inspection);
    assert.equal(options.find((option) => option.mode === 'selected_regions')?.available, true);
    assert.equal(options.find((option) => option.mode === 'selected_residues')?.available, false);
    assert.deepEqual(
        selectFrustraMpnnProteinSelectionMode(
            CANONICAL_FRUSTRAMPNN_SETTINGS,
            'selected_regions',
            inspection,
        ).protein_selection,
        {
            mode: 'selected_regions',
            entities: [],
            regions: [{ ...entityA, sequence_start: 1, sequence_end: 50 }],
            residues: [],
        },
    );
});


test('selected regions canonicalize stable entity sequence ranges and reject overlap', () => {
    const parsed = parseFrustraMpnnRequestedSettings({
        ...CANONICAL_FRUSTRAMPNN_SETTINGS,
        protein_selection: {
            mode: 'selected_regions',
            entities: [],
            regions: [
                { ...entityA, sequence_start: 30, sequence_end: 40 },
                { ...entityA, sequence_start: 10, sequence_end: 20 },
            ],
            residues: [],
        },
    });
    assert.deepEqual(parsed.protein_selection.regions.map((region) => [
        region.sequence_start,
        region.sequence_end,
    ]), [[10, 20], [30, 40]]);
    assert.throws(
        () => parseFrustraMpnnRequestedSettings({
            ...CANONICAL_FRUSTRAMPNN_SETTINGS,
            protein_selection: {
                mode: 'selected_regions',
                entities: [],
                regions: [
                    { ...entityA, sequence_start: 10, sequence_end: 20 },
                    { ...entityA, sequence_start: 20, sequence_end: 30 },
                ],
                residues: [],
            },
        }),
        /overlap/i,
    );
});

test('Structure Prediction FrustraMPNN submit transport is exact for enabled and disabled states', () => {
    const normalized = parseFrustraMpnnRequestedSettings(customSettings());
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(true, customSettings()), {
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: normalized,
    });
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(false, customSettings()), {
        run_frustrampnn: false,
        frustrampnn_requiredness: 'required',
    });
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(undefined, CANONICAL_FRUSTRAMPNN_SETTINGS), {
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: CANONICAL_FRUSTRAMPNN_SETTINGS,
    });
});

test('settings validation client projection excludes execution, runtime, path, command, scheduler, and storage fields', () => {
    const preview = parseFrustraMpnnSettingsValidationPreview(validationWire());
    assert.deepEqual(Object.keys(preview), [
        'validation_scope',
        'queue_resolution_requirement',
        'normalized_requested_settings',
        'effective_settings',
        'hashes',
    ]);
    const serialized = JSON.stringify(preview).toLowerCase();
    for (const forbidden of ['path', 'runtime', 'command', 'scheduler', 'storage', 'gpu']) {
        assert.doesNotMatch(serialized, new RegExp(forbidden));
    }
});

test('historical effective settings hydrate missing additive region fields', () => {
    const wire = JSON.parse(JSON.stringify(validationWire()));
    delete wire.normalized_requested_settings.protein_selection.regions;
    delete wire.effective_settings.requested_settings.protein_selection.regions;
    delete wire.effective_settings.value_sources.protein_selection.regions;
    delete wire.execution_configuration.effective_settings.requested_settings.protein_selection.regions;
    delete wire.execution_configuration.effective_settings.value_sources.protein_selection.regions;

    const preview = parseFrustraMpnnSettingsValidationPreview(wire);

    assert.deepEqual(preview.normalized_requested_settings.protein_selection.regions, []);
    assert.deepEqual(preview.effective_settings.requested_settings.protein_selection.regions, []);
    assert.equal(
        preview.effective_settings.value_sources.protein_selection.regions,
        'operator_request',
    );
});

test('governed owned and upload clients use the exact closed OpenAPI request shapes', async () => {
    const calls: Array<{ url: string; data: unknown; config?: unknown }> = [];
    const originalPost = api.post;
    (api as unknown as { post: (url: string, data: unknown, config?: unknown) => Promise<{ data: unknown }> }).post = async (url, data, config) => {
        calls.push({ url, data, config });
        return { data: url.includes('/sources/inspect/') ? sourceInspection : validationWire() };
    };
    const file = new File(['ATOM'], 'design.pdb', { type: 'chemical/x-pdb' });
    try {
        await inspectFrustraMpnnOwnedSource(
            { job_id: 'job / 1', invocation_id: 'invoke / 1' },
            { selected_model_number: 2, preferred_altloc: 'A' },
        );
        await inspectFrustraMpnnUploadedSource(file, { selected_model_number: 2, preferred_altloc: 'A' });
        await validateFrustraMpnnOwnedSettings(customSettings(), { job_id: 'job / 1', invocation_id: 'invoke / 1' });
        await validateFrustraMpnnUploadedSettings(customSettings(), file);

        assert.equal(calls[0]?.url, '/api/frustrampnn/sources/inspect/owned');
        assert.deepEqual(calls[0]?.data, {
            job_id: 'job / 1', invocation_id: 'invoke / 1', selected_model_number: 2, preferred_altloc: 'A',
        });
        assert.equal(calls[1]?.url, '/api/frustrampnn/sources/inspect/upload');
        assert.deepEqual(formKeys(calls[1]?.data as FormData), ['structure_file', 'selected_model_number', 'preferred_altloc']);
        assert.equal(calls[2]?.url, '/api/frustrampnn/settings/validate/owned');
        assert.deepEqual(calls[2]?.data, {
            job_id: 'job / 1', invocation_id: 'invoke / 1', settings: parseFrustraMpnnRequestedSettings(customSettings()),
        });
        assert.equal(calls[3]?.url, '/api/frustrampnn/settings/validate/upload');
        assert.deepEqual(formKeys(calls[3]?.data as FormData), ['structure_file', 'settings']);
        assert.deepEqual(
            JSON.parse(String((calls[3]?.data as FormData).get('settings'))),
            parseFrustraMpnnRequestedSettings(customSettings()),
        );
    } finally {
        (api as unknown as { post: typeof originalPost }).post = originalPost;
    }
});

test('uploaded analysis client sends the governed file and complete settings only', async () => {
    const launch = (frustraMpnnApi as unknown as {
        analyzeFrustraMpnnUpload?: (file: File, settings: FrustraMpnnRequestedSettings) => Promise<unknown>;
    }).analyzeFrustraMpnnUpload;
    assert.equal(typeof launch, 'function');
    const originalPost = api.post;
    let call: { url: string; data: unknown } | null = null;
    (api as unknown as { post: (url: string, data: unknown) => Promise<{ data: unknown }> }).post = async (url, data) => {
        call = { url, data };
        return { data: {
            job_id: 'child-1', child_job_id: 'child-1', result_job_id: 'child-1',
            name: 'FrustraMPNN analysis upload-1', parent_job_id: null, source_parent_job_id: null,
            trigger: 'upload_analyze', status: 'queued', created_at: '2026-08-09T00:00:00Z',
            started_at: null, completed_at: null, settings_value_origin: 'operator_request',
            requested_settings: { ...customSettings(), settings_value_origin: 'operator_request' },
            requested_settings_sha256: 'a'.repeat(64),
            candidates: [{
                selection_ordinal: 0, design_id: null, source_job_id: null, candidate_id: 'upload-1',
                invocation_id: 'invoke-1', source_artifact_id: null, source_artifact_sha256: 'b'.repeat(64),
                component_request_sha256: 'c'.repeat(64), normalized_pdb_sha256: 'd'.repeat(64),
                structure_map_sha256: 'e'.repeat(64), settings_value_origin: 'operator_request',
                requested_settings_sha256: 'a'.repeat(64), effective_settings: null,
                effective_settings_sha256: null, capability_inventory_byte_sha256: null,
                classification_policy_sha256: null, execution_configuration_sha256: null,
                runtime_identity_sha256: null, producer: null,
            }],
            results: [],
        } };
    };
    const file = new File(['ATOM'], 'uploaded.pdb', { type: 'chemical/x-pdb' });
    try {
        await launch!(file, customSettings());
        assert.equal(call?.url, '/api/frustrampnn/jobs/uploads/analyze');
        assert.deepEqual(formKeys(call?.data as FormData), ['pdb_file', 'frustrampnn_settings']);
        assert.equal((call?.data as FormData).get('pdb_file'), file);
        assert.deepEqual(
            JSON.parse(String((call?.data as FormData).get('frustrampnn_settings'))),
            parseFrustraMpnnRequestedSettings(customSettings()),
        );
    } finally {
        (api as unknown as { post: typeof originalPost }).post = originalPost;
    }
});

test('Structure Prediction owns one typed panel and never adds raw JSON or runtime controls to FrustraMPNN payloads', () => {
    const templateSource = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    const panelSource = readFileSync('src/components/frustrampnn/FrustraMpnnSettingsPanel.tsx', 'utf8');
    const apiSource = readFileSync('src/lib/frustraMpnnApi.ts', 'utf8');

    assert.equal((templateSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.match(templateSource, /hydrateFrustraMpnnSettings\(initialValues\?\.frustrampnn_settings\)/);
    assert.match(templateSource, /buildStructureFrustraMpnnSubmitParams\(runFrustrampnn, frustrampnnSettings\)/);
    assert.match(templateSource, /runFrustrampnn,\s*frustrampnnSettings,\s*isBoltzCpLaunch/);
    assert.match(apiSource, /\/api\/models\/frustrampnn\/integration/);
    assert.match(apiSource, /\/api\/frustrampnn\/settings\/validate/);
    assert.match(apiSource, /\/api\/frustrampnn\/sources\/inspect/);
    assert.doesNotMatch(panelSource, /textarea|raw json|command|runtime|scheduler|storage|gpu/i);
});

test('every current Antibody workflow FrustraMPNN control uses one shared panel state and complete launch contract', () => {
    const antibodySource = readFileSync('src/components/AntibodyDenovoTemplate.tsx', 'utf8');

    assert.equal((antibodySource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.match(antibodySource, /hydrateFrustraMpnnSettings\(initialValues\?\.frustrampnn_settings\)/);
    assert.match(antibodySource, /setFrustrampnnSettings\(hydrateFrustraMpnnSettings\(initialValues\.frustrampnn_settings\)\)/);
    assert.match(antibodySource, /setFrustrampnnSettings\(hydrateFrustraMpnnSettings\(p\.frustrampnn_settings\)\)/);
    assert.equal((antibodySource.match(/settingsControl=\{frustrampnnSettingsControl\}/g) || []).length, 2);
    assert.match(antibodySource, /\{!isRefinementMode && showQcPanels && \(\s*<div[^>]*>[\s\S]*?<ModelIntegrationControl/);
    assert.match(antibodySource, /buildFrustraMpnnLaunchParams\(effectiveRunFrustrampnn, frustrampnnSettings\)/);
    assert.match(antibodySource, /buildFrustraMpnnLaunchParams\(runFrustrampnn, frustrampnnSettings\)/);
    assert.doesNotMatch(antibodySource, /frustrampnn_(?:settings|config).*JSON|JSON.*frustrampnn_(?:settings|config)/i);
});

test('generic JobSubmission composes one governed FrustraMPNN control for supported resolved workflows', () => {
    const submissionSource = readFileSync('src/components/JobSubmission.tsx', 'utf8');

    assert.match(submissionSource, /useModelIntegrationConfig\('frustrampnn'\)/);
    assert.match(submissionSource, /resolveFrustraMpnnWorkflowId/);
    assert.match(submissionSource, /frustrampnnIntegrationQuery\.data\?\.workflows\?\.\[resolvedFrustrampnnWorkflowId\]/);
    assert.equal((submissionSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.equal((submissionSource.match(/data-job-submission-frustrampnn/g) || []).length, 1);
    assert.match(submissionSource, /workflowId=\{resolvedFrustrampnnWorkflowId\}/);
    assert.match(submissionSource, /hydrateFrustraMpnnSettings\(params\.frustrampnn_settings\)/);
    assert.match(submissionSource, /setClonedValues\(data\.params\);/);
    assert.match(submissionSource, /const nextParams = \{ \.\.\.defaults, \.\.\.\(clonedValues \|\| \{\}\) \};\s*setParams\(nextParams\);/);
    assert.match(submissionSource, /\}, \[selectedModel, selectedModelId, clonedValues\]\);/);
    assert.match(submissionSource, /mergeFrustraMpnnLaunchParams\(mergedParams, runFrustrampnn, frustrampnnSettings\)/);
    assert.match(submissionSource, /mergeFrustraMpnnLaunchParams\(filteredParams, runFrustrampnn, frustrampnnSettings\)/);
    assert.match(submissionSource, /const templateManagerParams[\s\S]*mergeFrustraMpnnLaunchParams\(/);
    assert.match(submissionSource, /const frustrampnnConfigurationReady = !resolvedFrustrampnnWorkflowId \|\| \(\s*!frustrampnnIntegrationQuery\.isFetching\s*&& !frustrampnnIntegrationQuery\.isError\s*&& configuredFrustrampnnWorkflow !== undefined\s*\);/);
    assert.match(submissionSource, /const isReady = frustrampnnConfigurationReady && Boolean\(/);
    assert.match(submissionSource, /FrustraMPNN integration configuration is unavailable\. Launch is blocked\./);
    assert.match(submissionSource, /const governedMergedParams = resolvedFrustrampnnWorkflowId\s*\? mergeFrustraMpnnLaunchParams/);
    assert.match(submissionSource, /const governedFilteredParams = resolvedFrustrampnnWorkflowId\s*\? mergeFrustraMpnnLaunchParams/);
    assert.doesNotMatch(submissionSource, /selectedTemplateId === ['"](?:structure_prediction|antibody_denovo)['"][\s\S]{0,500}data-job-submission-frustrampnn/);
});
