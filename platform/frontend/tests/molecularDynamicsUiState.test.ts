import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
    buildMolecularDynamicsJobSpec,
    estimateMolecularDynamicsScope,
    validateMolecularDynamicsChemistryProfile,
    validateMolecularDynamicsForm,
    type MolecularDynamicsChemistryProfile,
    type MolecularDynamicsForm,
} from '../src/components/molecularDynamicsUiState.js';

const validForm = (): MolecularDynamicsForm => ({
    jobName: '1AKI production MD',
    engine: 'gromacs',
    inputMode: 'structure',
    structurePath: '/data/1aki.pdb',
    coordinatesPath: '',
    topologyPath: '',
    replicas: 1,
    productionNs: 0.001,
    randomSeed: 20260717,
    forceField: 'amber99sb-ildn',
    waterModel: 'tip3p',
    paddingNm: 1,
    saltMolar: 0.15,
    temperatureK: 300,
    pressureBar: 1,
    minimizationSteps: 50000,
    nvtPs: 100,
    nptPs: 100,
    timestepFs: 2,
    trajectoryIntervalPs: 1,
    energyIntervalPs: 0.2,
    checkpointIntervalMinutes: 15,
    ntomp: 8,
});

const validProfile = (): MolecularDynamicsChemistryProfile => ({
    id: 'gmx_amber99sb_ildn_tip3p_smoke_v1',
    version: '1.0.0',
    profile_sha256: 'a'.repeat(64),
    display_name: 'AMBER99SB-ILDN + TIP3P infrastructure smoke',
    family: 'amber',
    assurance: 'smoke_fixture',
    legacy: true,
    automatic_preparation: true,
    inventory_class: 'selectable',
    availability_explanation: 'Selectable only for smoke_auto.',
    supported_engines: ['gromacs'],
    v1_preparation: { force_field: 'amber99sb-ildn', water_model: 'tip3p' },
    scientific_validation: {
        validated: true,
        lane: 'gromacs_1aki_smoke_v1',
        version: '1.0.0',
        scope: { launch_scope: 'smoke_auto', system_classes: ['infrastructure_smoke_fixture'] },
    },
    states: {
        installed: true,
        runtime_validated: true,
        scientifically_validated: true,
        operator_enabled: true,
        asset_probe_success: true,
        selectable: true,
    },
    launch_constraints: {
        input_mode: 'structure',
        structure_sha256: 'c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb',
        replicas: 1,
        engine: 'gromacs',
        force_field: 'amber99sb-ildn',
        water_model: 'tip3p',
        timestep_fs: 2,
        temperature_k: 300,
        pressure_bar: 1,
        salt_molar: 0.15,
        padding_nm: 1,
        max_production_steps: 5_000,
        max_minimization_steps: 50_000,
        max_nvt_steps: 50_000,
        max_npt_steps: 50_000,
    },
} as MolecularDynamicsChemistryProfile);

