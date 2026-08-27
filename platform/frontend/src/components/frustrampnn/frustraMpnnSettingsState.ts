export type FrustraMpnnProteinSelectionMode =
    | 'all_protein_entities'
    | 'selected_entities'
    | 'selected_regions'
    | 'selected_residues';
export type FrustraMpnnClassificationMode = 'canonical' | 'custom';

export interface FrustraMpnnEntitySelector {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string | null;
}

export interface FrustraMpnnResidueSelector extends Omit<FrustraMpnnEntitySelector, 'auth_asym_id'> {
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
}

export interface FrustraMpnnRegionSelector extends FrustraMpnnEntitySelector {
    sequence_start: number;
    sequence_end: number;
}

export type FrustraMpnnProteinSelection =
    | {
        mode: 'all_protein_entities';
        entities: [];
        regions: [];
        residues: [];
    }
    | {
        mode: 'selected_entities';
        entities: FrustraMpnnEntitySelector[];
        regions: [];
        residues: [];
    }
    | {
        mode: 'selected_regions';
        entities: [];
        regions: FrustraMpnnRegionSelector[];
        residues: [];
    }
    | {
        mode: 'selected_residues';
        entities: [];
        regions: [];
        residues: FrustraMpnnResidueSelector[];
    };

export interface FrustraMpnnSourceStructureSettings {
    selected_model_number: number;
    preferred_altloc: string;
}

export interface FrustraMpnnClassificationPolicy {
    mode: FrustraMpnnClassificationMode;
    high_max: number;
    minimal_min: number;
}

export interface FrustraMpnnRequestedSettings {
    schema_name: 'frustrampnn_settings';
    schema_version: 2;
    batching_enabled: boolean;
    structures_per_job: number;
    protein_selection: FrustraMpnnProteinSelection;
    source_structure: FrustraMpnnSourceStructureSettings;
    classification_policy: FrustraMpnnClassificationPolicy;
}

export interface FrustraMpnnInspectableEntity extends FrustraMpnnEntitySelector {
    pdb_chain_id: string | null;
}

export interface FrustraMpnnInspectableResidue extends FrustraMpnnResidueSelector {
    wt: string;
}

export interface FrustraMpnnSourceInspection {
    source_models: number[];
    selected_source_model: number;
    observed_altlocs: string[];
    selected_altloc: string;
    protein_entities: FrustraMpnnInspectableEntity[];
    protein_sequence_spans?: FrustraMpnnRegionSelector[];
    mapped_residues: FrustraMpnnInspectableResidue[];
}

export interface FrustraMpnnSelectionModeOption {
    mode: FrustraMpnnProteinSelectionMode;
    available: boolean;
    reason?: string;
}

const SETTINGS_KEYS = [
    'schema_name',
    'schema_version',
    'batching_enabled',
    'structures_per_job',
    'protein_selection',
    'source_structure',
    'classification_policy',
] as const;
const HISTORICAL_SETTINGS_V1_KEYS = SETTINGS_KEYS.filter(
    (key) => key !== 'batching_enabled' && key !== 'structures_per_job',
);
const SELECTION_KEYS = ['mode', 'entities', 'regions', 'residues'] as const;
const ENTITY_KEYS = ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id'] as const;
const REGION_KEYS = [...ENTITY_KEYS, 'sequence_start', 'sequence_end'] as const;
const RESIDUE_KEYS = [...ENTITY_KEYS, 'auth_seq_id', 'insertion_code', 'sequence_index'] as const;
const SOURCE_STRUCTURE_KEYS = ['selected_model_number', 'preferred_altloc'] as const;
const CLASSIFICATION_KEYS = ['mode', 'high_max', 'minimal_min'] as const;
const PROTEIN_SELECTION_MODES = new Set<FrustraMpnnProteinSelectionMode>([
    'all_protein_entities',
    'selected_entities',
    'selected_regions',
    'selected_residues',
]);

const requireRecord = (value: unknown, label: string): Record<string, unknown> => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        throw new Error(`${label} must be an object`);
    }
    return value as Record<string, unknown>;
};

