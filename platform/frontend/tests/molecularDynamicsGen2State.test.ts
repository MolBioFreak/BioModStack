import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import * as mdState from '../src/components/molecularDynamicsUiState.js';

const sha = (value: string) => value.repeat(64);

const form = (): mdState.MolecularDynamicsForm => ({
    jobName: 'typed MD',
    engine: 'gromacs',
    inputMode: 'structure',
    structurePath: '',
    coordinatesPath: '',
    topologyPath: '',
    replicas: 4,
    productionNs: 0.001,
    randomSeed: 17,
    forceField: 'browser-must-not-author',
    waterModel: 'browser-must-not-author',
    paddingNm: 2,
    saltMolar: 0.3,
    temperatureK: 310,
    pressureBar: 2,
    minimizationSteps: 4000,
    nvtPs: 10,
    nptPs: 20,
    timestepFs: 4,
    trajectoryIntervalPs: 0.5,
    energyIntervalPs: 0.25,
    checkpointIntervalMinutes: 5,
    ntomp: 6,
    neutralize: false,
});

const profile = (): mdState.MolecularDynamicsChemistryProfile => ({
    schema: 'bms.md.chemistry-profile.v1',
    id: 'amber_ff19sb_opc_protein_v1',
    version: '1.0.0',
    profile_sha256: sha('a'),
    display_name: 'AMBER ff19SB + OPC protein',
    family: 'amber',
    assurance: 'validated',
    legacy: false,
    automatic_preparation: true,
    inventory_class: 'selectable',
    availability_explanation: 'Exact managed fixture only.',
    supported_engines: ['gromacs'],
    v1_preparation: { force_field: 'amber99sb-ildn', water_model: 'tip3p' },
    launch_constraints: {
        input_mode: 'structure',
        structure_sha256: sha('b'),
        replicas: 1,
        engine: 'gromacs',
        force_field: 'ff19SB',
        water_model: 'OPC',
        timestep_fs: 2,
        temperature_k: 300,
        pressure_bar: 1,
        salt_molar: 0.15,
        padding_nm: 1,
        max_production_steps: 5000,
        max_minimization_steps: 50000,
        max_nvt_steps: 50000,
        max_npt_steps: 50000,
    },
    scientific_validation: {
        validated: true,
        lane: 'short-gpu',
        version: '1.0.0',
        scope: { launch_scope: 'short_gpu', system_classes: ['protein'] },
    },
    states: {
        installed: true,
        runtime_validated: true,
        scientifically_validated: true,
        operator_enabled: true,
        asset_probe_success: true,
        selectable: true,
    },
    explicit_exclusions: [],
    runtime_identity: {
        runtime_id: 'md-preparation-v1',
        runtime_version: '1',
        sif_sha256: sha('f'),
    },
});

const inspection = (): mdState.MolecularDynamicsStartingStructureInspection => ({
    schema_version: 'bms.md.starting-structure-inspection.v1',
    source_ref: { kind: 'managed_fixture', id: '1aki-admitted-v1' },
    identity: {
        label: 'RCSB 1AKI',
        format: 'pdb',
        size_bytes: 196371,
        sha256: sha('b'),
        pdb_id: '1AKI',
        producer_job_id: null,
        design_id: null,
    },
    inspection: {
        model_count: 1,
        atom_count: 1079,
        chains: ['A'],
        hetero_components: [],
        parser: { name: 'biopython', version: '1.85' },
    },
    admission: {
        state: 'admitted',
        profile_id: profile().id,
        code: 'MD_STARTING_STRUCTURE_ADMITTED',
        message: 'The exact bytes are admitted.',
    },
    viewer: { url: '/api/molecular-dynamics/starting-structures/artifact-1', format: 'pdb', sha256: sha('b') },
});

