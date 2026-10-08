import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
    buildMolecularDynamicsJobSpec,
    validateMolecularDynamicsChemistryProfile,
    type MolecularDynamicsForm,
} from '../src/components/molecularDynamicsUiState.js';

const frontendRoot = process.cwd();
const launcherSource = fs.readFileSync(
    path.join(frontendRoot, 'src', 'components', 'MolecularDynamicsTemplate.tsx'),
    'utf8',
);

interface CatalogProfileFixture {
    id: string;
    version: string;
    profile_sha256: string;
    display_name: string;
    family: string;
    assurance: string;
    legacy: boolean;
    automatic_preparation: boolean;
    inventory_class: string;
    availability_explanation: string;
    supported_engines: Array<'gromacs' | 'openmm'>;
    v1_preparation: { force_field: string; water_model: string };
    launch_constraints: {
        input_mode: 'structure';
        structure_sha256: string;
        replicas: number;
        engine: 'gromacs';
        force_field: string;
        water_model: string;
        timestep_fs: number;
        temperature_k: number;
        pressure_bar: number;
        salt_molar: number;
        padding_nm: number;
        max_production_steps: number;
        max_minimization_steps: number;
        max_nvt_steps: number;
        max_npt_steps: number;
    };
    scientific_validation: {
        validated: boolean;
        lane: string | null;
        version: string | null;
        scope: { launch_scope: string; system_classes: string[] };
    };
    states: {
        installed: boolean;
        runtime_validated: boolean;
        scientifically_validated: boolean;
        operator_enabled: boolean;
        asset_probe_success: boolean;
        selectable: boolean;
    };
}

const smokeProfile = (): CatalogProfileFixture => ({
    id: 'gmx_amber99sb_ildn_tip3p_smoke_v1',
    version: '1.0.0',
    profile_sha256: 'a'.repeat(64),
    display_name: 'AMBER99SB-ILDN + TIP3P infrastructure smoke',
    family: 'amber',
    assurance: 'smoke_fixture',
    legacy: true,
    automatic_preparation: true,
    inventory_class: 'selectable',
    availability_explanation: 'Selectable only for smoke_auto infrastructure fixtures.',
    supported_engines: ['gromacs'],
    v1_preparation: { force_field: 'amber99sb-ildn', water_model: 'tip3p' },
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
});

const validForm = (): MolecularDynamicsForm => ({
    jobName: 'catalog driven smoke',
    engine: 'gromacs',
    inputMode: 'structure',
    structurePath: '/data/1aki.pdb',
    coordinatesPath: '',
    topologyPath: '',
    replicas: 1,
    productionNs: 0.002,
    randomSeed: 20260717,
    forceField: 'charmm36-jul2022',
    waterModel: 'spce',
    paddingNm: 1,
    saltMolar: 0.15,
    temperatureK: 300,
    pressureBar: 1,
    minimizationSteps: 500,
    nvtPs: 1,
    nptPs: 1,
    timestepFs: 2,
    trajectoryIntervalPs: 1,
    energyIntervalPs: 1,
    checkpointIntervalMinutes: 15,
    ntomp: 8,
});

describe('molecular dynamics chemistry catalog launcher', () => {
    it('serializes the selected catalog profile actual v1 chemistry instead of form leftovers', () => {
        const catalogBuilder = buildMolecularDynamicsJobSpec as unknown as (
            form: MolecularDynamicsForm,
            profile: CatalogProfileFixture,
        ) => ReturnType<typeof buildMolecularDynamicsJobSpec>;

        const spec = catalogBuilder(validForm(), smokeProfile());

        assert.equal(spec.schema, 'bms.md.job.v1');
        assert.equal(spec.preparation.force_field, 'amber99sb-ildn');
        assert.equal(spec.preparation.water_model, 'tip3p');
        assert.equal((spec.preparation as Record<string, unknown>).chemistry_profile_id, smokeProfile().id);
        assert.equal((spec.preparation as Record<string, unknown>).chemistry_profile_sha256, smokeProfile().profile_sha256);
        assert.equal((spec.preparation as Record<string, unknown>).chemistry_profile_scope, 'smoke_auto');
    });

    it('rejects a stale or unavailable selected profile before serialization', () => {
        const catalogBuilder = buildMolecularDynamicsJobSpec as unknown as (
            form: MolecularDynamicsForm,
            profile: CatalogProfileFixture,
        ) => ReturnType<typeof buildMolecularDynamicsJobSpec>;
        const candidate = smokeProfile();
        candidate.states.selectable = false;
        candidate.inventory_class = 'candidate';
        candidate.availability_explanation = 'Candidate only: deployed assets are absent.';

        assert.throws(
            () => catalogBuilder(validForm(), candidate),
            /candidate.*not selectable|not selectable.*candidate/i,
        );
    });

    it('blocks a saved same-ID profile when its preserved digest differs from the current catalog', () => {
        const current = smokeProfile();
        current.profile_sha256 = 'b'.repeat(64);
        const validateSavedDigest = validateMolecularDynamicsChemistryProfile as unknown as (
            profile: CatalogProfileFixture,
            engine: 'gromacs' | 'openmm',
            automaticPreparationRequired: boolean,
            savedDigest: string,
        ) => string[];

        const errors = validateSavedDigest(current, 'gromacs', true, 'a'.repeat(64));

        assert.equal(errors.some((error) => /digest.*reselect|reselect.*digest/i.test(error)), true);
        assert.match(launcherSource, /profileDigestIsStale/u);
        assert.match(launcherSource, /value=\{profileDigestIsStale \? '' : selectedProfileId\}/u);
    });

    it('loads the bounded catalog API and has no hard-coded CHARMM36 option regression', () => {
        assert.match(launcherSource, /\/api\/molecular-dynamics\/chemistry-profiles/u);
        assert.match(launcherSource, /states\.selectable/u);
        assert.match(launcherSource, /candidate/u);
        assert.doesNotMatch(launcherSource, /charmm36-jul2022/u);
        assert.doesNotMatch(launcherSource, /<option value="amber99sb-ildn"/u);
    });
});