const requireExactKeys = (
    value: Record<string, unknown>,
    keys: readonly string[],
    label: string,
): void => {
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
        const unknown = actual.filter((key) => !expected.includes(key));
        const missing = expected.filter((key) => !actual.includes(key));
        const details = [
            unknown.length > 0 ? `unknown keys: ${unknown.join(', ')}` : '',
            missing.length > 0 ? `missing keys: ${missing.join(', ')}` : '',
        ].filter(Boolean).join('; ');
        throw new Error(`${label} must contain exact keys${details ? ` (${details})` : ''}`);
    }
};

const requireNonEmptyString = (value: unknown, label: string): string => {
    if (typeof value !== 'string' || value.length === 0) {
        throw new Error(`${label} must be a non-empty string`);
    }
    return value;
};

const requireNullableNonEmptyString = (value: unknown, label: string): string | null => {
    if (value === null) return null;
    return requireNonEmptyString(value, label);
};

const requireInteger = (
    value: unknown,
    label: string,
    minimum?: number,
    maximum?: number,
): number => {
    if (typeof value !== 'number' || !Number.isInteger(value)) {
        throw new Error(`${label} must be an integer`);
    }
    if (minimum !== undefined && value < minimum) {
        throw new Error(`${label} must be at least ${minimum}`);
    }
    if (maximum !== undefined && value > maximum) {
        throw new Error(`${label} must be at most ${maximum}`);
    }
    return value;
};

const requireBoolean = (value: unknown, label: string): boolean => {
    if (typeof value !== 'boolean') throw new Error(`${label} must be a boolean`);
    return value;
};

const requireFiniteNumber = (value: unknown, label: string): number => {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`${label} must be finite`);
    }
    return value;
};

const requireAltloc = (value: unknown, label: string): string => {
    if (typeof value !== 'string' || !/^(?:|[A-Za-z0-9])$/.test(value)) {
        throw new Error(`${label} must be blank or one explicit alphanumeric altloc`);
    }
    return value;
};

const parseEntity = (value: unknown, label: string): FrustraMpnnEntitySelector => {
    const record = requireRecord(value, label);
    requireExactKeys(record, ENTITY_KEYS, label);
    return {
        entity_instance_id: requireNonEmptyString(record.entity_instance_id, `${label}.entity_instance_id`),
        source_entity_id: requireNullableNonEmptyString(record.source_entity_id, `${label}.source_entity_id`),
        label_asym_id: requireNullableNonEmptyString(record.label_asym_id, `${label}.label_asym_id`),
        auth_asym_id: requireNullableNonEmptyString(record.auth_asym_id, `${label}.auth_asym_id`),
    };
};

const parseResidue = (value: unknown, label: string): FrustraMpnnResidueSelector => {
    const record = requireRecord(value, label);
    requireExactKeys(record, RESIDUE_KEYS, label);
    const entity = parseEntity(
        Object.fromEntries(ENTITY_KEYS.map((key) => [key, record[key]])),
        label,
    );
    const insertionCode = record.insertion_code;
    if (typeof insertionCode !== 'string' || insertionCode.length > 1) {
        throw new Error(`${label}.insertion_code must be a string of at most one character`);
    }
    return {
        ...entity,
        auth_asym_id: requireNonEmptyString(record.auth_asym_id, `${label}.auth_asym_id`),
        auth_seq_id: requireInteger(record.auth_seq_id, `${label}.auth_seq_id`),
        insertion_code: insertionCode,
        sequence_index: requireInteger(record.sequence_index, `${label}.sequence_index`, 1),
    };
};

const parseRegion = (value: unknown, label: string): FrustraMpnnRegionSelector => {
    const record = requireRecord(value, label);
    requireExactKeys(record, REGION_KEYS, label);
    const entity = parseEntity(
        Object.fromEntries(ENTITY_KEYS.map((key) => [key, record[key]])),
        label,
    );
    const sequenceStart = requireInteger(record.sequence_start, `${label}.sequence_start`, 1);
    const sequenceEnd = requireInteger(record.sequence_end, `${label}.sequence_end`, 1);
    if (sequenceStart > sequenceEnd) {
        throw new Error(`${label}.sequence_start must be less than or equal to sequence_end`);
    }
    return { ...entity, sequence_start: sequenceStart, sequence_end: sequenceEnd };
};

const entityKey = (entity: FrustraMpnnEntitySelector): string => JSON.stringify([
    entity.entity_instance_id,
    entity.source_entity_id ?? '',
    entity.label_asym_id ?? '',
    entity.auth_asym_id,
]);

