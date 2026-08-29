export type MolecularDynamicsEngine = 'gromacs' | 'openmm';
export {
    buildMolecularDynamicsHandoffInitialValues,
    buildMolecularDynamicsPredictionRoute,
    buildMolecularDynamicsPredictionReturnRoute,
    buildResultsViewerMolecularDynamicsRoute,
    loadMolecularDynamicsDraft,
    parseMolecularDynamicsHandoffRoute,
    parseMolecularDynamicsHandoffUserSequence,
    parseMolecularDynamicsHandoffUserSequencePage,
    storeMolecularDynamicsDraft,
} from './gen2StartingStructureState.js';
export type MolecularDynamicsInputMode = 'structure' | 'prepared';

export interface MolecularDynamicsLaunchConstraints {
    input_mode: 'structure';
    structure_sha256: string;
    replicas: number;
    engine: MolecularDynamicsEngine;
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
}

export interface MolecularDynamicsChemistryProfile {
    schema: 'bms.md.chemistry-profile.v1';
    id: string;
    version: string;
    profile_sha256: string;
    display_name: string;
    family: string;
    assurance: string;
    legacy: boolean;
    automatic_preparation: boolean;
    inventory_class: 'selectable' | 'candidate';
    availability_explanation: string;
    supported_engines: MolecularDynamicsEngine[];
    v1_preparation: {
        force_field: string;
        water_model: string;
    };
    launch_constraints: MolecularDynamicsLaunchConstraints | null;
    scientific_validation: {
        validated: boolean;
        lane: string | null;
        version: string | null;
        scope: {
            launch_scope: string;
            system_classes: string[];
            composition?: string;
            protocol?: string;
            ionic_conditions?: string;
            observables?: string[];
        };
    };
    states: {
        installed: boolean;
        runtime_validated: boolean;
        scientifically_validated: boolean;
        operator_enabled: boolean;
        asset_probe_success: boolean;
        selectable: boolean;
    };
    explicit_exclusions: string[];
    runtime_identity: {
        runtime_id: string;
        runtime_version: string | null;
        sif_sha256: string | null;
    };
}

export interface MolecularDynamicsChemistryProfileInventory {
    schema: 'bms.md.chemistry-profile-inventory.v1';
    catalog_digest: string;
    profiles: MolecularDynamicsChemistryProfile[];
    selectable_profile_ids: string[];
    count: number;
    bounded: true;
}

export interface MolecularDynamicsServerFilePage {
    items: Array<{ id: string; label: string; format: 'pdb' | 'cif'; bytes: number }>;
    next_cursor: string | null;
    count: number;
}

export interface MolecularDynamicsForm {
    jobName: string;
    engine: MolecularDynamicsEngine;
    inputMode: MolecularDynamicsInputMode;
    structurePath: string;
    coordinatesPath: string;
    topologyPath: string;
    replicas: number;
    productionNs: number;
    randomSeed: number;
    forceField: string;
    waterModel: string;
    paddingNm: number;
    saltMolar: number;
    temperatureK: number;
    pressureBar: number;
    minimizationSteps: number;
    nvtPs: number;
    nptPs: number;
    timestepFs: number;
    trajectoryIntervalPs: number;
    energyIntervalPs: number;
    checkpointIntervalMinutes: number;
    ntomp: number;
    neutralize?: boolean;
}

export type MolecularDynamicsStartingStructureKind =
    | 'rcsb'
    | 'design'
    | 'upload'
    | 'managed_fixture'
    | 'prior_md_input'
    | 'server_file';

export interface MolecularDynamicsStartingStructureRef {
    kind: MolecularDynamicsStartingStructureKind;
    id: string;
}

export interface MolecularDynamicsStartingStructureInspection {
    schema_version: 'bms.md.starting-structure-inspection.v1';
    source_ref: MolecularDynamicsStartingStructureRef;
    identity: {
        label: string;
        format: 'pdb' | 'cif';
        size_bytes: number;
        sha256: string;
        pdb_id: string | null;
        producer_job_id: string | null;
        design_id: string | null;
    };
    viewer: {
        url: string;
        format: 'pdb' | 'cif';
        sha256: string;
    };
    inspection: {
        model_count: number;
        chains: string[];
        atom_count: number;
        hetero_components: string[];
        parser: { name: 'biopython'; version: string };
    };
    admission: {
        state: 'profile_required' | 'admitted' | 'blocked';
        profile_id: string | null;
        code: string | null;
        message: string;
    };
}

export interface MolecularDynamicsPredictionSourceCandidate {
    source_ref: MolecularDynamicsStartingStructureRef;
    name: string;
    format: 'pdb' | 'cif';
    eligible: boolean;
    blocker_code: string | null;
    metrics: { plddt: number | null; ptm: number | null; iptm: number | null; confidence: number | null };
    created_at: string | null;
}

export interface MolecularDynamicsPredictionSourceCandidatePage {
    schema_version: 'bms.md.prediction-source-candidates.v1';
    job: {
        id: string;
        name: string;
        status: 'queued' | 'preparing' | 'running' | 'awaiting_input' | 'completed' | 'failed' | 'cancelled';
        model_id: string;
        mode: string;
        created_at: string | null;
        started_at: string | null;
        completed_at: string | null;
        failure: { code: string; message: string } | null;
    };
    candidates: MolecularDynamicsPredictionSourceCandidate[];
    next_cursor: string | null;
}

export interface MolecularDynamicsRequestedSettings {
    replicas: number;
    random_seed: number;
    padding_nm: number;
    salt_molar: number;
    neutralize: boolean;
    temperature_k: number;
    pressure_bar: number;
    timestep_fs: number;
    minimization_steps: number;
    nvt_ps: number;
    npt_ps: number;
    production_ns: number;
    trajectory_interval_ps: number;
    energy_interval_ps: number;
    checkpoint_interval_minutes: number;
    ntomp: number;
}