describe('molecular dynamics launcher contract', () => {
    it('builds an authoritative durable bms.md.job.v2 specification for profile-based GROMACS', () => {
        const catalogDigest = 'd'.repeat(64);
        const spec = buildMolecularDynamicsJobSpec(validForm(), validProfile(), catalogDigest);

        assert.equal(spec.schema, 'bms.md.job.v2');
        assert.equal(spec.engine, 'gromacs');
        assert.equal(spec.replicas, 1);
        assert.deepEqual(spec.input, { structure: '/data/1aki.pdb' });
        assert.equal(spec.stages.production.steps, 500);
        assert.equal(spec.stages.production.trajectory_interval_steps, 500);
        assert.equal(spec.stages.production.energy_interval_steps, 100);
        assert.equal(spec.stages.nvt.steps, 50_000);
        assert.equal(spec.stages.npt.steps, 50_000);
        assert.equal(spec.execution.gpu_id, '0');
        assert.equal(spec.execution.ntomp, 8);
        assert.equal(spec.execution.gpu_offload, 'full_forces');
        assert.deepEqual((spec as Extract<typeof spec, { schema: 'bms.md.job.v2' }>).chemistry, {
            profile_id: validProfile().id,
            profile_sha256: validProfile().profile_sha256,
            catalog_digest: catalogDigest,
            requested_scope: validProfile().scientific_validation.scope.launch_scope,
        });
        assert.equal('chemistry_assurance' in spec.preparation, false);
    });

    it('fails closed when a profile-based launch has no catalog generation digest', () => {
        assert.throws(
            () => buildMolecularDynamicsJobSpec(validForm(), validProfile()),
            /catalog digest/i,
        );
    });

    it('requires prepared coordinates and topology for the OpenMM alpha adapter', () => {
        const errors = validateMolecularDynamicsForm({ ...validForm(), engine: 'openmm' });
        assert.equal(errors.some((error) => error.includes('prepared coordinates and topology')), true);
    });

    it('serializes prepared OpenMM as external_unreviewed without catalog profile claims', () => {
        const spec = buildMolecularDynamicsJobSpec({
            ...validForm(),
            engine: 'openmm',
            inputMode: 'prepared',
            structurePath: '',
            coordinatesPath: '/data/system.gro',
            topologyPath: '/data/topol.top',
        }, validProfile());
        assert.equal(spec.engine, 'openmm');
        assert.equal(spec.stages.minimization.enabled, false);
        assert.equal(spec.stages.nvt.enabled, false);
        assert.equal(spec.stages.npt.enabled, false);
        const preparation = spec.preparation as unknown as Record<string, unknown>;
        assert.equal(preparation.chemistry_assurance, 'external_unreviewed');
        assert.equal(Object.keys(preparation).some((key) => key.startsWith('chemistry_profile_')), false);
    });

    it('rejects prepared GROMACS with an explanatory validator error and never serializes it', () => {
        const invalid = {
            ...validForm(),
            engine: 'gromacs' as const,
            inputMode: 'prepared' as const,
            structurePath: '',
            coordinatesPath: '/data/system.gro',
            topologyPath: '/data/topol.top',
        };

        const errors = validateMolecularDynamicsForm(invalid);
        assert.equal(errors.some((error) => /prepared.*OpenMM|OpenMM.*prepared/i.test(error)), true);
        assert.throws(() => buildMolecularDynamicsJobSpec(invalid, validProfile()), /prepared.*OpenMM|OpenMM.*prepared/i);
    });

    it('rejects an attached profile that is incompatible with the selected engine', () => {
        const errors = validateMolecularDynamicsChemistryProfile(validProfile(), 'openmm', false);
        assert.equal(errors.some((error) => error.includes('not validated for openmm')), true);
    });

    it('enforces all machine-readable smoke launch constraints in the form validator', () => {
        const validateWithProfile = validateMolecularDynamicsForm as unknown as (
            form: MolecularDynamicsForm,
            profile?: MolecularDynamicsChemistryProfile,
        ) => string[];
        for (const [field, value, fragment] of [
            ['replicas', 2, 'replica'],
            ['productionNs', 0.011, 'production'],
            ['saltMolar', 0.16, 'salt'],
            ['temperatureK', 301, 'temperature'],
            ['pressureBar', 2, 'pressure'],
            ['paddingNm', 1.1, 'padding'],
            ['minimizationSteps', 50_001, 'minimization'],
            ['nvtPs', 101, 'nvt'],
            ['nptPs', 101, 'npt'],
        ] as const) {
            const errors = validateWithProfile({ ...validForm(), [field]: value }, validProfile());
            assert.equal(errors.some((error) => error.toLowerCase().includes(fragment)), true, field);
        }
    });

    it('rejects invalid cadence and unsafe ranges before launch', () => {
        const errors = validateMolecularDynamicsForm({
            ...validForm(),
            replicas: 0,
            productionNs: 0,
            trajectoryIntervalPs: 0,
            checkpointIntervalMinutes: 0,
        });
        assert.equal(errors.length >= 4, true);
    });

    it('rejects positive output cadence that exceeds the production duration', () => {
        const errors = validateMolecularDynamicsForm({
            ...validForm(),
            trajectoryIntervalPs: 1.002,
            energyIntervalPs: 2,
        });

        assert.equal(errors.some((error) => error.includes('Trajectory interval') && error.includes('production')), true);
        assert.equal(errors.some((error) => error.includes('Energy interval') && error.includes('production')), true);
    });

    it('reports aggregate simulation scope without inventing throughput', () => {
        assert.deepEqual(estimateMolecularDynamicsScope(validForm()), {
            aggregateSimulationNs: 0.001,
            productionStepsPerReplica: 500,
            trajectoryFramesPerReplica: 1,
            totalTrajectoryFrames: 1,
        });
    });
});