const residueKey = (residue: FrustraMpnnResidueSelector): string => JSON.stringify([
    entityKey(residue),
    residue.auth_seq_id,
    residue.insertion_code,
    residue.sequence_index,
]);

const regionKey = (region: FrustraMpnnRegionSelector): string => JSON.stringify([
    entityKey(region),
    region.sequence_start,
    region.sequence_end,
]);

const compareRegions = (left: FrustraMpnnRegionSelector, right: FrustraMpnnRegionSelector): number => {
    const entityOrder = entityKey(left).localeCompare(entityKey(right));
    if (entityOrder !== 0) return entityOrder;
    if (left.sequence_start !== right.sequence_start) return left.sequence_start - right.sequence_start;
    return left.sequence_end - right.sequence_end;
};

const parseArray = <T>(
    value: unknown,
    label: string,
    parseItem: (item: unknown, itemLabel: string) => T,
): T[] => {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
    return value.map((item, index) => parseItem(item, `${label}[${index}]`));
};

const ensureUnique = <T>(items: T[], key: (item: T) => string, label: string): void => {
    const keys = items.map(key);
    if (new Set(keys).size !== keys.length) {
        throw new Error(`${label} contains duplicate stable identity`);
    }
};

export const parseFrustraMpnnRequestedSettings = (value: unknown): FrustraMpnnRequestedSettings => {
    const settings = requireRecord(value, 'frustrampnn_settings');
    requireExactKeys(settings, SETTINGS_KEYS, 'frustrampnn_settings');
    if (settings.schema_name !== 'frustrampnn_settings' || settings.schema_version !== 2) {
        throw new Error('frustrampnn_settings schema identity must be frustrampnn_settings v2');
    }
    const batchingEnabled = requireBoolean(
        settings.batching_enabled,
        'frustrampnn_settings.batching_enabled',
    );
    const structuresPerJob = requireInteger(
        settings.structures_per_job,
        'frustrampnn_settings.structures_per_job',
        1,
        250,
    );

    const selectionRecord = requireRecord(settings.protein_selection, 'frustrampnn_settings.protein_selection');
    requireExactKeys(selectionRecord, SELECTION_KEYS, 'frustrampnn_settings.protein_selection');
    if (!PROTEIN_SELECTION_MODES.has(selectionRecord.mode as FrustraMpnnProteinSelectionMode)) {
        throw new Error('frustrampnn_settings.protein_selection.mode is unsupported');
    }
    const mode = selectionRecord.mode as FrustraMpnnProteinSelectionMode;
    const entities = parseArray(selectionRecord.entities, 'frustrampnn_settings.protein_selection.entities', parseEntity)
        .sort((left, right) => entityKey(left).localeCompare(entityKey(right)));
    const regions = parseArray(selectionRecord.regions, 'frustrampnn_settings.protein_selection.regions', parseRegion)
        .sort(compareRegions);
    const residues = parseArray(selectionRecord.residues, 'frustrampnn_settings.protein_selection.residues', parseResidue)
        .sort((left, right) => residueKey(left).localeCompare(residueKey(right)));
    ensureUnique(entities, (item) => item.entity_instance_id, 'selected entities');
    ensureUnique(regions, regionKey, 'selected regions');
    ensureUnique(
        residues,
        (item) => JSON.stringify([item.entity_instance_id, item.auth_asym_id, item.auth_seq_id, item.insertion_code]),
        'selected residues',
    );
    for (let index = 1; index < regions.length; index += 1) {
        const previous = regions[index - 1];
        const current = regions[index];
        if (
            entityKey(previous) === entityKey(current)
            && current.sequence_start <= previous.sequence_end
        ) {
            throw new Error('selected regions cannot overlap');
        }
    }

    let proteinSelection: FrustraMpnnProteinSelection;
    if (mode === 'all_protein_entities') {
        if (entities.length !== 0 || regions.length !== 0 || residues.length !== 0) {
            throw new Error('all_protein_entities cannot contain entity, region, or residue selectors');
        }
        proteinSelection = { mode, entities: [], regions: [], residues: [] };
    } else if (mode === 'selected_entities') {
        if (entities.length === 0 || regions.length !== 0 || residues.length !== 0) {
            throw new Error('selected_entities requires entities and cannot contain regions or residues');
        }
        proteinSelection = { mode, entities, regions: [], residues: [] };
    } else if (mode === 'selected_regions') {
        if (regions.length === 0 || entities.length !== 0 || residues.length !== 0) {
            throw new Error('selected_regions requires regions and cannot contain entities or residues');
        }
        proteinSelection = { mode, entities: [], regions, residues: [] };
    } else {
        if (residues.length === 0 || entities.length !== 0 || regions.length !== 0) {
            throw new Error('selected_residues requires residues and cannot contain entities or regions');
        }
        proteinSelection = { mode, entities: [], regions: [], residues };
    }

    const source = requireRecord(settings.source_structure, 'frustrampnn_settings.source_structure');
    requireExactKeys(source, SOURCE_STRUCTURE_KEYS, 'frustrampnn_settings.source_structure');
    const sourceStructure: FrustraMpnnSourceStructureSettings = {
        selected_model_number: requireInteger(
            source.selected_model_number,
            'frustrampnn_settings.source_structure.selected_model_number',
            1,
        ),
        preferred_altloc: requireAltloc(
            source.preferred_altloc,
            'frustrampnn_settings.source_structure.preferred_altloc',
        ),
    };

    const classification = requireRecord(
        settings.classification_policy,
        'frustrampnn_settings.classification_policy',
    );
    requireExactKeys(classification, CLASSIFICATION_KEYS, 'frustrampnn_settings.classification_policy');
    if (classification.mode !== 'canonical' && classification.mode !== 'custom') {
        throw new Error('frustrampnn_settings.classification_policy.mode is unsupported');
    }
    const classificationPolicy: FrustraMpnnClassificationPolicy = {
        mode: classification.mode,
        high_max: requireFiniteNumber(
            classification.high_max,
            'frustrampnn_settings.classification_policy.high_max',
        ),
        minimal_min: requireFiniteNumber(
            classification.minimal_min,
            'frustrampnn_settings.classification_policy.minimal_min',
        ),
    };
    if (classificationPolicy.high_max >= classificationPolicy.minimal_min) {
        throw new Error('classification thresholds require high_max < minimal_min');
    }
    if (
        classificationPolicy.mode === 'canonical'
        && (classificationPolicy.high_max !== -1 || classificationPolicy.minimal_min !== 0.58)
    ) {
        throw new Error('canonical classification mode requires high_max -1.0 and minimal_min 0.58');
    }

    return {
        schema_name: 'frustrampnn_settings',
        schema_version: 2,
        batching_enabled: batchingEnabled,
        structures_per_job: structuresPerJob,
        protein_selection: proteinSelection,
        source_structure: sourceStructure,
        classification_policy: classificationPolicy,
    };
};