export interface MolecularDynamicsLaunchIntent {
    schema_version: 'bms.md.launch-intent.v1';
    name: string;
    source_ref: MolecularDynamicsStartingStructureRef;
    expected_source_sha256: string;
    chemistry_profile_id: string;
    chemistry_profile_sha256: string;
    catalog_digest: string;
    requested_settings: MolecularDynamicsRequestedSettings;
    launch_context_id: string | null;
}

export interface MolecularDynamicsLaunchPreview {
    schema_version: 'bms.md.launch-preview.v1';
    source: MolecularDynamicsStartingStructureInspection['identity'] & {
        source_ref: MolecularDynamicsStartingStructureRef;
    };
    chemistry: {
        profile_id: string;
        profile_sha256: string;
        catalog_digest: string;
        admitted: boolean;
    };
    requested_settings: MolecularDynamicsRequestedSettings;
    effective_request: Record<string, unknown>;
    warnings: Array<{ code: string; message: string }>;
    blockers: Array<{ code: string; message: string }>;
    preview_digest: string;
}

export interface MolecularDynamicsJobSpecV1 {
    schema: 'bms.md.job.v1';
    job_id: string;
    engine: MolecularDynamicsEngine;
    replicas: number;
    random_seed: number;
    input: { structure: string } | { coordinates: string; topology: string };
    preparation: {
        chemistry_assurance: 'smoke_fixture' | 'external_unreviewed';
        chemistry_profile_id?: string;
        chemistry_profile_sha256?: string;
        chemistry_profile_scope?: string;
        force_field: string;
        water_model: string;
        box_type: 'dodecahedron';
        padding_nm: number;
        salt_molar: number;
        positive_ion: 'NA';
        negative_ion: 'CL';
        solvent_group: 'SOL';
        solvent_coordinates: 'spc216.gro';
        neutralize: true;
    };
    stages: {
        minimization: { enabled: boolean; steps: number; force_tolerance_kj_mol_nm: number };
        nvt: { enabled: boolean; steps: number; temperature_k: number };
        npt: { enabled: boolean; steps: number; temperature_k: number; pressure_bar: number };
        production: {
            enabled: true;
            steps: number;
            timestep_fs: number;
            temperature_k: number;
            pressure_bar: number;
            checkpoint_interval_minutes: number;
            trajectory_interval_steps: number;
            energy_interval_steps: number;
        };
    };
    execution: {
        gpu_id: '0';
        ntmpi: 1;
        ntomp: number;
        gpu_offload: 'full';
        pin: 'on';
    };
}

export interface MolecularDynamicsJobSpecV2 {
    schema: 'bms.md.job.v2';
    job_id: string;
    engine: 'gromacs';
    replicas: number;
    random_seed: number;
    input: { structure: string };
    chemistry: {
        profile_id: string;
        profile_sha256: string;
        catalog_digest: string;
        requested_scope: string;
    };
    preparation: {
        box_type: 'dodecahedron';
        padding_nm: number;
        salt_molar: number;
        neutralize: true;
    };
    stages: MolecularDynamicsJobSpecV1['stages'];
    execution: {
        gpu_id: '0';
        ntmpi: 1;
        ntomp: number;
        gpu_offload: 'full_forces';
        pin: 'on';
    };
}

export type MolecularDynamicsJobSpec = MolecularDynamicsJobSpecV1 | MolecularDynamicsJobSpecV2;

const finitePositive = (value: number) => Number.isFinite(value) && value > 0;
const stepsFromPs = (picoseconds: number, timestepFs: number) => Math.round((picoseconds * 1000) / timestepFs);
const stepsFromNs = (nanoseconds: number, timestepFs: number) => Math.round((nanoseconds * 1_000_000) / timestepFs);

