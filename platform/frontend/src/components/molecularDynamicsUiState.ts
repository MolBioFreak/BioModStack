export type MolecularDynamicsEngine = 'gromacs' | 'openmm';
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
    explicit_exclusions?: string[];
}

export interface MolecularDynamicsChemistryProfileInventory {
    schema: 'bms.md.chemistry-profile-inventory.v1';
    catalog_digest: string;
    profiles: MolecularDynamicsChemistryProfile[];
    selectable_profile_ids: string[];
    count: number;
    bounded: true;
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
}

export interface MolecularDynamicsJobSpec {
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

export const buildMolecularDynamicsJobSpec = (
    form: MolecularDynamicsForm,
    chemistryProfile?: MolecularDynamicsChemistryProfile,
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
    const input = form.inputMode === 'prepared'
        ? { coordinates: form.coordinatesPath.trim(), topology: form.topologyPath.trim() }
        : { structure: form.structurePath.trim() };
    const preparation = automaticPreparation && chemistryProfile ? {
        chemistry_assurance: 'smoke_fixture' as const,
        chemistry_profile_id: chemistryProfile.id,
        chemistry_profile_sha256: chemistryProfile.profile_sha256,
        chemistry_profile_scope: chemistryProfile.scientific_validation.scope.launch_scope,
        force_field: chemistryProfile.v1_preparation.force_field,
        water_model: chemistryProfile.v1_preparation.water_model,
    } : {
        chemistry_assurance: 'external_unreviewed' as const,
        force_field: 'external',
        water_model: 'external',
    };
    return {
        schema: 'bms.md.job.v1',
        job_id: 'assigned-by-bms',
        engine: form.engine,
        replicas: form.replicas,
        random_seed: form.randomSeed,
        input,
        preparation: {
            ...preparation,
            box_type: 'dodecahedron',
            padding_nm: form.paddingNm,
            salt_molar: form.saltMolar,
            positive_ion: 'NA',
            negative_ion: 'CL',
            solvent_group: 'SOL',
            solvent_coordinates: 'spc216.gro',
            neutralize: true,
        },
        stages: {
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
        },
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