export const CANONICAL_FRUSTRAMPNN_SETTINGS: FrustraMpnnRequestedSettings = Object.freeze(
    parseFrustraMpnnRequestedSettings({
        schema_name: 'frustrampnn_settings',
        schema_version: 2,
        batching_enabled: false,
        structures_per_job: 1,
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
    }),
);

export const hydrateFrustraMpnnSettings = (value: unknown): FrustraMpnnRequestedSettings => {
    if (value === undefined) return parseFrustraMpnnRequestedSettings(CANONICAL_FRUSTRAMPNN_SETTINGS);

    const persisted = requireRecord(value, 'persisted frustrampnn_settings');
    const selection = requireRecord(
        persisted.protein_selection,
        'persisted frustrampnn_settings.protein_selection',
    );
    const withRegions = Object.prototype.hasOwnProperty.call(selection, 'regions')
        ? persisted
        : { ...persisted, protein_selection: { ...selection, regions: [] } };
    const hasOrigin = Object.prototype.hasOwnProperty.call(persisted, 'settings_value_origin');
    const keys = persisted.schema_version === 1 ? HISTORICAL_SETTINGS_V1_KEYS : SETTINGS_KEYS;
    requireExactKeys(
        persisted,
        hasOrigin ? [...keys, 'settings_value_origin'] : keys,
        'persisted frustrampnn_settings',
    );
    if (
        hasOrigin
        && persisted.settings_value_origin !== 'bms_default'
        && persisted.settings_value_origin !== 'operator_request'
    ) {
        throw new Error('persisted frustrampnn_settings.settings_value_origin is invalid');
    }
    const normalized = Object.fromEntries(keys.map((key) => [key, withRegions[key]]));
    return parseFrustraMpnnRequestedSettings(persisted.schema_version === 1
        ? {
            ...normalized,
            schema_version: 2,
            batching_enabled: false,
            structures_per_job: 1,
        }
        : normalized);
};