const gen2 = mdState as unknown as {
    applyMolecularDynamicsProfileDefaults: (
        form: mdState.MolecularDynamicsForm,
        profile: mdState.MolecularDynamicsChemistryProfile,
    ) => mdState.MolecularDynamicsForm;
    buildMolecularDynamicsLaunchIntent: (input: {
        form: mdState.MolecularDynamicsForm;
        source: { source_ref: { kind: 'managed_fixture'; id: string }; identity: { sha256: string } };
        profile: mdState.MolecularDynamicsChemistryProfile;
        catalogDigest: string;
        launchContextId?: string | null;
    }) => Record<string, unknown>;
    resolveMolecularDynamicsCloneSource: (initialValues: Record<string, unknown>) => { kind: string; id: string } | null;
    buildMolecularDynamicsPredictionHandoff: (sequence: string, name: string) => Record<string, unknown>;
    parseMolecularDynamicsStartingStructureInspection: (
        value: unknown,
        expectedSource?: { kind: string; id: string },
        expectedProfileId?: string,
    ) => Record<string, unknown>;
    parseMolecularDynamicsLaunchPreview: (value: unknown, expectedIntent?: Record<string, unknown>) => Record<string, unknown>;
    parseMolecularDynamicsChemistryProfileInventory: (value: unknown) => mdState.MolecularDynamicsChemistryProfileInventory;
    parseMolecularDynamicsServerFilePage: (value: unknown) => Record<string, unknown>;
    parseMolecularDynamicsPredictionSourceCandidates: (value: unknown, expectedJobId?: string) => Record<string, unknown>;
    buildMolecularDynamicsPredictionRoute: (sequenceId: string, draftId: string) => string;
    buildMolecularDynamicsPredictionReturnRoute: (draftId: string, jobResponse: unknown) => string;
    parseMolecularDynamicsHandoffUserSequence: (value: unknown, expectedSequenceId: string) => Record<string, unknown>;
    parseMolecularDynamicsHandoffUserSequencePage: (value: unknown) => Array<Record<string, unknown>>;
    buildMolecularDynamicsHandoffInitialValues: (
        route: {
            sourceSequenceId: string | null;
            draftId: string | null;
            sourcePredictionJobId: string | null;
            sourceDesignId: string | null;
            returnTemplate: 'molecular_dynamics' | null;
        },
        savedDraft: Record<string, unknown> | null,
    ) => Record<string, unknown>;
    parseMolecularDynamicsHandoffRoute: (search: string) => {
        sourceSequenceId: string | null;
        draftId: string | null;
        sourcePredictionJobId: string | null;
        sourceDesignId: string | null;
        returnTemplate: 'molecular_dynamics' | null;
    };
    buildResultsViewerMolecularDynamicsRoute: (jobId: string, designId: string) => string;
};