export const validateMolecularDynamicsForm = (
    form: MolecularDynamicsForm,
    chemistryProfile?: MolecularDynamicsChemistryProfile,
): string[] => {
    const errors: string[] = [];
    if (!form.jobName.trim()) errors.push('Job name is required.');
    if (form.inputMode === 'structure' && !form.structurePath.trim()) {
        errors.push('A starting PDB structure is required.');
    }
    if (form.inputMode === 'prepared' && (!form.coordinatesPath.trim() || !form.topologyPath.trim())) {
        errors.push('Prepared coordinates and topology are both required.');
    }
    if (form.inputMode === 'prepared' && form.engine !== 'openmm') {
        errors.push('Prepared coordinates and topology are supported only by OpenMM.');
    }
    if (form.engine === 'openmm' && form.inputMode !== 'prepared') {
        errors.push('The OpenMM alpha adapter requires prepared coordinates and topology.');
    }
    if (!Number.isInteger(form.replicas) || form.replicas < 1 || form.replicas > 16) {
        errors.push('Replica count must be an integer from 1 to 16.');
    }
    if (!finitePositive(form.productionNs) || form.productionNs > 10_000) {
        errors.push('Production duration must be greater than 0 and no more than 10,000 ns per replica.');
    }
    if (!Number.isInteger(form.randomSeed) || form.randomSeed < 1) errors.push('Random seed must be a positive integer.');
    if (form.timestepFs !== 2) errors.push('The validated phase-1 timestep is fixed at 2 fs.');
    if (!finitePositive(form.temperatureK) || form.temperatureK > 500) errors.push('Temperature must be between 0 and 500 K.');
    if (!finitePositive(form.pressureBar) || form.pressureBar > 100) errors.push('Pressure must be between 0 and 100 bar.');
    if (!finitePositive(form.trajectoryIntervalPs)) errors.push('Trajectory interval must be greater than 0 ps.');
    if (!finitePositive(form.energyIntervalPs)) errors.push('Energy interval must be greater than 0 ps.');
    if (finitePositive(form.productionNs) && finitePositive(form.trajectoryIntervalPs)
        && stepsFromPs(form.trajectoryIntervalPs, form.timestepFs) > stepsFromNs(form.productionNs, form.timestepFs)) {
        errors.push('Trajectory interval must not exceed the production duration.');
    }
    if (finitePositive(form.productionNs) && finitePositive(form.energyIntervalPs)
        && stepsFromPs(form.energyIntervalPs, form.timestepFs) > stepsFromNs(form.productionNs, form.timestepFs)) {
        errors.push('Energy interval must not exceed the production duration.');
    }
    if (!finitePositive(form.checkpointIntervalMinutes)) errors.push('Checkpoint interval must be greater than 0 minutes.');
    if (!Number.isInteger(form.minimizationSteps) || form.minimizationSteps < 1) errors.push('Minimization steps must be a positive integer.');
    if (!finitePositive(form.nvtPs) || !finitePositive(form.nptPs)) errors.push('NVT and NPT durations must be greater than 0 ps.');
    if (!Number.isInteger(form.ntomp) || form.ntomp < 1 || form.ntomp > 64) errors.push('CPU threads must be an integer from 1 to 64.');

    const constraints = form.inputMode === 'structure' ? chemistryProfile?.launch_constraints : undefined;
    if (form.inputMode === 'structure' && chemistryProfile && !constraints) {
        errors.push(`Chemistry profile ${chemistryProfile.display_name} has no selectable launch constraints.`);
    }
    if (constraints) {
        const productionSteps = stepsFromNs(form.productionNs, form.timestepFs);
        const nvtSteps = stepsFromPs(form.nvtPs, form.timestepFs);
        const nptSteps = stepsFromPs(form.nptPs, form.timestepFs);
        if (form.replicas !== constraints.replicas) errors.push(`Smoke replica count is fixed at ${constraints.replicas}.`);
        if (form.engine !== constraints.engine) errors.push(`Smoke engine is fixed at ${constraints.engine}.`);
        if (form.timestepFs !== constraints.timestep_fs) errors.push(`Smoke timestep is fixed at ${constraints.timestep_fs} fs.`);
        if (form.temperatureK !== constraints.temperature_k) errors.push(`Smoke temperature is fixed at ${constraints.temperature_k} K.`);
        if (form.pressureBar !== constraints.pressure_bar) errors.push(`Smoke pressure is fixed at ${constraints.pressure_bar} bar.`);
        if (form.saltMolar !== constraints.salt_molar) errors.push(`Smoke salt concentration is fixed at ${constraints.salt_molar} M.`);
        if (form.paddingNm !== constraints.padding_nm) errors.push(`Smoke box padding is fixed at ${constraints.padding_nm} nm.`);
        if (productionSteps > constraints.max_production_steps) errors.push(`Smoke production is limited to ${constraints.max_production_steps} steps.`);
        if (form.minimizationSteps > constraints.max_minimization_steps) errors.push(`Smoke minimization is limited to ${constraints.max_minimization_steps} steps.`);
        if (nvtSteps > constraints.max_nvt_steps) errors.push(`Smoke NVT is limited to ${constraints.max_nvt_steps} steps.`);
        if (nptSteps > constraints.max_npt_steps) errors.push(`Smoke NPT is limited to ${constraints.max_npt_steps} steps.`);
    }
    return errors;
};

export const validateMolecularDynamicsChemistryProfile = (
    profile: MolecularDynamicsChemistryProfile | undefined,
    engine: MolecularDynamicsEngine,
    automaticPreparationRequired = true,
    savedProfileDigest?: string,
): string[] => {
    if (!profile) {
        return ['The selected chemistry profile is stale or no longer present in the deployed catalog.'];
    }
    if (!profile.states.selectable) {
        return [`Chemistry profile ${profile.display_name} is a candidate and is not selectable. ${profile.availability_explanation}`];
    }
    if (savedProfileDigest !== undefined && savedProfileDigest !== profile.profile_sha256) {
        return ['The saved chemistry profile digest is stale; explicitly reselect the profile before launch.'];
    }
    if (automaticPreparationRequired && !profile.automatic_preparation) {
        return [`Chemistry profile ${profile.display_name} does not support automatic preparation.`];
    }
    if (!profile.supported_engines.includes(engine)) {
        return [`Chemistry profile ${profile.display_name} is not validated for ${engine}.`];
    }
    if (!profile.scientific_validation.scope.launch_scope) {
        return [`Chemistry profile ${profile.display_name} has no explicit validation scope.`];
    }
    if (automaticPreparationRequired && !profile.launch_constraints) {
        return [`Chemistry profile ${profile.display_name} has no selectable launch constraints.`];
    }
    return [];
};

export const applyMolecularDynamicsProfileDefaults = (
    form: MolecularDynamicsForm,
    profile: MolecularDynamicsChemistryProfile,
): MolecularDynamicsForm => {
    const constraints = profile.launch_constraints;
    if (!constraints) return { ...form };
    return {
        ...form,
        engine: constraints.engine,
        replicas: constraints.replicas,
        paddingNm: constraints.padding_nm,
        saltMolar: constraints.salt_molar,
        temperatureK: constraints.temperature_k,
        pressureBar: constraints.pressure_bar,
        timestepFs: constraints.timestep_fs,
        forceField: constraints.force_field,
        waterModel: constraints.water_model,
    };
};

export const molecularDynamicsRequestedSettings = (
    form: MolecularDynamicsForm,
): MolecularDynamicsRequestedSettings => ({
    replicas: form.replicas,
    random_seed: form.randomSeed,
    padding_nm: form.paddingNm,
    salt_molar: form.saltMolar,
    neutralize: form.neutralize !== false,
    temperature_k: form.temperatureK,
    pressure_bar: form.pressureBar,
    timestep_fs: form.timestepFs,
    minimization_steps: form.minimizationSteps,
    nvt_ps: form.nvtPs,
    npt_ps: form.nptPs,
    production_ns: form.productionNs,
    trajectory_interval_ps: form.trajectoryIntervalPs,
    energy_interval_ps: form.energyIntervalPs,
    checkpoint_interval_minutes: form.checkpointIntervalMinutes,
    ntomp: form.ntomp,
});

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/;