export type FrustraMpnnLaunchParams =
    | {
        run_frustrampnn: true;
        frustrampnn_requiredness: 'required';
        frustrampnn_settings: FrustraMpnnRequestedSettings;
    }
    | {
        run_frustrampnn: false;
    };

export const buildFrustraMpnnLaunchParams = (
    enabled: boolean,
    settings: FrustraMpnnRequestedSettings,
): FrustraMpnnLaunchParams => {
    if (!enabled) return { run_frustrampnn: false };
    return {
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: parseFrustraMpnnRequestedSettings(settings),
    };
};

export const resolveFrustraMpnnWorkflowId = (
    modelId: unknown,
    modeId: unknown,
): 'protein_design' | 'complex_prediction' | null => {
    const normalizedModelId = typeof modelId === 'string' ? modelId.trim().toLowerCase() : '';
    const normalizedModeId = typeof modeId === 'string' ? modeId.trim().toLowerCase() : '';
    if (normalizedModelId === 'rfdiffusion' && normalizedModeId.length > 0) return 'protein_design';
    if (normalizedModelId === 'boltz2' && normalizedModeId === 'complex') return 'complex_prediction';
    return null;
};

const FRUSTRAMPNN_LAUNCH_KEYS = [
    'run_frustrampnn',
    'frustrampnn_requiredness',
    'frustrampnn_settings',
    'frustrampnn_settings_value_origin',
] as const;

export const mergeFrustraMpnnLaunchParams = <T extends Record<string, unknown>>(
    params: T,
    enabled: boolean,
    settings: FrustraMpnnRequestedSettings,
): Omit<T, typeof FRUSTRAMPNN_LAUNCH_KEYS[number]> & FrustraMpnnLaunchParams => {
    const normalized = { ...params };
    for (const key of FRUSTRAMPNN_LAUNCH_KEYS) delete normalized[key];
    return {
        ...normalized,
        ...buildFrustraMpnnLaunchParams(enabled, settings),
    };
};

export const getFrustraMpnnInspectionRegionBounds = (
    inspection: FrustraMpnnSourceInspection,
    entity: FrustraMpnnEntitySelector,
): { start: number; end: number } | null => {
    const key = entityKey(entity);
    const spans = (inspection.protein_sequence_spans ?? []).filter((span) => (
        entityKey(span) === key
    ));
    if (spans.length > 1) {
        throw new Error('source inspection has ambiguous sequence spans for one protein entity');
    }
    if (spans.length === 1) {
        return { start: spans[0].sequence_start, end: spans[0].sequence_end };
    }
    const indices = inspection.mapped_residues
        .filter((residue) => entityKey(residue) === key)
        .map((residue) => residue.sequence_index);
    return indices.length > 0
        ? { start: Math.min(...indices), end: Math.max(...indices) }
        : null;
};

export const getFrustraMpnnSelectionModeOptions = (
    inspection?: FrustraMpnnSourceInspection | null,
): FrustraMpnnSelectionModeOption[] => [
    { mode: 'all_protein_entities', available: true },
    {
        mode: 'selected_entities',
        available: Boolean(inspection && inspection.protein_entities.length > 0),
        ...(!inspection || inspection.protein_entities.length === 0
            ? { reason: 'Exact source entity identity is unavailable until source inspection is produced.' }
            : {}),
    },
    {
        mode: 'selected_regions',
        available: Boolean(inspection && inspection.protein_entities.some((entity) => (
            getFrustraMpnnInspectionRegionBounds(inspection, entity) !== null
        ))),
        ...(!inspection || !inspection.protein_entities.some((entity) => (
            getFrustraMpnnInspectionRegionBounds(inspection, entity) !== null
        ))
            ? { reason: 'Exact source sequence identity is unavailable until source inspection is produced.' }
            : {}),
    },
    {
        mode: 'selected_residues',
        available: Boolean(inspection && inspection.mapped_residues.length > 0),
        ...(!inspection || inspection.mapped_residues.length === 0
            ? { reason: 'Exact source residue identity is unavailable until source inspection is produced.' }
            : {}),
    },
];