describe('Molecular Dynamics Gen 2 typed state', () => {
    it('projects every profile-fixed value into the editable form without changing operator-owned values', () => {
        assert.equal(typeof gen2.applyMolecularDynamicsProfileDefaults, 'function');
        const effective = gen2.applyMolecularDynamicsProfileDefaults(form(), profile());
        assert.deepEqual({
            replicas: effective.replicas,
            paddingNm: effective.paddingNm,
            saltMolar: effective.saltMolar,
            temperatureK: effective.temperatureK,
            pressureBar: effective.pressureBar,
            timestepFs: effective.timestepFs,
        }, {
            replicas: 1,
            paddingNm: 1,
            saltMolar: 0.15,
            temperatureK: 300,
            pressureBar: 1,
            timestepFs: 2,
        });
        assert.equal(effective.randomSeed, 17);
        assert.equal(effective.productionNs, 0.001);
        assert.equal(effective.neutralize, false);
    });

    it('accepts only a recursively closed chemistry profile inventory', () => {
        assert.equal(typeof gen2.parseMolecularDynamicsChemistryProfileInventory, 'function');
        const selected = profile();
        const inventory = {
            schema: 'bms.md.chemistry-profile-inventory.v1',
            catalog_digest: sha('c'),
            profiles: [selected],
            selectable_profile_ids: [selected.id],
            count: 1,
            bounded: true as const,
        };
        assert.equal(gen2.parseMolecularDynamicsChemistryProfileInventory(inventory), inventory);
        assert.throws(
            () => gen2.parseMolecularDynamicsChemistryProfileInventory({
                ...inventory,
                profiles: [{ ...selected, states: { ...selected.states, runtime_path: '/private/runtime.sif' } }],
            }),
            /chemistry profile inventory/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsChemistryProfileInventory({
                ...inventory,
                profiles: [{ ...selected, runtime_identity: { ...selected.runtime_identity, image_path: '/private/runtime.sif' } }],
            }),
            /chemistry profile inventory/i,
        );
    });

    it('accepts only the closed opaque server-file inventory', () => {
        assert.equal(typeof gen2.parseMolecularDynamicsServerFilePage, 'function');
        const page = {
            items: [{ id: 'opaque-handle', label: 'candidate.cif', format: 'cif', bytes: 4096 }],
            next_cursor: null,
            count: 1,
        };
        assert.equal(gen2.parseMolecularDynamicsServerFilePage(page), page);
        assert.throws(
            () => gen2.parseMolecularDynamicsServerFilePage({
                ...page,
                items: [{ ...page.items[0], managed_path: '/private/candidate.cif' }],
            }),
            /server-file inventory/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsServerFilePage({ ...page, items: [{ ...page.items[0], format: 'mmcif' }] }),
            /server-file inventory/i,
        );
    });

    it('builds the closed typed intent from inspected source identity and complete requested settings', () => {
        assert.equal(typeof gen2.buildMolecularDynamicsLaunchIntent, 'function');
        const fixed = gen2.applyMolecularDynamicsProfileDefaults(form(), profile());
        const intent = gen2.buildMolecularDynamicsLaunchIntent({
            form: fixed,
            source: {
                source_ref: { kind: 'managed_fixture', id: '1aki-admitted-v1' },
                identity: { sha256: sha('b') },
            },
            profile: profile(),
            catalogDigest: sha('c'),
            launchContextId: 'launch-context-1',
        });
        assert.deepEqual(Object.keys(intent).sort(), [
            'catalog_digest',
            'chemistry_profile_id',
            'chemistry_profile_sha256',
            'expected_source_sha256',
            'launch_context_id',
            'name',
            'requested_settings',
            'schema_version',
            'source_ref',
        ]);
        assert.equal(intent.schema_version, 'bms.md.launch-intent.v1');
        assert.deepEqual(intent.source_ref, { kind: 'managed_fixture', id: '1aki-admitted-v1' });
        assert.deepEqual(intent.requested_settings, {
            replicas: 1,
            random_seed: 17,
            padding_nm: 1,
            salt_molar: 0.15,
            neutralize: false,
            temperature_k: 300,
            pressure_bar: 1,
            timestep_fs: 2,
            minimization_steps: 4000,
            nvt_ps: 10,
            npt_ps: 20,
            production_ns: 0.001,
            trajectory_interval_ps: 0.5,
            energy_interval_ps: 0.25,
            checkpoint_interval_minutes: 5,
            ntomp: 6,
        });
    });

    it('reopens legacy and Gen 2 clones through prior job-owned MD input instead of a browser path', () => {
        assert.equal(typeof gen2.resolveMolecularDynamicsCloneSource, 'function');
        assert.deepEqual(
            gen2.resolveMolecularDynamicsCloneSource({ source_job_id: '11111111-1111-4111-8111-111111111111' }),
            { kind: 'prior_md_input', id: '11111111-1111-4111-8111-111111111111' },
        );
        assert.deepEqual(
            gen2.resolveMolecularDynamicsCloneSource({
                intent: { source_ref: { kind: 'design', id: '22222222-2222-4222-8222-222222222222' } },
            }),
            { kind: 'design', id: '22222222-2222-4222-8222-222222222222' },
        );
        assert.equal(gen2.resolveMolecularDynamicsCloneSource({ md_job_spec: { input: { structure: '/host/path.pdb' } } }), null);
    });

    it('fails closed on malformed starting-structure and preview wire responses', () => {
        const selectedProfile = profile();
        const sourceInspection = inspection();
        assert.equal(gen2.parseMolecularDynamicsStartingStructureInspection(sourceInspection), sourceInspection);
        assert.throws(
            () => gen2.parseMolecularDynamicsStartingStructureInspection({ ...sourceInspection, schema_version: 'unknown' }),
            /starting-structure inspection/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsStartingStructureInspection({ ...sourceInspection, admission: { state: 'admitted' } }),
            /starting-structure inspection/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsStartingStructureInspection(
                sourceInspection,
                { kind: 'design', id: '22222222-2222-4222-8222-222222222222' },
                selectedProfile.id,
            ),
            /starting-structure inspection/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsStartingStructureInspection(sourceInspection, sourceInspection.source_ref, 'another-profile'),
            /starting-structure inspection/i,
        );
        const preview = {
            schema_version: 'bms.md.launch-preview.v1',
            source: { source_ref: sourceInspection.source_ref, ...sourceInspection.identity },
            chemistry: { profile_id: selectedProfile.id, profile_sha256: selectedProfile.profile_sha256, catalog_digest: sha('c'), admitted: true },
            requested_settings: {
                replicas: 1,
                random_seed: 17,
                padding_nm: 1,
                salt_molar: 0.15,
                neutralize: false,
                temperature_k: 300,
                pressure_bar: 1,
                timestep_fs: 2,
                minimization_steps: 4000,
                nvt_ps: 10,
                npt_ps: 20,
                production_ns: 0.001,
                trajectory_interval_ps: 0.5,
                energy_interval_ps: 0.25,
                checkpoint_interval_minutes: 5,
                ntomp: 6,
            },
            effective_request: {
                engine: 'gromacs',
                replicas: 1,
                random_seed: 17,
                preparation: { box_type: 'dodecahedron', padding_nm: 1, salt_molar: 0.15, neutralize: false },
                stages: {
                    minimization: { enabled: true, steps: 4000, force_tolerance_kj_mol_nm: 1000 },
                    nvt: { enabled: true, steps: 5000, temperature_k: 300 },
                    npt: { enabled: true, steps: 10000, temperature_k: 300, pressure_bar: 1 },
                    production: {
                        enabled: true,
                        steps: 500,
                        timestep_fs: 2,
                        temperature_k: 300,
                        pressure_bar: 1,
                        checkpoint_interval_minutes: 5,
                        trajectory_interval_steps: 250,
                        energy_interval_steps: 125,
                    },
                },
                execution: { ntmpi: 1, ntomp: 6, gpu_offload: 'full', pin: 'on', placement_authority: 'global_scheduler' },
            },
            preview_digest: sha('e'),
            blockers: [],
            warnings: [],
        };
        const expectedIntent = gen2.buildMolecularDynamicsLaunchIntent({
            form: gen2.applyMolecularDynamicsProfileDefaults(form(), selectedProfile),
            source: sourceInspection,
            profile: selectedProfile,
            catalogDigest: sha('c'),
        });
        assert.equal(gen2.parseMolecularDynamicsLaunchPreview(preview, expectedIntent), preview);
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({
                ...preview,
                source: { ...preview.source, source_ref: { kind: 'rcsb', id: '1AKI' } },
            }, expectedIntent),
            /launch preview/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({
                ...preview,
                chemistry: { ...preview.chemistry, profile_sha256: sha('d') },
            }, expectedIntent),
            /launch preview/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({
                ...preview,
                requested_settings: { ...preview.requested_settings, random_seed: 18 },
            }, expectedIntent),
            /launch preview/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({ ...preview, preview_digest: 'not-a-digest' }),
            /launch preview/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({ ...preview, blockers: [{ code: 'X' }] }),
            /launch preview/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsLaunchPreview({
                ...preview,
                effective_request: {
                    ...preview.effective_request,
                    preparation: { ...preview.effective_request.preparation, managed_path: '/private/input.pdb' },
                },
            }),
            /launch preview/i,
        );
    });

    it('hands sequence prediction to the canonical Structure Prediction launcher without inventing coordinates', () => {
        assert.equal(typeof gen2.buildMolecularDynamicsPredictionHandoff, 'function');
        assert.deepEqual(
            gen2.buildMolecularDynamicsPredictionHandoff(' acd efg ', 'Candidate A'),
            {
                name: 'Candidate A structure prediction',
                job_name: 'Candidate A structure prediction',
                sequence: 'ACDEFG',
                sequence_name: 'Candidate A',
                md_return_template: 'molecular_dynamics',
            },
        );
        assert.throws(() => gen2.buildMolecularDynamicsPredictionHandoff('ACD*', 'bad'), /protein sequence/i);
    });

    it('accepts only the closed durable UserSequence handoff record', () => {
        assert.equal(typeof gen2.parseMolecularDynamicsHandoffUserSequence, 'function');
        const sequenceId = '11111111-1111-4111-8111-111111111111';
        const record = {
            id: sequenceId,
            name: 'Candidate A',
            sequence: 'ACDEFG',
            description: null,
            length: 6,
            organism: null,
            uniprot_id: null,
            ncbi_id: null,
            is_preset: false,
            created_at: '2026-08-26T00:00:00Z',
            updated_at: null,
        };
        assert.equal(gen2.parseMolecularDynamicsHandoffUserSequence(record, sequenceId), record);
        assert.throws(
            () => gen2.parseMolecularDynamicsHandoffUserSequence({ ...record, managed_path: '/private/sequence.fa' }, sequenceId),
            /saved sequence/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsHandoffUserSequence({ ...record, length: 7 }, sequenceId),
            /saved sequence/i,
        );
        assert.deepEqual(gen2.parseMolecularDynamicsHandoffUserSequencePage([record]), [record]);
        assert.throws(
            () => gen2.parseMolecularDynamicsHandoffUserSequencePage([{ ...record, private_path: '/private/sequence.fa' }]),
            /saved sequence/i,
        );
    });

    it('restores the exact saved chemistry identity when a prediction handoff returns', () => {
        assert.equal(typeof gen2.buildMolecularDynamicsHandoffInitialValues, 'function');
        const draftId = '22222222-2222-4222-8222-222222222222';
        const jobId = '33333333-3333-4333-8333-333333333333';
        const route = gen2.parseMolecularDynamicsHandoffRoute(
            `?template=molecular_dynamics&md_draft_id=${draftId}&source_prediction_job_id=${jobId}`,
        );
        assert.deepEqual(
            gen2.buildMolecularDynamicsHandoffInitialValues(route, {
                form: { jobName: 'returned MD', structurePath: '/must-not-return' },
                selectedProfileId: profile().id,
                selectedProfileDigest: profile().profile_sha256,
            }),
            {
                md_form: { jobName: 'returned MD' },
                intent: {
                    chemistry_profile_id: profile().id,
                    chemistry_profile_sha256: profile().profile_sha256,
                },
                source_prediction_job_id: jobId,
            },
        );
    });

    it('accepts only the recursively closed sanitized prediction Job and Design projection', () => {
        assert.equal(typeof gen2.parseMolecularDynamicsPredictionSourceCandidates, 'function');
        const jobId = '33333333-3333-4333-8333-333333333333';
        const designId = '77777777-7777-4777-8777-777777777777';
        const page = {
            schema_version: 'bms.md.prediction-source-candidates.v1',
            job: {
                id: jobId,
                name: 'Candidate A structure prediction',
                status: 'completed',
                model_id: 'boltz2',
                mode: 'predict',
                created_at: '2026-08-26T00:00:00Z',
                started_at: '2026-08-26T00:00:01Z',
                completed_at: '2026-08-26T00:00:02Z',
                failure: null,
            },
            candidates: [{
                source_ref: { kind: 'design', id: designId },
                name: 'model_01',
                format: 'pdb',
                eligible: true,
                blocker_code: null,
                metrics: { plddt: 91, ptm: 0.8, iptm: null, confidence: 0.9 },
                created_at: '2026-08-26T00:00:02Z',
            }],
            next_cursor: null,
        };
        assert.equal(gen2.parseMolecularDynamicsPredictionSourceCandidates(page, jobId), page);
        assert.throws(
            () => gen2.parseMolecularDynamicsPredictionSourceCandidates({
                ...page,
                candidates: [{ ...page.candidates[0], metrics: { ...page.candidates[0].metrics, pdb_path: '/private/model.pdb' } }],
            }, jobId),
            /prediction source candidates/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsPredictionSourceCandidates({
                ...page,
                job: { ...page.job, params: { managed_path: '/private/job' } },
            }, jobId),
            /prediction source candidates/i,
        );
    });

    it('builds and parses UUID-only standalone prediction routes and rejects ambiguous parameters', () => {
        assert.equal(typeof gen2.buildMolecularDynamicsPredictionRoute, 'function');
        assert.equal(typeof gen2.buildMolecularDynamicsPredictionReturnRoute, 'function');
        assert.equal(typeof gen2.parseMolecularDynamicsHandoffRoute, 'function');
        const sequenceId = '11111111-1111-4111-8111-111111111111';
        const draftId = '22222222-2222-4222-8222-222222222222';
        const jobId = '33333333-3333-4333-8333-333333333333';
        const outbound = gen2.buildMolecularDynamicsPredictionRoute(sequenceId, draftId);
        assert.equal(outbound, `/submit?template=structure_prediction&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}`);
        assert.deepEqual(gen2.parseMolecularDynamicsHandoffRoute(outbound.slice('/submit'.length)), {
            sourceSequenceId: sequenceId,
            draftId,
            sourcePredictionJobId: null,
            sourceDesignId: null,
            returnTemplate: 'molecular_dynamics',
        });
        assert.equal(
            gen2.buildMolecularDynamicsPredictionReturnRoute(draftId, { id: jobId }),
            `/submit?template=molecular_dynamics&md_draft_id=${draftId}&source_prediction_job_id=${jobId}`,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsHandoffRoute(`?template=structure_prediction&source_sequence_id=${sequenceId}&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}`),
            /route/i,
        );
        assert.throws(
            () => gen2.parseMolecularDynamicsHandoffRoute(`?template=structure_prediction&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}&return_uri=%2Fprivate`),
            /route/i,
        );
        assert.throws(() => gen2.buildMolecularDynamicsPredictionReturnRoute(draftId, { job_id: jobId }), /job/i);
        assert.throws(() => gen2.buildMolecularDynamicsPredictionRoute('not-a-uuid', draftId), /route/i);
    });

    it('builds the exact standalone ResultsViewer Design handoff route', () => {
        assert.equal(typeof gen2.buildResultsViewerMolecularDynamicsRoute, 'function');
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designId = '77777777-7777-4777-8777-777777777777';
        assert.equal(
            gen2.buildResultsViewerMolecularDynamicsRoute(jobId, designId),
            `/submit?template=molecular_dynamics&source_prediction_job_id=${jobId}&source_design_id=${designId}`,
        );
        assert.throws(() => gen2.buildResultsViewerMolecularDynamicsRoute(jobId, 'bad'), /route/i);
    });
});