const wireRecord = (value: unknown, contract: string): Record<string, unknown> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Invalid ${contract} response.`);
    return value as Record<string, unknown>;
};

const exactWireKeys = (record: Record<string, unknown>, keys: readonly string[], contract: string) => {
    const actual = Object.keys(record).sort();
    const expected = [...keys].sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
        throw new Error(`Invalid ${contract} response.`);
    }
};

const closedWireKeys = (record: Record<string, unknown>, required: readonly string[], optional: readonly string[], contract: string) => {
    const actual = Object.keys(record);
    const allowed = new Set([...required, ...optional]);
    if (required.some((key) => !(key in record)) || actual.some((key) => !allowed.has(key))) {
        throw new Error(`Invalid ${contract} response.`);
    }
};

const wireString = (value: unknown, contract: string, allowNull = false) => {
    if (allowNull && value === null) return;
    if (typeof value !== 'string') throw new Error(`Invalid ${contract} response.`);
};

const wireFinite = (value: unknown, contract: string) => {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`Invalid ${contract} response.`);
};

const parseSourceRef = (value: unknown, contract: string): MolecularDynamicsStartingStructureRef => {
    const record = wireRecord(value, contract);
    exactWireKeys(record, ['kind', 'id'], contract);
    const kinds: MolecularDynamicsStartingStructureKind[] = ['rcsb', 'design', 'upload', 'managed_fixture', 'prior_md_input', 'server_file'];
    if (!kinds.includes(record.kind as MolecularDynamicsStartingStructureKind) || typeof record.id !== 'string' || !record.id.trim()) {
        throw new Error(`Invalid ${contract} response.`);
    }
    return record as unknown as MolecularDynamicsStartingStructureRef;
};

export const parseMolecularDynamicsChemistryProfileInventory = (value: unknown): MolecularDynamicsChemistryProfileInventory => {
    const contract = 'chemistry profile inventory';
    const root = wireRecord(value, contract);
    exactWireKeys(root, ['schema', 'catalog_digest', 'profiles', 'selectable_profile_ids', 'count', 'bounded'], contract);
    if (root.schema !== 'bms.md.chemistry-profile-inventory.v1' || !SHA256_RE.test(String(root.catalog_digest)) || root.bounded !== true) {
        throw new Error(`Invalid ${contract} response.`);
    }
    if (!Array.isArray(root.profiles) || !Array.isArray(root.selectable_profile_ids)
        || !root.selectable_profile_ids.every((id) => typeof id === 'string')
        || typeof root.count !== 'number' || !Number.isInteger(root.count) || root.count !== root.profiles.length) {
        throw new Error(`Invalid ${contract} response.`);
    }
    root.profiles.forEach((entry) => {
        const profile = wireRecord(entry, contract);
        closedWireKeys(profile, [
            'schema', 'id', 'version', 'profile_sha256', 'display_name', 'family', 'assurance', 'legacy', 'automatic_preparation',
            'inventory_class', 'availability_explanation', 'supported_engines', 'v1_preparation', 'launch_constraints',
            'scientific_validation', 'states', 'explicit_exclusions', 'runtime_identity',
        ], [], contract);
        if (profile.schema !== 'bms.md.chemistry-profile.v1') throw new Error(`Invalid ${contract} response.`);
        ['id', 'version', 'display_name', 'family', 'assurance', 'availability_explanation'].forEach((key) => wireString(profile[key], contract));
        if (!SHA256_RE.test(String(profile.profile_sha256)) || typeof profile.legacy !== 'boolean' || typeof profile.automatic_preparation !== 'boolean'
            || !['selectable', 'candidate'].includes(String(profile.inventory_class))) throw new Error(`Invalid ${contract} response.`);
        if (!Array.isArray(profile.supported_engines) || !profile.supported_engines.every((engine) => ['gromacs', 'openmm'].includes(String(engine)))) throw new Error(`Invalid ${contract} response.`);
        const v1 = wireRecord(profile.v1_preparation, contract);
        exactWireKeys(v1, ['force_field', 'water_model'], contract);
        wireString(v1.force_field, contract);
        wireString(v1.water_model, contract);
        if (profile.launch_constraints !== null) {
            const constraints = wireRecord(profile.launch_constraints, contract);
            exactWireKeys(constraints, [
                'input_mode', 'structure_sha256', 'replicas', 'engine', 'force_field', 'water_model', 'timestep_fs',
                'temperature_k', 'pressure_bar', 'salt_molar', 'padding_nm', 'max_production_steps', 'max_minimization_steps',
                'max_nvt_steps', 'max_npt_steps',
            ], contract);
            if (constraints.input_mode !== 'structure' || !SHA256_RE.test(String(constraints.structure_sha256))
                || !['gromacs', 'openmm'].includes(String(constraints.engine))) throw new Error(`Invalid ${contract} response.`);
            ['force_field', 'water_model'].forEach((key) => wireString(constraints[key], contract));
            ['replicas', 'timestep_fs', 'temperature_k', 'pressure_bar', 'salt_molar', 'padding_nm', 'max_production_steps', 'max_minimization_steps', 'max_nvt_steps', 'max_npt_steps']
                .forEach((key) => wireFinite(constraints[key], contract));
        }
        const validation = wireRecord(profile.scientific_validation, contract);
        exactWireKeys(validation, ['validated', 'lane', 'version', 'scope'], contract);
        if (typeof validation.validated !== 'boolean') throw new Error(`Invalid ${contract} response.`);
        wireString(validation.lane, contract, true);
        wireString(validation.version, contract, true);
        const scope = wireRecord(validation.scope, contract);
        closedWireKeys(scope, ['launch_scope', 'system_classes'], ['composition', 'protocol', 'ionic_conditions', 'observables'], contract);
        wireString(scope.launch_scope, contract);
        if (!Array.isArray(scope.system_classes) || !scope.system_classes.every((item) => typeof item === 'string')) throw new Error(`Invalid ${contract} response.`);
        ['composition', 'protocol', 'ionic_conditions'].forEach((key) => {
            if (key in scope) wireString(scope[key], contract);
        });
        if ('observables' in scope && (!Array.isArray(scope.observables) || !scope.observables.every((item) => typeof item === 'string'))) throw new Error(`Invalid ${contract} response.`);
        const states = wireRecord(profile.states, contract);
        exactWireKeys(states, ['installed', 'runtime_validated', 'scientifically_validated', 'operator_enabled', 'asset_probe_success', 'selectable'], contract);
        if (!Object.values(states).every((state) => typeof state === 'boolean')) throw new Error(`Invalid ${contract} response.`);
        if (!Array.isArray(profile.explicit_exclusions) || !profile.explicit_exclusions.every((item) => typeof item === 'string')) throw new Error(`Invalid ${contract} response.`);
        const runtimeIdentity = wireRecord(profile.runtime_identity, contract);
        exactWireKeys(runtimeIdentity, ['runtime_id', 'runtime_version', 'sif_sha256'], contract);
        wireString(runtimeIdentity.runtime_id, contract);
        wireString(runtimeIdentity.runtime_version, contract, true);
        if (runtimeIdentity.sif_sha256 !== null && !SHA256_RE.test(String(runtimeIdentity.sif_sha256))) throw new Error(`Invalid ${contract} response.`);
    });
    return value as MolecularDynamicsChemistryProfileInventory;
};

export const parseMolecularDynamicsServerFilePage = (value: unknown): MolecularDynamicsServerFilePage => {
    const contract = 'server-file inventory';
    const root = wireRecord(value, contract);
    exactWireKeys(root, ['items', 'next_cursor', 'count'], contract);
    if (!Array.isArray(root.items) || typeof root.count !== 'number' || !Number.isInteger(root.count)
        || root.count < 0 || root.count !== root.items.length) throw new Error(`Invalid ${contract} response.`);
    wireString(root.next_cursor, contract, true);
    root.items.forEach((entry) => {
        const item = wireRecord(entry, contract);
        exactWireKeys(item, ['id', 'label', 'format', 'bytes'], contract);
        if (typeof item.id !== 'string' || !item.id || item.id.length > 2048
            || typeof item.label !== 'string' || !item.label || item.label.length > 512
            || !['pdb', 'cif'].includes(String(item.format))
            || typeof item.bytes !== 'number' || !Number.isInteger(item.bytes) || item.bytes < 0) {
            throw new Error(`Invalid ${contract} response.`);
        }
    });
    return value as MolecularDynamicsServerFilePage;
};

export const parseMolecularDynamicsStartingStructureInspection = (
    value: unknown,
    expectedSource?: MolecularDynamicsStartingStructureRef,
    expectedProfileId?: string,
): MolecularDynamicsStartingStructureInspection => {
    const contract = 'starting-structure inspection';
    const root = wireRecord(value, contract);
    exactWireKeys(root, ['schema_version', 'source_ref', 'identity', 'viewer', 'inspection', 'admission'], contract);
    if (root.schema_version !== 'bms.md.starting-structure-inspection.v1') throw new Error(`Invalid ${contract} response.`);
    const sourceRef = parseSourceRef(root.source_ref, contract);
    if (expectedSource && (sourceRef.kind !== expectedSource.kind || sourceRef.id !== expectedSource.id)) {
        throw new Error(`Invalid ${contract} response.`);
    }
    const identity = wireRecord(root.identity, contract);
    exactWireKeys(identity, ['label', 'format', 'size_bytes', 'sha256', 'pdb_id', 'producer_job_id', 'design_id'], contract);
    wireString(identity.label, contract);
    if (!['pdb', 'cif'].includes(String(identity.format))) throw new Error(`Invalid ${contract} response.`);
    wireFinite(identity.size_bytes, contract);
    if (!SHA256_RE.test(String(identity.sha256))) throw new Error(`Invalid ${contract} response.`);
    wireString(identity.pdb_id, contract, true);
    wireString(identity.producer_job_id, contract, true);
    wireString(identity.design_id, contract, true);
    const viewer = wireRecord(root.viewer, contract);
    exactWireKeys(viewer, ['url', 'format', 'sha256'], contract);
    wireString(viewer.url, contract);
    if (!['pdb', 'cif'].includes(String(viewer.format)) || viewer.format !== identity.format || viewer.sha256 !== identity.sha256) {
        throw new Error(`Invalid ${contract} response.`);
    }
    const summary = wireRecord(root.inspection, contract);
    exactWireKeys(summary, ['model_count', 'chains', 'atom_count', 'hetero_components', 'parser'], contract);
    wireFinite(summary.model_count, contract);
    wireFinite(summary.atom_count, contract);
    if (!Array.isArray(summary.chains) || !summary.chains.every((entry) => typeof entry === 'string')) throw new Error(`Invalid ${contract} response.`);
    if (!Array.isArray(summary.hetero_components) || !summary.hetero_components.every((entry) => typeof entry === 'string')) throw new Error(`Invalid ${contract} response.`);
    const parser = wireRecord(summary.parser, contract);
    exactWireKeys(parser, ['name', 'version'], contract);
    if (parser.name !== 'biopython') throw new Error(`Invalid ${contract} response.`);
    wireString(parser.version, contract);
    const admission = wireRecord(root.admission, contract);
    exactWireKeys(admission, ['state', 'profile_id', 'code', 'message'], contract);
    if (!['profile_required', 'admitted', 'blocked'].includes(String(admission.state))) throw new Error(`Invalid ${contract} response.`);
    wireString(admission.profile_id, contract, true);
    wireString(admission.code, contract, true);
    wireString(admission.message, contract);
    if (expectedProfileId && admission.profile_id !== expectedProfileId) throw new Error(`Invalid ${contract} response.`);
    return value as MolecularDynamicsStartingStructureInspection;
};

export const parseMolecularDynamicsPredictionSourceCandidates = (
    value: unknown,
    expectedJobId?: string,
): MolecularDynamicsPredictionSourceCandidatePage => {
    const contract = 'prediction source candidates';
    const root = wireRecord(value, contract);
    exactWireKeys(root, ['schema_version', 'job', 'candidates', 'next_cursor'], contract);
    if (root.schema_version !== 'bms.md.prediction-source-candidates.v1') throw new Error(`Invalid ${contract} response.`);
    const job = wireRecord(root.job, contract);
    exactWireKeys(job, ['id', 'name', 'status', 'model_id', 'mode', 'created_at', 'started_at', 'completed_at', 'failure'], contract);
    if (typeof job.id !== 'string' || !UUID_RE.test(job.id) || (expectedJobId && job.id !== expectedJobId)) throw new Error(`Invalid ${contract} response.`);
    wireString(job.name, contract);
    wireString(job.model_id, contract);
    wireString(job.mode, contract);
    if (!['queued', 'preparing', 'running', 'awaiting_input', 'completed', 'failed', 'cancelled'].includes(String(job.status))) throw new Error(`Invalid ${contract} response.`);
    ['created_at', 'started_at', 'completed_at'].forEach((key) => wireString(job[key], contract, true));
    if (job.failure !== null) {
        const failure = wireRecord(job.failure, contract);
        exactWireKeys(failure, ['code', 'message'], contract);
        wireString(failure.code, contract);
        wireString(failure.message, contract);
    }
    if (!Array.isArray(root.candidates)) throw new Error(`Invalid ${contract} response.`);
    root.candidates.forEach((entry) => {
        const candidate = wireRecord(entry, contract);
        exactWireKeys(candidate, ['source_ref', 'name', 'format', 'eligible', 'blocker_code', 'metrics', 'created_at'], contract);
        const sourceRef = parseSourceRef(candidate.source_ref, contract);
        if (sourceRef.kind !== 'design' || !UUID_RE.test(sourceRef.id)) throw new Error(`Invalid ${contract} response.`);
        wireString(candidate.name, contract);
        if (!['pdb', 'cif'].includes(String(candidate.format)) || typeof candidate.eligible !== 'boolean') throw new Error(`Invalid ${contract} response.`);
        wireString(candidate.blocker_code, contract, true);
        wireString(candidate.created_at, contract, true);
        const metrics = wireRecord(candidate.metrics, contract);
        exactWireKeys(metrics, ['plddt', 'ptm', 'iptm', 'confidence'], contract);
        ['plddt', 'ptm', 'iptm', 'confidence'].forEach((key) => {
            if (metrics[key] !== null) wireFinite(metrics[key], contract);
        });
    });
    wireString(root.next_cursor, contract, true);
    return value as MolecularDynamicsPredictionSourceCandidatePage;
};

const REQUESTED_SETTING_KEYS = [
    'replicas', 'random_seed', 'padding_nm', 'salt_molar', 'neutralize', 'temperature_k', 'pressure_bar',
    'timestep_fs', 'minimization_steps', 'nvt_ps', 'npt_ps', 'production_ns', 'trajectory_interval_ps',
    'energy_interval_ps', 'checkpoint_interval_minutes', 'ntomp',
] as const;

export const parseMolecularDynamicsLaunchPreview = (
    value: unknown,
    expectedIntent?: MolecularDynamicsLaunchIntent,
): MolecularDynamicsLaunchPreview => {
    const contract = 'launch preview';
    const root = wireRecord(value, contract);
    exactWireKeys(root, ['schema_version', 'source', 'chemistry', 'requested_settings', 'effective_request', 'warnings', 'blockers', 'preview_digest'], contract);
    if (root.schema_version !== 'bms.md.launch-preview.v1' || !SHA256_RE.test(String(root.preview_digest))) throw new Error(`Invalid ${contract} response.`);
    const source = wireRecord(root.source, contract);
    exactWireKeys(source, ['source_ref', 'label', 'format', 'size_bytes', 'sha256', 'pdb_id', 'producer_job_id', 'design_id'], contract);
    const sourceRef = parseSourceRef(source.source_ref, contract);
    wireString(source.label, contract);
    if (!['pdb', 'cif'].includes(String(source.format)) || !SHA256_RE.test(String(source.sha256))) throw new Error(`Invalid ${contract} response.`);
    wireFinite(source.size_bytes, contract);
    wireString(source.pdb_id, contract, true);
    wireString(source.producer_job_id, contract, true);
    wireString(source.design_id, contract, true);
    const chemistry = wireRecord(root.chemistry, contract);
    exactWireKeys(chemistry, ['profile_id', 'profile_sha256', 'catalog_digest', 'admitted'], contract);
    wireString(chemistry.profile_id, contract);
    if (!SHA256_RE.test(String(chemistry.profile_sha256)) || !SHA256_RE.test(String(chemistry.catalog_digest)) || typeof chemistry.admitted !== 'boolean') {
        throw new Error(`Invalid ${contract} response.`);
    }
    const settings = wireRecord(root.requested_settings, contract);
    exactWireKeys(settings, REQUESTED_SETTING_KEYS, contract);
    REQUESTED_SETTING_KEYS.forEach((key) => {
        if (key === 'neutralize') {
            if (typeof settings[key] !== 'boolean') throw new Error(`Invalid ${contract} response.`);
        } else wireFinite(settings[key], contract);
    });
    const effective = wireRecord(root.effective_request, contract);
    exactWireKeys(effective, ['engine', 'replicas', 'random_seed', 'preparation', 'stages', 'execution'], contract);
    if (effective.engine !== 'gromacs') throw new Error(`Invalid ${contract} response.`);
    wireFinite(effective.replicas, contract);
    wireFinite(effective.random_seed, contract);
    const preparation = wireRecord(effective.preparation, contract);
    exactWireKeys(preparation, ['box_type', 'padding_nm', 'salt_molar', 'neutralize'], contract);
    if (!['cubic', 'triclinic', 'dodecahedron', 'octahedron'].includes(String(preparation.box_type))) throw new Error(`Invalid ${contract} response.`);
    wireFinite(preparation.padding_nm, contract);
    wireFinite(preparation.salt_molar, contract);
    if (typeof preparation.neutralize !== 'boolean') throw new Error(`Invalid ${contract} response.`);
    const stages = wireRecord(effective.stages, contract);
    exactWireKeys(stages, ['minimization', 'nvt', 'npt', 'production'], contract);
    const stageContracts: Array<[string, readonly string[]]> = [
        ['minimization', ['enabled', 'steps', 'force_tolerance_kj_mol_nm']],
        ['nvt', ['enabled', 'steps', 'temperature_k']],
        ['npt', ['enabled', 'steps', 'temperature_k', 'pressure_bar']],
        ['production', ['enabled', 'steps', 'timestep_fs', 'temperature_k', 'pressure_bar', 'checkpoint_interval_minutes', 'trajectory_interval_steps', 'energy_interval_steps']],
    ];
    stageContracts.forEach(([stageName, keys]) => {
        const stage = wireRecord(stages[stageName], contract);
        exactWireKeys(stage, keys, contract);
        if (typeof stage.enabled !== 'boolean') throw new Error(`Invalid ${contract} response.`);
        keys.filter((key) => key !== 'enabled').forEach((key) => wireFinite(stage[key], contract));
    });
    const execution = wireRecord(effective.execution, contract);
    exactWireKeys(execution, ['ntmpi', 'ntomp', 'gpu_offload', 'pin', 'placement_authority'], contract);
    if (execution.ntmpi !== 1 || typeof execution.ntomp !== 'number' || !Number.isInteger(execution.ntomp) || execution.ntomp < 1
        || execution.gpu_offload !== 'full' || execution.pin !== 'on' || execution.placement_authority !== 'global_scheduler') {
        throw new Error(`Invalid ${contract} response.`);
    }
    for (const field of ['warnings', 'blockers'] as const) {
        if (!Array.isArray(root[field])) throw new Error(`Invalid ${contract} response.`);
        root[field].forEach((notice) => {
            const record = wireRecord(notice, contract);
            exactWireKeys(record, ['code', 'message'], contract);
            wireString(record.code, contract);
            wireString(record.message, contract);
        });
    }
    if (expectedIntent) {
        if (sourceRef.kind !== expectedIntent.source_ref.kind || sourceRef.id !== expectedIntent.source_ref.id
            || source.sha256 !== expectedIntent.expected_source_sha256
            || chemistry.profile_id !== expectedIntent.chemistry_profile_id
            || chemistry.profile_sha256 !== expectedIntent.chemistry_profile_sha256
            || chemistry.catalog_digest !== expectedIntent.catalog_digest
            || chemistry.admitted !== true
            || REQUESTED_SETTING_KEYS.some((key) => !Object.is(settings[key], expectedIntent.requested_settings[key]))) {
            throw new Error(`Invalid ${contract} response.`);
        }
    }
    return value as MolecularDynamicsLaunchPreview;
};

export const buildMolecularDynamicsLaunchIntent = ({
    form,
    source,
    profile,
    catalogDigest,
    launchContextId,
}: {
    form: MolecularDynamicsForm;
    source: Pick<MolecularDynamicsStartingStructureInspection, 'source_ref' | 'identity'>;
    profile: MolecularDynamicsChemistryProfile;
    catalogDigest: string;
    launchContextId?: string | null;
}): MolecularDynamicsLaunchIntent => {
    if (!form.jobName.trim()) throw new Error('Job name is required.');
    if (!SHA256_RE.test(source.identity.sha256)) throw new Error('Inspected starting-structure SHA-256 is invalid.');
    if (!SHA256_RE.test(profile.profile_sha256) || !SHA256_RE.test(catalogDigest)) {
        throw new Error('Chemistry profile or catalog identity is invalid.');
    }
    const errors = [
        ...validateMolecularDynamicsForm({ ...form, structurePath: 'typed-starting-structure' }, profile),
        ...validateMolecularDynamicsChemistryProfile(profile, form.engine, true),
    ];
    if (errors.length) throw new Error(errors.join(' '));
    return {
        schema_version: 'bms.md.launch-intent.v1',
        name: form.jobName.trim(),
        source_ref: { ...source.source_ref },
        expected_source_sha256: source.identity.sha256,
        chemistry_profile_id: profile.id,
        chemistry_profile_sha256: profile.profile_sha256,
        catalog_digest: catalogDigest,
        requested_settings: molecularDynamicsRequestedSettings(form),
        launch_context_id: launchContextId ?? null,
    };
};

export const resolveMolecularDynamicsCloneSource = (
    initialValues: Record<string, unknown>,
): MolecularDynamicsStartingStructureRef | null => {
    const sourceDesignId = typeof initialValues.source_design_id === 'string'
        ? initialValues.source_design_id.trim()
        : '';
    if (UUID_RE.test(sourceDesignId)) return { kind: 'design', id: sourceDesignId };
    const sourceJobId = typeof initialValues.source_job_id === 'string'
        ? initialValues.source_job_id.trim()
        : '';
    if (UUID_RE.test(sourceJobId)) return { kind: 'prior_md_input', id: sourceJobId };
    const intent = initialValues.intent;
    const sourceRef = intent && typeof intent === 'object' && !Array.isArray(intent)
        ? (intent as Record<string, unknown>).source_ref
        : initialValues.source_ref;
    if (!sourceRef || typeof sourceRef !== 'object' || Array.isArray(sourceRef)) return null;
    const kind = (sourceRef as Record<string, unknown>).kind;
    const id = (sourceRef as Record<string, unknown>).id;
    if (kind !== 'design' || typeof id !== 'string' || !UUID_RE.test(id)) return null;
    return { kind, id };
};

export const buildMolecularDynamicsPredictionHandoff = (
    sequence: string,
    name: string,
): Record<string, unknown> => {
    const normalizedSequence = sequence.toUpperCase().replace(/\s+/g, '');
    const normalizedName = name.trim() || 'MD starting structure';
    if (!normalizedSequence || !/^[ACDEFGHIKLMNPQRSTVWY]+$/.test(normalizedSequence)) {
        throw new Error('A valid canonical protein sequence is required for Structure Prediction.');
    }
    const jobName = `${normalizedName} structure prediction`;
    return {
        name: jobName,
        job_name: jobName,
        sequence: normalizedSequence,
        sequence_name: normalizedName,
        md_return_template: 'molecular_dynamics',
    };
};

export const buildMolecularDynamicsJobSpec = (
    form: MolecularDynamicsForm,
    chemistryProfile?: MolecularDynamicsChemistryProfile,
    catalogDigest?: string,
): MolecularDynamicsJobSpec => {
    const automaticPreparation = form.inputMode === 'structure';
    const errors = [
        ...validateMolecularDynamicsForm(form, automaticPreparation ? chemistryProfile : undefined),
        ...(automaticPreparation
            ? validateMolecularDynamicsChemistryProfile(chemistryProfile, form.engine, true)
            : []),
    ];
    if (errors.length) throw new Error(errors.join(' '));
    if (automaticPreparation && !chemistryProfile) throw new Error('A chemistry profile is required for automatic preparation.');
    if (automaticPreparation && !chemistryProfile!.legacy && !/^[0-9a-f]{64}$/.test(catalogDigest || '')) {
        throw new Error('A valid deployed chemistry catalog digest is required for automatic preparation.');
    }
    const stages: MolecularDynamicsJobSpecV1['stages'] = {
        minimization: { enabled: form.engine === 'gromacs', steps: form.minimizationSteps, force_tolerance_kj_mol_nm: 1000 },
        nvt: { enabled: form.engine === 'gromacs', steps: stepsFromPs(form.nvtPs, form.timestepFs), temperature_k: form.temperatureK },
        npt: { enabled: form.engine === 'gromacs', steps: stepsFromPs(form.nptPs, form.timestepFs), temperature_k: form.temperatureK, pressure_bar: form.pressureBar },
        production: {
            enabled: true,
            steps: stepsFromNs(form.productionNs, form.timestepFs),
            timestep_fs: form.timestepFs,
            temperature_k: form.temperatureK,
            pressure_bar: form.pressureBar,
            checkpoint_interval_minutes: form.checkpointIntervalMinutes,
            trajectory_interval_steps: stepsFromPs(form.trajectoryIntervalPs, form.timestepFs),
            energy_interval_steps: stepsFromPs(form.energyIntervalPs, form.timestepFs),
        },
    };
    if (automaticPreparation && chemistryProfile!.legacy) {
        return {
            schema: 'bms.md.job.v1',
            job_id: 'assigned-by-bms',
            engine: form.engine,
            replicas: form.replicas,
            random_seed: form.randomSeed,
            input: { structure: form.structurePath.trim() },
            preparation: {
                chemistry_assurance: 'smoke_fixture',
                chemistry_profile_id: chemistryProfile!.id,
                chemistry_profile_sha256: chemistryProfile!.profile_sha256,
                chemistry_profile_scope: chemistryProfile!.scientific_validation.scope.launch_scope,
                force_field: chemistryProfile!.v1_preparation.force_field,
                water_model: chemistryProfile!.v1_preparation.water_model,
                box_type: 'dodecahedron', padding_nm: form.paddingNm,
                salt_molar: form.saltMolar, positive_ion: 'NA', negative_ion: 'CL',
                solvent_group: 'SOL', solvent_coordinates: 'spc216.gro', neutralize: true,
            },
            stages,
            execution: { gpu_id: '0', ntmpi: 1, ntomp: form.ntomp, gpu_offload: 'full', pin: 'on' },
        };
    }
    if (automaticPreparation) {
        return {
            schema: 'bms.md.job.v2',
            job_id: 'assigned-by-bms',
            engine: 'gromacs',
            replicas: form.replicas,
            random_seed: form.randomSeed,
            input: { structure: form.structurePath.trim() },
            chemistry: {
                profile_id: chemistryProfile!.id,
                profile_sha256: chemistryProfile!.profile_sha256,
                catalog_digest: catalogDigest!,
                requested_scope: chemistryProfile!.scientific_validation.scope.launch_scope,
            },
            preparation: {
                box_type: 'dodecahedron', padding_nm: form.paddingNm,
                salt_molar: form.saltMolar, neutralize: true,
            },
            stages,
            execution: { gpu_id: '0', ntmpi: 1, ntomp: form.ntomp, gpu_offload: 'full_forces', pin: 'on' },
        };
    }
    return {
        schema: 'bms.md.job.v1',
        job_id: 'assigned-by-bms',
        engine: form.engine,
        replicas: form.replicas,
        random_seed: form.randomSeed,
        input: { coordinates: form.coordinatesPath.trim(), topology: form.topologyPath.trim() },
        preparation: {
            chemistry_assurance: 'external_unreviewed', force_field: 'external', water_model: 'external',
            box_type: 'dodecahedron', padding_nm: form.paddingNm, salt_molar: form.saltMolar,
            positive_ion: 'NA', negative_ion: 'CL', solvent_group: 'SOL',
            solvent_coordinates: 'spc216.gro', neutralize: true,
        },
        stages,
        execution: { gpu_id: '0', ntmpi: 1, ntomp: form.ntomp, gpu_offload: 'full', pin: 'on' },
    };
};

export const estimateMolecularDynamicsScope = (form: MolecularDynamicsForm) => {
    const productionStepsPerReplica = stepsFromNs(form.productionNs, form.timestepFs);
    const trajectoryIntervalSteps = stepsFromPs(form.trajectoryIntervalPs, form.timestepFs);
    const trajectoryFramesPerReplica = Math.floor(productionStepsPerReplica / trajectoryIntervalSteps);
    return {
        aggregateSimulationNs: form.productionNs * form.replicas,
        productionStepsPerReplica,
        trajectoryFramesPerReplica,
        totalTrajectoryFrames: trajectoryFramesPerReplica * form.replicas,
    };
};