export const selectFrustraMpnnProteinSelectionMode = (
    settings: FrustraMpnnRequestedSettings,
    mode: FrustraMpnnProteinSelectionMode,
    inspection?: FrustraMpnnSourceInspection | null,
): FrustraMpnnRequestedSettings => {
    let proteinSelection: FrustraMpnnProteinSelection;
    if (mode === 'all_protein_entities') {
        proteinSelection = { mode, entities: [], regions: [], residues: [] };
    } else if (mode === 'selected_entities') {
        const first = inspection?.protein_entities[0];
        if (!first) throw new Error('selected entity mode is unavailable without exact source inspection');
        const entity: FrustraMpnnEntitySelector = {
            entity_instance_id: first.entity_instance_id,
            source_entity_id: first.source_entity_id,
            label_asym_id: first.label_asym_id,
            auth_asym_id: first.auth_asym_id,
        };
        proteinSelection = { mode, entities: [entity], regions: [], residues: [] };
    } else if (mode === 'selected_regions') {
        const first = inspection?.protein_entities.find((entity) => (
            getFrustraMpnnInspectionRegionBounds(inspection, entity) !== null
        ));
        if (!first || !inspection) {
            throw new Error('selected region mode is unavailable without exact source inspection');
        }
        const bounds = getFrustraMpnnInspectionRegionBounds(inspection, first);
        if (!bounds) {
            throw new Error('selected region mode is unavailable without exact source inspection');
        }
        const entity: FrustraMpnnEntitySelector = {
            entity_instance_id: first.entity_instance_id,
            source_entity_id: first.source_entity_id,
            label_asym_id: first.label_asym_id,
            auth_asym_id: first.auth_asym_id,
        };
        proteinSelection = {
            mode,
            entities: [],
            regions: [{
                ...entity,
                sequence_start: bounds.start,
                sequence_end: bounds.end,
            }],
            residues: [],
        };
    } else {
        const first = inspection?.mapped_residues[0];
        if (!first) throw new Error('selected residue mode is unavailable without exact source inspection');
        const residue: FrustraMpnnResidueSelector = {
            entity_instance_id: first.entity_instance_id,
            source_entity_id: first.source_entity_id,
            label_asym_id: first.label_asym_id,
            auth_asym_id: first.auth_asym_id,
            auth_seq_id: first.auth_seq_id,
            insertion_code: first.insertion_code,
            sequence_index: first.sequence_index,
        };
        proteinSelection = { mode, entities: [], regions: [], residues: [residue] };
    }
    return parseFrustraMpnnRequestedSettings({ ...settings, protein_selection: proteinSelection });
};

export const setFrustraMpnnEntitySelected = (
    settings: FrustraMpnnRequestedSettings,
    entity: FrustraMpnnInspectableEntity,
    selected: boolean,
): FrustraMpnnRequestedSettings => {
    if (settings.protein_selection.mode !== 'selected_entities') {
        throw new Error('entity selection requires selected_entities mode');
    }
    const selector: FrustraMpnnEntitySelector = {
        entity_instance_id: entity.entity_instance_id,
        source_entity_id: entity.source_entity_id,
        label_asym_id: entity.label_asym_id,
        auth_asym_id: entity.auth_asym_id,
    };
    const key = entityKey(selector);
    const current = settings.protein_selection.entities;
    const entities = selected
        ? (current.some((item) => entityKey(item) === key) ? current : [...current, selector])
        : current.filter((item) => entityKey(item) !== key);
    if (entities.length === 0) throw new Error('selected_entities requires at least one entity');
    return parseFrustraMpnnRequestedSettings({
        ...settings,
        protein_selection: { mode: 'selected_entities', entities, regions: [], residues: [] },
    });
};

export const setFrustraMpnnRegion = (
    settings: FrustraMpnnRequestedSettings,
    index: number,
    region: FrustraMpnnRegionSelector,
): FrustraMpnnRequestedSettings => {
    if (settings.protein_selection.mode !== 'selected_regions') {
        throw new Error('region editing requires selected_regions mode');
    }
    if (!Number.isInteger(index) || index < 0 || index >= settings.protein_selection.regions.length) {
        throw new Error('selected region index is invalid');
    }
    const regions = settings.protein_selection.regions.map((current, currentIndex) => (
        currentIndex === index ? region : current
    ));
    return parseFrustraMpnnRequestedSettings({
        ...settings,
        protein_selection: { mode: 'selected_regions', entities: [], regions, residues: [] },
    });
};

export const addFrustraMpnnRegion = (
    settings: FrustraMpnnRequestedSettings,
    inspection: FrustraMpnnSourceInspection,
): FrustraMpnnRequestedSettings => {
    if (settings.protein_selection.mode !== 'selected_regions') {
        throw new Error('region editing requires selected_regions mode');
    }
    for (const inspectedEntity of inspection.protein_entities) {
        const entity: FrustraMpnnEntitySelector = {
            entity_instance_id: inspectedEntity.entity_instance_id,
            source_entity_id: inspectedEntity.source_entity_id,
            label_asym_id: inspectedEntity.label_asym_id,
            auth_asym_id: inspectedEntity.auth_asym_id,
        };
        const bounds = getFrustraMpnnInspectionRegionBounds(inspection, entity);
        if (!bounds) continue;
        const existing = settings.protein_selection.regions
            .filter((region) => entityKey(region) === entityKey(entity))
            .sort((left, right) => left.sequence_start - right.sequence_start);
        let uncovered = bounds.start;
        for (const region of existing) {
            if (region.sequence_end < uncovered) continue;
            if (region.sequence_start > uncovered) break;
            uncovered = region.sequence_end + 1;
        }
        if (uncovered <= bounds.end) {
            return parseFrustraMpnnRequestedSettings({
                ...settings,
                protein_selection: {
                    mode: 'selected_regions',
                    entities: [],
                    regions: [
                        ...settings.protein_selection.regions,
                        { ...entity, sequence_start: uncovered, sequence_end: uncovered },
                    ],
                    residues: [],
                },
            });
        }
    }
    throw new Error('every inspected protein sequence position is already covered');
};

export const removeFrustraMpnnRegion = (
    settings: FrustraMpnnRequestedSettings,
    index: number,
): FrustraMpnnRequestedSettings => {
    if (settings.protein_selection.mode !== 'selected_regions') {
        throw new Error('region editing requires selected_regions mode');
    }
    if (settings.protein_selection.regions.length === 1) {
        throw new Error('selected_regions requires at least one region');
    }
    const regions = settings.protein_selection.regions.filter((_region, currentIndex) => currentIndex !== index);
    return parseFrustraMpnnRequestedSettings({
        ...settings,
        protein_selection: { mode: 'selected_regions', entities: [], regions, residues: [] },
    });
};

export const setFrustraMpnnResidueSelected = (
    settings: FrustraMpnnRequestedSettings,
    residue: FrustraMpnnInspectableResidue,
    selected: boolean,
): FrustraMpnnRequestedSettings => {
    if (settings.protein_selection.mode !== 'selected_residues') {
        throw new Error('residue selection requires selected_residues mode');
    }
    const selector: FrustraMpnnResidueSelector = {
        entity_instance_id: residue.entity_instance_id,
        source_entity_id: residue.source_entity_id,
        label_asym_id: residue.label_asym_id,
        auth_asym_id: residue.auth_asym_id,
        auth_seq_id: residue.auth_seq_id,
        insertion_code: residue.insertion_code,
        sequence_index: residue.sequence_index,
    };
    const key = residueKey(selector);
    const current = settings.protein_selection.residues;
    const residues = selected
        ? (current.some((item) => residueKey(item) === key) ? current : [...current, selector])
        : current.filter((item) => residueKey(item) !== key);
    if (residues.length === 0) throw new Error('selected_residues requires at least one residue');
    return parseFrustraMpnnRequestedSettings({
        ...settings,
        protein_selection: { mode: 'selected_residues', entities: [], regions: [], residues },
    });
};

export const updateFrustraMpnnSourceStructure = (
    settings: FrustraMpnnRequestedSettings,
    sourceStructure: FrustraMpnnSourceStructureSettings,
): FrustraMpnnRequestedSettings => parseFrustraMpnnRequestedSettings({
    ...settings,
    source_structure: sourceStructure,
});

export const updateFrustraMpnnClassificationPolicy = (
    settings: FrustraMpnnRequestedSettings,
    classificationPolicy: FrustraMpnnClassificationPolicy,
): FrustraMpnnRequestedSettings => parseFrustraMpnnRequestedSettings({
    ...settings,
    classification_policy: classificationPolicy,
});

export const frustraMpnnEntitySelectorKey = entityKey;
export const frustraMpnnRegionSelectorKey = regionKey;
export const frustraMpnnResidueSelectorKey = residueKey;
