import { api } from './api.js';
import type { CmLandscapePage, CmLandscapeRow } from '../components/conformationalMapping/conformationalMappingApi.js';
import type { FrustraMpnnStructureMap } from '../components/conformationalMapping/frustraMpnnViewerMetrics.js';
import { parseFrustraMpnnMultidimensionalPage, type FrustraMpnnResultPage } from '../components/frustraMpnnMultidimensionalModel.js';
import type { StructureCameraState, StructureLayerState, StructureRepresentationState } from '../structureViewer/contracts/scenePresentation.js';
import {
    parseFrustraMpnnRequestedSettings,
    type FrustraMpnnInspectableEntity,
    type FrustraMpnnInspectableResidue,
    type FrustraMpnnRegionSelector,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnSourceInspection,
} from '../components/frustrampnn/frustraMpnnSettingsState.js';

export type {
    FrustraMpnnInspectableEntity,
    FrustraMpnnInspectableResidue,
    FrustraMpnnRequestedSettings,
    FrustraMpnnSourceInspection,
} from '../components/frustrampnn/frustraMpnnSettingsState.js';

export type FrustraMpnnTerminalStatus = 'succeeded' | 'failed' | 'not_run';
export type FrustraMpnnJobStatus = 'queued' | 'running' | 'awaiting_input' | 'completed' | 'failed' | 'cancelled';
export type FrustraMpnnClass = 'high' | 'neutral' | 'minimal';
export type FrustraMpnnSlotStatus = 'ok' | 'missing';

export interface FrustraMpnnWorkflowIntegration {
    default_enabled: boolean;
    enabled_summary: string;
}

export interface FrustraMpnnIntegrationConfig {
    model_id: 'frustrampnn';
    model_name: string;
    model_version: string;
    stage_parameter: string;
    operator_label: string;
    checkpoint_label: string | null;
    model_summary: string;
    semantic_roles: string[];
    workflows: Record<string, FrustraMpnnWorkflowIntegration>;
    capability_inventory: Record<string, unknown>;
    capability_inventory_byte_sha256: string;
    capability_inventory_content_sha256: string;
    settings_schema: Record<string, unknown>;
    canonical_defaults: FrustraMpnnRequestedSettings;
    parameter_descriptors: Array<Record<string, unknown>>;
    field_ownership: Record<string, unknown>;
    control_kind_hints: Record<string, unknown>;
    compatibility_rules: Array<Record<string, unknown>>;
}

export interface FrustraMpnnSettingsValidationHashes {
    settings_sha256: string;
    effective_settings_sha256: string;
    configuration_sha256: string;
    capability_inventory_byte_sha256: string;
    structure_map_sha256: string;
}

export interface FrustraMpnnSettingsValidationPreview {
    validation_scope: 'preview_only';
    queue_resolution_requirement: 'submission_must_re_resolve_governed_source';
    normalized_requested_settings: FrustraMpnnRequestedSettings;
    effective_settings: FrustraMpnnEffectiveSettingsProjection;
    hashes: FrustraMpnnSettingsValidationHashes;
}

const FRUSTRAMPNN_INTEGRATION_KEYS = [
    'model_id',
    'model_name',
    'model_version',
    'stage_parameter',
    'operator_label',
    'checkpoint_label',
    'model_summary',
    'semantic_roles',
    'workflows',
    'capability_inventory',
    'capability_inventory_byte_sha256',
    'capability_inventory_content_sha256',
    'settings_schema',
    'canonical_defaults',
    'parameter_descriptors',
    'field_ownership',
    'control_kind_hints',
    'compatibility_rules',
] as const;
const FRUSTRAMPNN_INSPECTION_KEYS = [
    'source_models',
    'selected_source_model',
    'observed_altlocs',
    'selected_altloc',
    'protein_entities',
    'mapped_residues',
] as const;
const FRUSTRAMPNN_INSPECTION_WITH_SEQUENCE_SPANS_KEYS = [
    ...FRUSTRAMPNN_INSPECTION_KEYS,
    'protein_sequence_spans',
] as const;
const FRUSTRAMPNN_INSPECTION_ENTITY_KEYS = [
    'entity_instance_id',
    'source_entity_id',
    'label_asym_id',
    'auth_asym_id',
    'pdb_chain_id',
] as const;
const FRUSTRAMPNN_INSPECTION_RESIDUE_KEYS = [
    'entity_instance_id',
    'source_entity_id',
    'label_asym_id',
    'auth_asym_id',
    'auth_seq_id',
    'insertion_code',
    'sequence_index',
    'wt',
] as const;
const FRUSTRAMPNN_INSPECTION_SPAN_KEYS = [
    'entity_instance_id',
    'source_entity_id',
    'label_asym_id',
    'auth_asym_id',
    'sequence_start',
    'sequence_end',
] as const;
const FRUSTRAMPNN_VALIDATION_KEYS = [
    'validation_scope',
    'queue_resolution_requirement',
    'normalized_requested_settings',
    'effective_settings',
    'execution_configuration',
    'hashes',
] as const;
const FRUSTRAMPNN_VALIDATION_HASH_KEYS = [
    'settings_sha256',
    'effective_settings_sha256',
    'configuration_sha256',
    'capability_inventory_byte_sha256',
    'structure_map_sha256',
] as const;
const FRUSTRAMPNN_FORBIDDEN_PUBLIC_KEY_PARTS = [
    'path',
    'runtime',
    'scheduler',
    'storage',
    'command',
    'container',
    'executable',
    'device',
    'gpu',
] as const;

const fmRecord = (value: unknown, label: string): Record<string, unknown> => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        throw new Error(`${label} must be an object`);
    }
    return value as Record<string, unknown>;
};

const fmExactKeys = (
    record: Record<string, unknown>,
    keys: readonly string[],
    label: string,
): void => {
    const expected = [...keys].sort();
    const actual = Object.keys(record).sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
        throw new Error(`${label} has unknown or missing keys`);
    }
};

const fmString = (value: unknown, label: string, allowBlank = false): string => {
    if (typeof value !== 'string' || (!allowBlank && value.length === 0)) {
        throw new Error(`${label} must be ${allowBlank ? 'a string' : 'a non-empty string'}`);
    }
    return value;
};

const fmInteger = (value: unknown, label: string, minimum?: number): number => {
    if (typeof value !== 'number' || !Number.isInteger(value) || (minimum !== undefined && value < minimum)) {
        throw new Error(`${label} must be an integer${minimum === undefined ? '' : ` >= ${minimum}`}`);
    }
    return value;
};

const fmAltloc = (value: unknown, label: string): string => {
    const altloc = fmString(value, label, true);
    if (!/^(?:|[A-Za-z0-9])$/.test(altloc)) throw new Error(`${label} is not a valid altloc`);
    return altloc;
};

const fmSha256 = (value: unknown, label: string): string => {
    const sha256 = fmString(value, label);
    if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error(`${label} must be a lowercase SHA-256`);
    return sha256;
};

const fmStringArray = (value: unknown, label: string): string[] => {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
    return value.map((item, index) => fmString(item, `${label}[${index}]`));
};

const assertNoUnsafePublicFields = (value: unknown, label: string): void => {
    if (Array.isArray(value)) {
        value.forEach((item, index) => assertNoUnsafePublicFields(item, `${label}[${index}]`));
        return;
    }
    if (typeof value !== 'object' || value === null) return;
    for (const [key, child] of Object.entries(value)) {
        const normalizedKey = key.toLowerCase();
        if (FRUSTRAMPNN_FORBIDDEN_PUBLIC_KEY_PARTS.some((part) => normalizedKey.includes(part))) {
            throw new Error(`${label} contains forbidden field ${key}`);
        }
        assertNoUnsafePublicFields(child, `${label}.${key}`);
    }
};

const fmMetadataObject = (value: unknown, label: string): Record<string, unknown> => {
    const record = fmRecord(value, label);
    assertNoUnsafePublicFields(record, label);
    return { ...record };
};

const fmMetadataArray = (value: unknown, label: string): Array<Record<string, unknown>> => {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
    return value.map((item, index) => fmMetadataObject(item, `${label}[${index}]`));
};

export const parseFrustraMpnnIntegration = (value: unknown): FrustraMpnnIntegrationConfig => {
    const payload = fmRecord(value, 'FrustraMPNN integration response');
    fmExactKeys(payload, FRUSTRAMPNN_INTEGRATION_KEYS, 'FrustraMPNN integration response');
    assertNoUnsafePublicFields(payload, 'FrustraMPNN integration response');
    if (payload.model_id !== 'frustrampnn') throw new Error('FrustraMPNN integration model identity is invalid');

    const semanticRoles = fmStringArray(payload.semantic_roles, 'semantic_roles');
    const workflowPayload = fmRecord(payload.workflows, 'workflows');
    const workflows: Record<string, FrustraMpnnWorkflowIntegration> = {};
    for (const [workflowId, workflowValue] of Object.entries(workflowPayload)) {
        fmString(workflowId, 'workflow id');
        const workflow = fmRecord(workflowValue, `workflows.${workflowId}`);
        fmExactKeys(workflow, ['default_enabled', 'enabled_summary'], `workflows.${workflowId}`);
        if (typeof workflow.default_enabled !== 'boolean') {
            throw new Error(`workflows.${workflowId}.default_enabled must be boolean`);
        }
        workflows[workflowId] = {
            default_enabled: workflow.default_enabled,
            enabled_summary: fmString(workflow.enabled_summary, `workflows.${workflowId}.enabled_summary`),
        };
    }

    return {
        model_id: 'frustrampnn',
        model_name: fmString(payload.model_name, 'model_name'),
        model_version: fmString(payload.model_version, 'model_version'),
        stage_parameter: fmString(payload.stage_parameter, 'stage_parameter'),
        operator_label: fmString(payload.operator_label, 'operator_label'),
        checkpoint_label: payload.checkpoint_label === null
            ? null
            : fmString(payload.checkpoint_label, 'checkpoint_label'),
        model_summary: fmString(payload.model_summary, 'model_summary'),
        semantic_roles: semanticRoles,
        workflows,
        capability_inventory: fmMetadataObject(payload.capability_inventory, 'capability_inventory'),
        capability_inventory_byte_sha256: fmSha256(
            payload.capability_inventory_byte_sha256,
            'capability_inventory_byte_sha256',
        ),
        capability_inventory_content_sha256: fmSha256(
            payload.capability_inventory_content_sha256,
            'capability_inventory_content_sha256',
        ),
        settings_schema: fmMetadataObject(payload.settings_schema, 'settings_schema'),
        canonical_defaults: parseFrustraMpnnRequestedSettings(payload.canonical_defaults),
        parameter_descriptors: fmMetadataArray(payload.parameter_descriptors, 'parameter_descriptors'),
        field_ownership: fmMetadataObject(payload.field_ownership, 'field_ownership'),
        control_kind_hints: fmMetadataObject(payload.control_kind_hints, 'control_kind_hints'),
        compatibility_rules: fmMetadataArray(payload.compatibility_rules, 'compatibility_rules'),
    };
};

const parseInspectableEntity = (value: unknown, label: string): FrustraMpnnInspectableEntity => {
    const record = fmRecord(value, label);
    fmExactKeys(record, FRUSTRAMPNN_INSPECTION_ENTITY_KEYS, label);
    const parsed = parseFrustraMpnnRequestedSettings({
        schema_name: 'frustrampnn_settings',
        schema_version: 1,
        protein_selection: {
            mode: 'selected_entities',
            entities: [{
                entity_instance_id: record.entity_instance_id,
                source_entity_id: record.source_entity_id,
                label_asym_id: record.label_asym_id,
                auth_asym_id: record.auth_asym_id,
            }],
            regions: [],
            residues: [],
        },
        source_structure: { selected_model_number: 1, preferred_altloc: '' },
        classification_policy: { mode: 'canonical', high_max: -1, minimal_min: 0.58 },
    });
    if (parsed.protein_selection.mode !== 'selected_entities') throw new Error(`${label} is invalid`);
    const pdbChainId = fmNullableString(record.pdb_chain_id, `${label}.pdb_chain_id`);
    return { ...parsed.protein_selection.entities[0], pdb_chain_id: pdbChainId };
};

const parseInspectableResidue = (value: unknown, label: string): FrustraMpnnInspectableResidue => {
    const record = fmRecord(value, label);
    fmExactKeys(record, FRUSTRAMPNN_INSPECTION_RESIDUE_KEYS, label);
    const parsed = parseFrustraMpnnRequestedSettings({
        schema_name: 'frustrampnn_settings',
        schema_version: 1,
        protein_selection: {
            mode: 'selected_residues',
            entities: [],
            regions: [],
            residues: [{
                entity_instance_id: record.entity_instance_id,
                source_entity_id: record.source_entity_id,
                label_asym_id: record.label_asym_id,
                auth_asym_id: record.auth_asym_id,
                auth_seq_id: record.auth_seq_id,
                insertion_code: record.insertion_code,
                sequence_index: record.sequence_index,
            }],
        },
        source_structure: { selected_model_number: 1, preferred_altloc: '' },
        classification_policy: { mode: 'canonical', high_max: -1, minimal_min: 0.58 },
    });
    if (parsed.protein_selection.mode !== 'selected_residues') throw new Error(`${label} is invalid`);
    const wt = fmString(record.wt, `${label}.wt`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt)) throw new Error(`${label}.wt is invalid`);
    return { ...parsed.protein_selection.residues[0], wt };
};

const parseInspectableSequenceSpan = (
    value: unknown,
    label: string,
): FrustraMpnnRegionSelector => {
    const record = fmRecord(value, label);
    fmExactKeys(record, FRUSTRAMPNN_INSPECTION_SPAN_KEYS, label);
    const parsed = parseFrustraMpnnRequestedSettings({
        schema_name: 'frustrampnn_settings',
        schema_version: 1,
        protein_selection: {
            mode: 'selected_regions',
            entities: [],
            regions: [record],
            residues: [],
        },
        source_structure: { selected_model_number: 1, preferred_altloc: '' },
        classification_policy: { mode: 'canonical', high_max: -1, minimal_min: 0.58 },
    });
    if (parsed.protein_selection.mode !== 'selected_regions') throw new Error(`${label} is invalid`);
    return parsed.protein_selection.regions[0];
};

export const parseFrustraMpnnSourceInspection = (value: unknown): FrustraMpnnSourceInspection => {
    const payload = fmRecord(value, 'FrustraMPNN source inspection');
    const hasSequenceSpans = Object.prototype.hasOwnProperty.call(payload, 'protein_sequence_spans');
    fmExactKeys(
        payload,
        hasSequenceSpans
            ? FRUSTRAMPNN_INSPECTION_WITH_SEQUENCE_SPANS_KEYS
            : FRUSTRAMPNN_INSPECTION_KEYS,
        'FrustraMPNN source inspection',
    );
    const sourceModels = Array.isArray(payload.source_models)
        ? payload.source_models.map((item, index) => fmInteger(item, `source_models[${index}]`, 1))
        : (() => { throw new Error('source_models must be an array'); })();
    if (sourceModels.length === 0 || new Set(sourceModels).size !== sourceModels.length) {
        throw new Error('source_models must contain unique available model numbers');
    }
    const selectedSourceModel = fmInteger(payload.selected_source_model, 'selected_source_model', 1);
    if (!sourceModels.includes(selectedSourceModel)) {
        throw new Error('selected_source_model must be one of source_models');
    }
    if (!Array.isArray(payload.observed_altlocs)) throw new Error('observed_altlocs must be an array');
    const observedAltlocs = payload.observed_altlocs.map((item, index) => fmAltloc(item, `observed_altlocs[${index}]`));
    if (new Set(observedAltlocs).size !== observedAltlocs.length) {
        throw new Error('observed_altlocs must contain unique choices');
    }
    const selectedAltloc = fmAltloc(payload.selected_altloc, 'selected_altloc');
    if (!observedAltlocs.includes(selectedAltloc)) {
        throw new Error('selected_altloc must be one of observed_altlocs');
    }
    if (!Array.isArray(payload.protein_entities)
        || (hasSequenceSpans && !Array.isArray(payload.protein_sequence_spans))
        || !Array.isArray(payload.mapped_residues)) {
        throw new Error('source inspection selectors must be arrays');
    }
    const proteinEntities = payload.protein_entities.map((item, index) => (
        parseInspectableEntity(item, `protein_entities[${index}]`)
    ));
    const mappedResidues = payload.mapped_residues.map((item, index) => (
        parseInspectableResidue(item, `mapped_residues[${index}]`)
    ));
    const proteinSequenceSpans = hasSequenceSpans
        ? (payload.protein_sequence_spans as unknown[]).map((item, index) => (
            parseInspectableSequenceSpan(item, `protein_sequence_spans[${index}]`)
        ))
        : [];
    return {
        source_models: sourceModels,
        selected_source_model: selectedSourceModel,
        observed_altlocs: observedAltlocs,
        selected_altloc: selectedAltloc,
        protein_entities: proteinEntities,
        protein_sequence_spans: proteinSequenceSpans,
        mapped_residues: mappedResidues,
    };
};

export const parseFrustraMpnnSettingsValidationPreview = (
    value: unknown,
): FrustraMpnnSettingsValidationPreview => {
    const payload = fmRecord(value, 'FrustraMPNN settings validation response');
    fmExactKeys(payload, FRUSTRAMPNN_VALIDATION_KEYS, 'FrustraMPNN settings validation response');
    if (payload.validation_scope !== 'preview_only') throw new Error('validation_scope is invalid');
    if (payload.queue_resolution_requirement !== 'submission_must_re_resolve_governed_source') {
        throw new Error('queue_resolution_requirement is invalid');
    }
    const effective = parseFrustraMpnnEffectiveSettingsProjection(payload.effective_settings);
    const configurationKeys = [
        'configuration_id', 'schema_name', 'schema_version', 'tool_id', 'tool_version',
        'effective_settings', 'settings_value_origin', 'requested_settings_sha256', 'effective_settings_sha256',
        'capability_inventory_byte_sha256', 'classification_policy_sha256', 'runtime',
        'runtime_identity_sha256', 'normalization_policy_id', 'normalization_policy_version',
        'threshold_policy_id', 'source_artifact_sha256', 'structure_map_sha256',
        'normalized_pdb_sha256', 'configuration_sha256',
    ] as const;
    const configuration = fmClosedProjection(
        payload.execution_configuration,
        'execution_configuration',
        configurationKeys,
        configurationKeys,
    );
    if (configuration.configuration_id !== 'frustrampnn_execution_configuration_v2'
        || configuration.schema_name !== 'frustrampnn_execution_configuration'
        || configuration.schema_version !== 2
        || configuration.tool_id !== 'frustrampnn'
        || configuration.normalization_policy_id !== 'frustrampnn_structure_normalizer'
        || configuration.normalization_policy_version !== 1
        || configuration.threshold_policy_id !== 'frustrampnn_class_v1') {
        throw new Error('execution_configuration identity is invalid');
    }
    fmValueOrigin(configuration.settings_value_origin, 'execution_configuration.settings_value_origin');
    fmSha256(configuration.requested_settings_sha256, 'execution_configuration.requested_settings_sha256');
    fmSha256(configuration.effective_settings_sha256, 'execution_configuration.effective_settings_sha256');
    fmSha256(configuration.capability_inventory_byte_sha256, 'execution_configuration.capability_inventory_byte_sha256');
    fmSha256(configuration.classification_policy_sha256, 'execution_configuration.classification_policy_sha256');
    fmSha256(configuration.runtime_identity_sha256, 'execution_configuration.runtime_identity_sha256');
    fmSha256(configuration.source_artifact_sha256, 'execution_configuration.source_artifact_sha256');
    fmSha256(configuration.structure_map_sha256, 'execution_configuration.structure_map_sha256');
    fmSha256(configuration.normalized_pdb_sha256, 'execution_configuration.normalized_pdb_sha256');
    fmSha256(configuration.configuration_sha256, 'execution_configuration.configuration_sha256');
    parseFrustraMpnnEffectiveSettingsProjection(configuration.effective_settings);
    const runtime = fmClosedProjection(
        configuration.runtime,
        'execution_configuration.runtime',
        [
            'sif_name', 'sif_sha256', 'executable_sha256', 'checkpoint_id', 'checkpoint_sha256',
            'package_version', 'source_commit', 'python_version', 'pytorch_version', 'image_version',
        ],
        [
            'sif_name', 'sif_sha256', 'executable_sha256', 'checkpoint_id', 'checkpoint_sha256',
            'package_version', 'source_commit', 'python_version', 'pytorch_version', 'image_version',
        ],
    );
    fmString(runtime.sif_name, 'execution_configuration.runtime.sif_name');
    fmSha256(runtime.sif_sha256, 'execution_configuration.runtime.sif_sha256');
    fmSha256(runtime.executable_sha256, 'execution_configuration.runtime.executable_sha256');
    fmString(runtime.checkpoint_id, 'execution_configuration.runtime.checkpoint_id');
    fmSha256(runtime.checkpoint_sha256, 'execution_configuration.runtime.checkpoint_sha256');
    for (const key of ['package_version', 'source_commit', 'python_version', 'pytorch_version', 'image_version'] as const) {
        fmString(runtime[key], `execution_configuration.runtime.${key}`);
    }
    const hashes = fmRecord(payload.hashes, 'hashes');
    fmExactKeys(hashes, FRUSTRAMPNN_VALIDATION_HASH_KEYS, 'hashes');
    return {
        validation_scope: 'preview_only',
        queue_resolution_requirement: 'submission_must_re_resolve_governed_source',
        normalized_requested_settings: parsePersistedRequestedSettingsProjection(
            payload.normalized_requested_settings,
            'normalized_requested_settings',
        ),
        effective_settings: effective,
        hashes: {
            settings_sha256: fmSha256(hashes.settings_sha256, 'hashes.settings_sha256'),
            effective_settings_sha256: fmSha256(hashes.effective_settings_sha256, 'hashes.effective_settings_sha256'),
            configuration_sha256: fmSha256(hashes.configuration_sha256, 'hashes.configuration_sha256'),
            capability_inventory_byte_sha256: fmSha256(
                hashes.capability_inventory_byte_sha256,
                'hashes.capability_inventory_byte_sha256',
            ),
            structure_map_sha256: fmSha256(hashes.structure_map_sha256, 'hashes.structure_map_sha256'),
        },
    };
};

export interface FrustraMpnnOwnedSourceReference {
    job_id: string;
    invocation_id: string;
}

export interface FrustraMpnnSourceStructureSelection {
    selected_model_number: number;
    preferred_altloc: string;
}

const normalizeOwnedSourceReference = (
    source: FrustraMpnnOwnedSourceReference,
): FrustraMpnnOwnedSourceReference => ({
    job_id: fmString(source.job_id, 'owned source job_id'),
    invocation_id: fmString(source.invocation_id, 'owned source invocation_id'),
});

const normalizeSourceStructureSelection = (
    sourceStructure: FrustraMpnnSourceStructureSelection,
): FrustraMpnnSourceStructureSelection => ({
    selected_model_number: fmInteger(sourceStructure.selected_model_number, 'selected_model_number', 1),
    preferred_altloc: fmAltloc(sourceStructure.preferred_altloc, 'preferred_altloc'),
});

export const fetchFrustraMpnnIntegration = async (
    signal?: AbortSignal,
): Promise<FrustraMpnnIntegrationConfig> => {
    const response = await api.get<unknown>('/api/models/frustrampnn/integration', { signal });
    return parseFrustraMpnnIntegration(response.data);
};

export const inspectFrustraMpnnOwnedSource = async (
    source: FrustraMpnnOwnedSourceReference,
    sourceStructure: FrustraMpnnSourceStructureSelection,
    signal?: AbortSignal,
): Promise<FrustraMpnnSourceInspection> => {
    const response = await api.post<unknown>(
        '/api/frustrampnn/sources/inspect/owned',
        { ...normalizeOwnedSourceReference(source), ...normalizeSourceStructureSelection(sourceStructure) },
        { signal },
    );
    return parseFrustraMpnnSourceInspection(response.data);
};

export const inspectFrustraMpnnUploadedSource = async (
    structureFile: File,
    sourceStructure: FrustraMpnnSourceStructureSelection,
    signal?: AbortSignal,
): Promise<FrustraMpnnSourceInspection> => {
    const normalized = normalizeSourceStructureSelection(sourceStructure);
    const form = new FormData();
    form.append('structure_file', structureFile);
    form.append('selected_model_number', String(normalized.selected_model_number));
    form.append('preferred_altloc', normalized.preferred_altloc);
    const response = await api.post<unknown>('/api/frustrampnn/sources/inspect/upload', form, { signal });
    return parseFrustraMpnnSourceInspection(response.data);
};

export const validateFrustraMpnnOwnedSettings = async (
    settings: FrustraMpnnRequestedSettings,
    source: FrustraMpnnOwnedSourceReference,
    signal?: AbortSignal,
): Promise<FrustraMpnnSettingsValidationPreview> => {
    const response = await api.post<unknown>(
        '/api/frustrampnn/settings/validate/owned',
        { ...normalizeOwnedSourceReference(source), settings: parseFrustraMpnnRequestedSettings(settings) },
        { signal },
    );
    return parseFrustraMpnnSettingsValidationPreview(response.data);
};

export const validateFrustraMpnnUploadedSettings = async (
    settings: FrustraMpnnRequestedSettings,
    structureFile: File,
    signal?: AbortSignal,
): Promise<FrustraMpnnSettingsValidationPreview> => {
    const form = new FormData();
    form.append('structure_file', structureFile);
    form.append('settings', JSON.stringify(parseFrustraMpnnRequestedSettings(settings)));
    const response = await api.post<unknown>('/api/frustrampnn/settings/validate/upload', form, { signal });
    return parseFrustraMpnnSettingsValidationPreview(response.data);
};

export interface FrustraMpnnDesignSelection {
    design_id: string;
    source_sha256: string;
}

export interface FrustraMpnnAnalyzeRequest {
    selections: FrustraMpnnDesignSelection[];
    frustrampnn_settings: FrustraMpnnRequestedSettings;
}

export interface FrustraMpnnGpuProvenance {
    physical_device_id: string;
    task_visible_device_index: number | null;
}

export interface FrustraMpnnReceiptProducer {
    producer_stage: string | null;
    producer_id: string | null;
    source_stage_family: string | null;
    source_stage_mode: string | null;
    artifact_class: string | null;
    parent_job_id: string | null;
    parent_invocation_id: string | null;
    parent_landscape_sha256: string | null;
    guidance_id: string | null;
    protein_sequence_sha256: string | null;
}

export interface FrustraMpnnChildCandidateReceipt {
    selection_ordinal: number | null;
    design_id: string | null;
    source_job_id: string | null;
    candidate_id: string | null;
    invocation_id: string | null;
    source_artifact_id: string | null;
    source_artifact_sha256: string | null;
    component_request_sha256: string | null;
    normalized_pdb_sha256: string | null;
    structure_map_sha256: string | null;
    settings_value_origin: 'bms_default' | 'operator_request' | null;
    requested_settings_sha256: string | null;
    effective_settings: FrustraMpnnEffectiveSettingsProjection | null;
    effective_settings_sha256: string | null;
    capability_inventory_byte_sha256: string | null;
    classification_policy_sha256: string | null;
    execution_configuration_sha256: string | null;
    runtime_identity_sha256: string | null;
    producer: FrustraMpnnReceiptProducer | null;
}

export interface FrustraMpnnReceiptResult {
    candidate_id: string;
    design_id: string | null;
    source_artifact_id: string | null;
    source_artifact_sha256: string;
    invocation_id: string;
    request_sha256: string;
    status: FrustraMpnnTerminalStatus | null;
    manifest_sha256: string;
    gpu_provenance: FrustraMpnnGpuProvenance | null;
}

export interface FrustraMpnnHandoffMetadata {
    parent_landscape_sha256: string;
    parent_candidate_id: string;
    guidance_id: string | null;
    producer_id: string;
}

export interface FrustraMpnnChildReceipt {
    job_id: string;
    child_job_id: string;
    result_job_id: string;
    name: string;
    parent_job_id: string | null;
    source_parent_job_id: string | null;
    trigger: string;
    status: string;
    created_at: string | null;
    started_at: string | null;
    completed_at: string | null;
    settings_value_origin: 'bms_default' | 'operator_request';
    requested_settings: FrustraMpnnRequestedSettings;
    requested_settings_sha256: string;
    candidates: FrustraMpnnChildCandidateReceipt[];
    results: FrustraMpnnReceiptResult[];
    handoff?: FrustraMpnnHandoffMetadata;
}

export interface FrustraMpnnClassCounts {
    high: number;
    neutral: number;
    minimal: number;
}

export interface FrustraMpnnClassFractions {
    high: number;
    neutral: number;
    minimal: number;
}

export type FrustraMpnnFailureClass =
    | 'request_invalid'
    | 'source_missing'
    | 'source_hash_mismatch'
    | 'identity_ambiguous'
    | 'normalization_failed'
    | 'runtime_unavailable'
    | 'runtime_digest_mismatch'
    | 'checkpoint_mismatch'
    | 'gpu_admission_failed'
    | 'inference_nonzero_exit'
    | 'inference_timeout'
    | 'raw_output_missing'
    | 'raw_output_invalid'
    | 'position_mapping_failed'
    | 'wildtype_mismatch'
    | 'landscape_incomplete'
    | 'manifest_invalid'
    | 'publication_failed'
    | 'ingestion_failed';

export interface FrustraMpnnRuntimeIdentity {
    runtime_identity_sha256?: string;
    runtime_id?: string;
    sif_name?: string;
    sif_sha256?: string;
    image_sha256?: string;
    executable_sha256?: string;
    checkpoint_id?: string;
    checkpoint_sha256?: string;
    package_version?: string;
    source_commit?: string;
    python_version?: string;
    pytorch_version?: string;
    image_version?: string;
}

export interface FrustraMpnnTerminalArtifact {
    role: string | null;
    schema_name: string | null;
    schema_version: number | null;
    sha256: string;
    bytes: number;
    cardinality: {
        kind: 'rows' | 'residues' | 'slots' | 'records';
        count: number;
    } | null;
}

export interface FrustraMpnnTerminalResult {
    schema_name: string | null;
    schema_version: number | null;
    request_sha256: string | null;
    invocation_id: string | null;
    component_id: string | null;
    component_contract_version: string | null;
    candidate_id: string | null;
    parent_job_id: string | null;
    parent_workflow_id: string | null;
    status: FrustraMpnnTerminalStatus | null;
    failure_class: string | null;
    source_artifact: {
        artifact_id: string | null;
        sha256: string | null;
        media_type: string | null;
        producer_stage: string | null;
    } | null;
    runtime_identity: FrustraMpnnRuntimeIdentity | null;
    artifacts: FrustraMpnnTerminalArtifact[];
    result_payload: { schema_name: string | null; schema_version: number | null; sha256: string | null } | null;
    started_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
    gpu_provenance: FrustraMpnnGpuProvenance | null;
}

interface FrustraMpnnSummaryCommon {
    schema_name: 'frustrampnn_summary';
    target_id: string;
    parent_job_id: string;
    candidate_id: string;
    landscape_sha256: string;
    residue_support: {
        expected: number;
        mapped: number;
        scoreable: number;
        excluded: number;
        ambiguous: number;
    };
    slot_support: { expected: number; observed: number; scoreable: number };
    missingness_by_reason: Record<string, number>;
    native_slot_counts: FrustraMpnnClassCounts;
    native_slot_fractions: FrustraMpnnClassFractions;
    complete_landscape_counts: FrustraMpnnClassCounts;
    complete_landscape_fractions: FrustraMpnnClassFractions;
    support_by_entity_chain: Array<{
        entity_instance_id: string;
        auth_asym_id: string;
        expected_residues: number;
        mapped_residues: number;
        scoreable_residues: number;
        expected_slots: number;
        observed_slots: number;
        scoreable_slots: number;
    }>;
    threshold_policy: { id: 'frustrampnn_class_v1'; high_max: number; minimal_min: number } | { mode: 'canonical' | 'custom'; high_max: number; minimal_min: number };
    threshold_policy_sha256: string;
}

export interface FrustraMpnnHistoricalSummaryV1 extends FrustraMpnnSummaryCommon {
    schema_version: 1;
    configuration_id: 'frustrampnn_global_v1';
    configuration_sha256: string;
    threshold_policy: { id: 'frustrampnn_class_v1'; high_max: number; minimal_min: number };
}

export interface FrustraMpnnSummaryV2 extends FrustraMpnnSummaryCommon {
    schema_version: 2;
    execution_configuration_id: 'frustrampnn_execution_configuration_v2';
    execution_configuration_sha256: string;
    requested_settings_sha256: string;
    effective_settings_sha256: string;
    runtime_identity_sha256: string;
    source_artifact_sha256: string;
    structure_map_sha256: string;
    normalized_pdb_sha256: string;
    threshold_policy_id: 'frustrampnn_class_v1';
    threshold_policy: { mode: 'canonical' | 'custom'; high_max: number; minimal_min: number };
}

export type FrustraMpnnSummary = FrustraMpnnHistoricalSummaryV1 | FrustraMpnnSummaryV2;

export type FrustraMpnnAuthorityVersion = 'v2' | 'historical_v1';
export type FrustraMpnnMissingField =
    | 'settings_sha256'
    | 'effective_settings_sha256'
    | 'effective_settings_json'
    | 'capability_inventory_sha256'
    | 'statistics_sha256'
    | 'statistics_json'
    | 'comparison_compatibility_id';

export interface FrustraMpnnEffectiveSettingsProjection {
    schema_name: 'frustrampnn_effective_settings';
    schema_version: 1;
    requested_settings: FrustraMpnnRequestedSettings;
    settings_value_origin: 'bms_default' | 'operator_request';
    resolved_chains: Array<{
        entity: FrustraMpnnInspectableEntity;
        pdb_chain_id: string;
        residues: Array<FrustraMpnnInspectableResidue & {
            label_seq_id: number | null;
            pdb_chain_id: string;
            pdb_residue_id: number;
            pdb_insertion_code: string;
            model_position: number;
            residue_name: string;
        }>;
    }>;
    normalization_policy_id: 'frustrampnn_structure_normalizer';
    normalization_policy_version: 1;
    threshold_policy_id: 'frustrampnn_class_v1';
    threshold_policy_sha256: string;
    settings_sha256: string;
    capability_inventory_byte_sha256: string;
    resolution_identity: {
        source_artifact_sha256: string;
        structure_map_schema_name: string;
        structure_map_schema_version: number;
        structure_map_sha256: string;
        normalized_pdb_sha256: string;
    };
    value_sources: {
        protein_selection: {
            mode: 'bms_default' | 'operator_request';
            entities: 'bms_default' | 'operator_request';
            regions: 'bms_default' | 'operator_request';
            residues: 'bms_default' | 'operator_request';
        };
        source_structure: { selected_model_number: 'bms_default' | 'operator_request'; preferred_altloc: 'bms_default' | 'operator_request' };
        classification_policy: { mode: 'bms_default' | 'operator_request'; high_max: 'bms_default' | 'operator_request'; minimal_min: 'bms_default' | 'operator_request' };
    };
    effective_settings_sha256: string;
}

export interface FrustraMpnnExecutionReceiptProjection {
    schema_name: string | null;
    schema_version: number | null;
    invocation_id: string;
    execution_configuration_sha256: string | null;
    requested_settings_sha256: string | null;
    effective_settings_sha256: string | null;
    runtime_identity_sha256: string | null;
    source_artifact_sha256: string | null;
    structure_map_sha256: string | null;
    normalized_pdb_sha256: string | null;
    command_count: number | null;
    merged_raw_csv_sha256?: string;
    landscape_sha256?: string;
    summary_sha256?: string;
    gpu_provenance: FrustraMpnnGpuProvenance | null;
    started_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
}

export interface FrustraMpnnDenominator { kind: string; count: number }
export interface FrustraMpnnFractionMetric { value: number | null; denominator: FrustraMpnnDenominator; missingness_reason: string | null }

export interface FrustraMpnnDistribution {
    count: number;
    mean: number | null;
    median: number | null;
    sample_sd: number | null;
    min: number | null;
    max: number | null;
    q1: number | null;
    q3: number | null;
    iqr: number | null;
    denominators: Record<'count' | 'mean' | 'median' | 'sample_sd' | 'min' | 'max' | 'q1' | 'q3' | 'iqr', FrustraMpnnDenominator>;
    missingness_reasons: Record<'count' | 'mean' | 'median' | 'sample_sd' | 'min' | 'max' | 'q1' | 'q3' | 'iqr', string | null>;
}

export interface FrustraMpnnClassBurden {
    support_count: number;
    counts: FrustraMpnnClassCounts;
    fractions: { high: number | null; neutral: number | null; minimal: number | null };
    denominator: FrustraMpnnDenominator;
    missingness_reason: string | null;
}

export interface FrustraMpnnStatisticsSupport {
    source_residue_count: number;
    selected_residue_count: number;
    observed_residue_count: number;
    scoreable_residue_count: number;
    excluded_residue_count: number;
    missing_residue_count: number;
    mapping_missing_residue_count: number;
    selected_missing_residue_count: number;
    fully_scoreable_residue_count: number;
    partially_scoreable_residue_count: number;
    expected_slot_count: number;
    observed_slot_count: number;
    scoreable_slot_count: number;
    excluded_slot_count: number;
    mapping_missing_slot_count: number;
    missing_slot_count: number;
    residue_fractions: Record<'selected' | 'observed' | 'scoreable' | 'excluded' | 'missing' | 'selected_missing', FrustraMpnnFractionMetric>;
    slot_fractions: Record<'observed' | 'scoreable' | 'excluded' | 'missing' | 'selected_missing', FrustraMpnnFractionMetric>;
    exclusion_reasons: Array<{ authority: 'structure_map_row' | 'excluded_record' | 'landscape'; status: string; reason_code: string; reason: string; count: number }>;
    missing_reasons: Array<{ authority: 'structure_map_row' | 'excluded_record' | 'landscape'; status: string; reason_code: string; reason: string; count: number }>;
}

export interface FrustraMpnnGroupSupport {
    selected_residue_count: number;
    observed_residue_count: number;
    fully_scoreable_residue_count: number;
    expected_slot_count: number;
    observed_slot_count: number;
    scoreable_slot_count: number;
}

export interface FrustraMpnnStatisticsResidueIdentity {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    pdb_chain_id: string;
    model_position: number;
}

export interface FrustraMpnnRankedAlternative extends FrustraMpnnStatisticsResidueIdentity {
    mutation_aa: string;
    score: number;
    score_class: FrustraMpnnClass;
    native_score: number;
    delta: number;
    rank: number;
}

export interface FrustraMpnnStatistics {
    schema_name: 'frustrampnn_statistics';
    schema_version: 1;
    hash_semantics: 'sha256(rfc8785(document_without_top_level_statistics_sha256))';
    invocation_id: string;
    parent_job_id: string;
    candidate_id: string;
    target_id: string;
    landscape_sha256: string;
    source_artifact_sha256: string;
    normalized_pdb_sha256: string;
    settings_sha256: string;
    effective_settings_sha256: string;
    capability_inventory_content_sha256: string;
    capability_inventory_byte_sha256: string;
    configuration_sha256: string;
    runtime_identity_sha256: string;
    classification_policy_sha256: string;
    execution_plan_sha256: string;
    comparison_compatibility_id: string;
    statistics_sha256: string;
    structure_map: { schema_name: 'frustrampnn_structure_map'; schema_version: 1; sha256: string };
    output_contract_version: '2.0';
    canonical_amino_acid_order: 'ACDEFGHIKLMNPQRSTVWY';
    comparison_compatibility_basis: {
        schema_name: 'frustrampnn_comparison_compatibility_basis';
        schema_version: 2;
        raw_score_semantics: {
            model: { checkpoint_id: string; checkpoint_sha256: string };
            tool: { tool_id: string; tool_version: string };
            capability: { schema_name: 'frustrampnn_capability_inventory'; schema_version: 1; content_sha256: string };
            output_schema: { component_id: 'frustrampnn'; component_contract_version: '2.0'; landscape_schema_name: 'frustrampnn_landscape'; landscape_schema_version: 2; score_field: 'score' };
            canonical_amino_acid_order: 'ACDEFGHIKLMNPQRSTVWY';
            normalization: { normalizer_version: string; identity_authority: string; identity_domain: string; selected_source_model: number; altloc_policy: string; normalization_policy_id: 'frustrampnn_structure_normalizer'; normalization_policy_version: 1 };
        };
        classification_policy: { policy_id: string; policy_sha256: string; policy: { mode: 'canonical' | 'custom'; high_max: number; minimal_min: number } };
    };
    support: FrustraMpnnStatisticsSupport;
    distributions: { overall: FrustraMpnnDistribution; native: FrustraMpnnDistribution; non_native: FrustraMpnnDistribution };
    class_burden: { all: FrustraMpnnClassBurden; native: FrustraMpnnClassBurden; non_native: FrustraMpnnClassBurden };
    native_vs_alternative: {
        native_mean: number | null;
        alternative_mean: number | null;
        alternative_minus_native: FrustraMpnnDistribution;
        denominators: { native_mean: FrustraMpnnDenominator; alternative_mean: FrustraMpnnDenominator };
        missingness_reasons: { native_mean: string | null; alternative_mean: string | null };
    };
    per_chain: Array<{ entity_instance_id: string; source_entity_id: string | null; label_asym_id: string | null; auth_asym_id: string; pdb_chain_id: string; support: FrustraMpnnGroupSupport; all: FrustraMpnnDistribution; native: FrustraMpnnDistribution; non_native: FrustraMpnnDistribution }>;
    per_entity: Array<{ entity_instance_id: string; source_entity_id: string | null; label_asym_id: string | null; support: FrustraMpnnGroupSupport; all: FrustraMpnnDistribution; native: FrustraMpnnDistribution; non_native: FrustraMpnnDistribution }>;
    per_residue: Array<FrustraMpnnStatisticsResidueIdentity & { native_score: number; native_class: FrustraMpnnClass; all: FrustraMpnnDistribution; non_native: FrustraMpnnDistribution; alternative_class_burden: FrustraMpnnClassBurden }>;
    per_mutation_amino_acid: Array<{ mutation_aa: string; distribution: FrustraMpnnDistribution; class_composition: FrustraMpnnClassBurden }>;
    contiguous_native_class_regions: Array<{ entity_instance_id: string; source_entity_id: string | null; label_asym_id: string | null; auth_asym_id: string; pdb_chain_id: string; native_class: FrustraMpnnClass; start: FrustraMpnnStatisticsResidueIdentity; end: FrustraMpnnStatisticsResidueIdentity; length: number }>;
    ranked_non_native_alternatives: { support_count: number; omitted_count: number; best_to_worst: FrustraMpnnRankedAlternative[]; worst_to_best: FrustraMpnnRankedAlternative[] };
}

export interface FrustraMpnnResultListItem {
    invocation_id: string;
    parent_job_id: string;
    parent_workflow_id: string;
    candidate_id: string;
    design_id: string | null;
    requiredness: string;
    source_artifact_id: string | null;
    source_artifact_sha256: string;
    request_sha256: string;
    manifest_sha256: string;
    summary_sha256: string;
    created_at: string;
    authority_version: FrustraMpnnAuthorityVersion;
    availability: boolean;
    statistics_available: boolean;
    missing_fields: FrustraMpnnMissingField[];
    settings_sha256: string | null;
    effective_settings_sha256: string | null;
    effective_settings_json: FrustraMpnnEffectiveSettingsProjection | null;
    capability_inventory_sha256: string | null;
    statistics_sha256: string | null;
    statistics_json: FrustraMpnnStatistics | null;
    comparison_compatibility_id: string | null;
    status: FrustraMpnnTerminalStatus;
    component_contract_version: '1.0' | '2.0';
    runtime_identity: FrustraMpnnRuntimeIdentity;
    runtime_identity_sha256: string | null;
    gpu_provenance: FrustraMpnnGpuProvenance | null;
    failure_class: FrustraMpnnFailureClass | null;
    reopen_destination: { surface: 'frustrampnn-workbench'; params: { job_id: string; invocation_id: string } };
}

export interface FrustraMpnnResultDetail extends FrustraMpnnResultListItem {
    summary: FrustraMpnnSummary;
    terminal_result: FrustraMpnnTerminalResult;
    execution_receipt: FrustraMpnnExecutionReceiptProjection | null;
}

export interface FrustraMpnnResultList {
    items: FrustraMpnnResultListItem[];
    total: number;
    limit: number;
    offset: number;
}

export interface FrustraMpnnStatisticsResponse {
    result_id: string;
    parent_job_id: string;
    candidate_id: string;
    invocation_id: string;
    authority_version: FrustraMpnnAuthorityVersion;
    availability: boolean;
    missing_fields: FrustraMpnnMissingField[];
    settings_sha256: string | null;
    effective_settings_sha256: string | null;
    effective_settings_json: FrustraMpnnEffectiveSettingsProjection | null;
    capability_inventory_sha256: string | null;
    statistics_sha256: string | null;
    statistics_json: FrustraMpnnStatistics | null;
    comparison_compatibility_id: string | null;
    statistics: FrustraMpnnStatistics | null;
}

export type FrustraMpnnStatisticsQueryLevel = 'overview' | 'residue' | 'mutation_aa' | 'chain' | 'entity';

interface FrustraMpnnStatisticsQueryRowBase {
    dataset: FrustraMpnnResultReference;
    availability: boolean;
    unavailable_reason: string | null;
    distribution: FrustraMpnnDistribution | null;
    native_distribution: FrustraMpnnDistribution | null;
    non_native_distribution: FrustraMpnnDistribution | null;
    class_burden: FrustraMpnnClassBurden | null;
    native_score: number | null;
    native_class: FrustraMpnnClass | null;
}

export interface FrustraMpnnStatisticsResidueKey {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    pdb_chain_id: string;
    model_position: number;
}

export interface FrustraMpnnStatisticsMutationAAKey { mutation_aa: string }
export interface FrustraMpnnStatisticsChainKey {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string;
    pdb_chain_id: string;
}
export interface FrustraMpnnStatisticsEntityKey {
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
}

export interface FrustraMpnnStatisticsOverviewQueryRow extends FrustraMpnnStatisticsQueryRowBase {
    level: 'overview';
    key: Record<string, never>;
    support: FrustraMpnnStatisticsSupport | null;
}
export interface FrustraMpnnStatisticsResidueQueryRow extends FrustraMpnnStatisticsQueryRowBase {
    level: 'residue';
    key: FrustraMpnnStatisticsResidueKey | null;
    support: null;
}
export interface FrustraMpnnStatisticsMutationAAQueryRow extends FrustraMpnnStatisticsQueryRowBase {
    level: 'mutation_aa';
    key: FrustraMpnnStatisticsMutationAAKey | null;
    support: null;
}
export interface FrustraMpnnStatisticsChainQueryRow extends FrustraMpnnStatisticsQueryRowBase {
    level: 'chain';
    key: FrustraMpnnStatisticsChainKey | null;
    support: FrustraMpnnGroupSupport | null;
}
export interface FrustraMpnnStatisticsEntityQueryRow extends FrustraMpnnStatisticsQueryRowBase {
    level: 'entity';
    key: FrustraMpnnStatisticsEntityKey | null;
    support: FrustraMpnnGroupSupport | null;
}

export type FrustraMpnnStatisticsQueryRow =
    | FrustraMpnnStatisticsOverviewQueryRow
    | FrustraMpnnStatisticsResidueQueryRow
    | FrustraMpnnStatisticsMutationAAQueryRow
    | FrustraMpnnStatisticsChainQueryRow
    | FrustraMpnnStatisticsEntityQueryRow;

export interface FrustraMpnnStatisticsQueryResponse {
    items: FrustraMpnnStatisticsQueryRow[];
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
}

export interface FrustraMpnnArtifact {
    artifact_id: string;
    role: string;
    content_sha256: string;
    size_bytes: number;
    media_type: string;
    schema_name: string | null;
    schema_version: number | null;
    cardinality: FrustraMpnnTerminalArtifact['cardinality'];
    download_url: string;
}

export interface FrustraMpnnArtifactList {
    items: FrustraMpnnArtifact[];
    total: number;
}

export type FrustraMpnnArtifactIdentity = Pick<
    FrustraMpnnArtifact,
    'role' | 'media_type' | 'schema_name' | 'schema_version'
>;

export const selectFrustraMpnnArtifactByIdentity = (
    items: readonly FrustraMpnnArtifact[],
    identity: FrustraMpnnArtifactIdentity,
): FrustraMpnnArtifact | undefined => {
    const matches = items.filter((item) => (
        item.role === identity.role
        && item.media_type === identity.media_type
        && item.schema_name === identity.schema_name
        && item.schema_version === identity.schema_version
    ));
    if (matches.length > 1) {
        throw new Error(`FrustraMPNN artifact identity is ambiguous for role ${identity.role}`);
    }
    return matches[0];
};

export interface FrustraMpnnComparisonDifference {
    field_path: string;
    left: unknown;
    right: unknown;
}

export interface FrustraMpnnComparisonIdentity {
    entity_instance_id: string;
    source_entity_id: string;
    label_asym_id: string;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    wt: string;
}

export interface FrustraMpnnCompatibilityDomains {
    raw_score: {
        status: 'compatible' | 'hard_incompatible' | 'unknown';
        reasons: string[];
        differences: FrustraMpnnComparisonDifference[];
    };
    classification: {
        status: 'compatible' | 'policy_different' | 'unknown';
        reasons: string[];
        differences: FrustraMpnnComparisonDifference[];
    };
    identity_alignment: {
        status: 'exact' | 'partial' | 'none';
        reasons: string[];
        differences: Array<{ side: 'reference_only' | 'target_only'; identity: FrustraMpnnComparisonIdentity }>;
        reference_identity_count: number;
        target_identity_count: number;
        aligned_identity_count: number;
    };
}

export interface FrustraMpnnCompatibilityMetadata {
    compatibility_status: 'compatible' | 'incompatible' | 'unknown';
    left_comparison_compatibility_id: string | null;
    right_comparison_compatibility_id: string | null;
    override_used: boolean;
    compatibility_differences: FrustraMpnnComparisonDifference[];
}

export interface FrustraMpnnPairCompatibility extends FrustraMpnnCompatibilityMetadata {
    target_label: string;
    target_id: string | null;
    target_landscape_sha256: string;
    target_configuration_sha256: string | null;
    compatibility_domains: FrustraMpnnCompatibilityDomains;
}

export interface FrustraMpnnComparisonSide {
    sequence_index: number | null;
    auth_seq_id: number | null;
    score: number | null;
    class: FrustraMpnnClass | null;
    scoreable: boolean;
    status: string;
}

export interface FrustraMpnnComparisonRow {
    residue_key: { entity_instance_id: string; auth_asym_id: string; auth_seq_id: number; insertion_code: string };
    sequence_index: number | null;
    mutation_aa: string;
    wt: string | null;
    mapping_state: 'mapped' | 'unmapped';
    missingness_state: string;
    biological_status: string;
    reference: FrustraMpnnComparisonSide;
    target: FrustraMpnnComparisonSide;
    raw_score_delta: number | null;
    classification_transition: string | null;
}

export interface FrustraMpnnMultiComparisonRow {
    residue_key: FrustraMpnnComparisonRow['residue_key'];
    sequence_index: number | null;
    mutation_aa: string;
    mapping_state: 'mapped' | 'unmapped';
    missingness_state: string;
    missingness_by_target: string[];
    biological_status: string;
    reference: FrustraMpnnComparisonSide | null;
    targets: Array<FrustraMpnnComparisonSide | null>;
    raw_score_deltas: Array<number | null>;
    classification_transitions: Array<string | null>;
}

export interface FrustraMpnnResultReference {
    parent_job_id: string;
    invocation_id: string;
}

export const FRUSTRAMPNN_MULTI_TARGET_LIMIT = 8;

export interface FrustraMpnnSourceResultReference extends FrustraMpnnResultReference {
    role: 'reference' | 'target';
    target_label: string | null;
    landscape_sha256: string;
    configuration_sha256: string | null;
}

export interface FrustraMpnnPairComparisonSummary {
    total_rows: number;
    biologically_scored: number;
    incompatible: number;
    unmapped: number;
    missing_reference: number;
    missing_target: number;
    missing_both: number;
    transitions: number;
}

export interface FrustraMpnnMultiComparisonSummary {
    target_count: number;
    total_rows: number;
    biologically_scored: number;
    partially_scored: number;
    missing: number;
    unmapped: number;
    incompatible: number;
    transitions: number;
}

interface FrustraMpnnComparisonBase extends FrustraMpnnCompatibilityMetadata {
    schema_version: 1;
    comparison_id: string;
    comparison_sha256: string;
    reference_landscape_sha256: string;
    target_landscape_sha256: string;
    configuration_id: string | null;
    configuration_sha256: string | null;
    reference_configuration_sha256: string | null;
    persisted: true;
    created_at: string;
    reference: FrustraMpnnResultReference;
    target: FrustraMpnnResultReference;
}

export interface FrustraMpnnPairComparison extends FrustraMpnnComparisonBase {
    schema_name: 'frustrampnn_comparison';
    summary: FrustraMpnnPairComparisonSummary;
    target_configuration_sha256: string | null;
    comparability: {
        status: 'comparable' | 'incompatible';
        reasons: string[];
        reference_configuration_id: string | null;
        target_configuration_id: string | null;
        reference_configuration_sha256: string | null;
        target_configuration_sha256: string | null;
    };
    compatibility_domains: FrustraMpnnCompatibilityDomains;
    rows: FrustraMpnnComparisonRow[];
}

export interface FrustraMpnnMultiComparison extends FrustraMpnnComparisonBase {
    schema_name: 'frustrampnn_multistate_comparison';
    summary: FrustraMpnnMultiComparisonSummary;
    comparison_mode: 'multi_state';
    target_landscape_sha256s: string[];
    target_labels: string[];
    target_configuration_sha256s: Array<string | null>;
    pair_compatibility: FrustraMpnnPairCompatibility[];
    source_result_references: FrustraMpnnSourceResultReference[];
    comparability: FrustraMpnnCompatibilityMetadata & {
        status: 'comparable' | 'incompatible';
        reasons: string[];
        target_count: number;
        pair_compatibility: FrustraMpnnPairCompatibility[];
    };
    rows: FrustraMpnnMultiComparisonRow[];
}

export type FrustraMpnnComparison = FrustraMpnnPairComparison | FrustraMpnnMultiComparison;

export interface FrustraMpnnComparisonRowsPage {
    comparison_id: string;
    items: Array<(FrustraMpnnComparisonRow & { kind: 'pair' }) | (FrustraMpnnMultiComparisonRow & { kind: 'multi' })>;
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
}

const fmClosedProjection = (
    value: unknown,
    label: string,
    allowed: readonly string[],
    required: readonly string[],
): Record<string, unknown> => {
    const payload = fmRecord(value, label);
    const unknown = Object.keys(payload).filter((key) => !allowed.includes(key));
    const missing = required.filter((key) => !(key in payload));
    if (unknown.length || missing.length) {
        throw new Error(`${label} has unknown or missing keys (${[...unknown, ...missing].join(', ')})`);
    }
    return payload;
};

const fmBoolean = (value: unknown, label: string): boolean => {
    if (typeof value !== 'boolean') throw new Error(`${label} must be boolean`);
    return value;
};

const fmFinite = (value: unknown, label: string): number => {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be finite`);
    return value;
};

const fmNullableFinite = (value: unknown, label: string): number | null => (
    value === null ? null : fmFinite(value, label)
);

const fmNullableString = (value: unknown, label: string): string | null => (
    value === null ? null : fmString(value, label)
);

const fmOptionalSha256 = (value: unknown, label: string): string | null => (
    value === null ? null : fmSha256(value, label)
);

const fmOptionalString = (value: unknown, label: string): string | null => (
    value === null ? null : fmString(value, label)
);

const fmOptionalInteger = (value: unknown, label: string, minimum = 0): number | null => (
    value === null ? null : fmInteger(value, label, minimum)
);

const fmPdbInsertionCode = (value: unknown, label: string): string => {
    const insertionCode = fmString(value, label, true);
    if (insertionCode.length > 1) throw new Error(`${label} must contain at most one character`);
    return insertionCode;
};

const fmResidueName = (value: unknown, label: string): string => {
    const residueName = fmString(value, label);
    if (residueName.length !== 3) throw new Error(`${label} must contain exactly three characters`);
    return residueName;
};

const fmRuntimeIdentityProjection = (value: unknown): FrustraMpnnRuntimeIdentity => {
    const payload = fmRecord(value, 'runtime_identity');
    const safeKeys = [
        'runtime_identity_sha256', 'runtime_id', 'sif_name', 'sif_sha256', 'image_sha256', 'executable_sha256',
        'checkpoint_id', 'checkpoint_sha256', 'package_version', 'source_commit', 'python_version',
        'pytorch_version', 'image_version',
    ] as const;
    fmClosedProjection(payload, 'runtime_identity', safeKeys, []);
    const projection: FrustraMpnnRuntimeIdentity = {};
    for (const key of safeKeys) {
        if (payload[key] !== undefined) projection[key] = fmString(payload[key], `runtime_identity.${key}`);
    }
    return projection;
};

const fmGpuProvenanceProjection = (value: unknown, label = 'gpu_provenance'): FrustraMpnnGpuProvenance => {
    const payload = fmClosedProjection(
        value,
        label,
        ['physical_device_id', 'task_visible_device_index'],
        ['physical_device_id', 'task_visible_device_index'],
    );
    return {
        physical_device_id: fmString(payload.physical_device_id, `${label}.physical_device_id`),
        task_visible_device_index: payload.task_visible_device_index === null
            ? null
            : fmInteger(payload.task_visible_device_index, `${label}.task_visible_device_index`, 0),
    };
};

const parsePersistedRequestedSettingsProjection = (
    value: unknown,
    label: string,
): FrustraMpnnRequestedSettings => {
    const payload = fmClosedProjection(
        value,
        label,
        ['schema_name', 'schema_version', 'settings_value_origin', 'protein_selection', 'source_structure', 'classification_policy'],
        ['schema_name', 'schema_version', 'settings_value_origin', 'protein_selection', 'source_structure', 'classification_policy'],
    );
    if (payload.settings_value_origin !== 'bms_default' && payload.settings_value_origin !== 'operator_request') {
        throw new Error(`${label}.settings_value_origin is invalid`);
    }
    const persistedSelection = fmRecord(
        payload.protein_selection,
        `${label}.protein_selection`,
    );
    const proteinSelection = Object.hasOwn(persistedSelection, 'regions')
        ? persistedSelection
        : { ...persistedSelection, regions: [] };
    return parseFrustraMpnnRequestedSettings({
        schema_name: payload.schema_name,
        schema_version: payload.schema_version,
        protein_selection: proteinSelection,
        source_structure: payload.source_structure,
        classification_policy: payload.classification_policy,
    });
};

const fmValueOrigin = (value: unknown, label: string): 'bms_default' | 'operator_request' => {
    if (value !== 'bms_default' && value !== 'operator_request') throw new Error(`${label} is invalid`);
    return value;
};

export const parseFrustraMpnnEffectiveSettingsProjection = (
    value: unknown,
): FrustraMpnnEffectiveSettingsProjection => {
    const keys = [
        'schema_name', 'schema_version', 'requested_settings', 'settings_value_origin', 'resolved_chains',
        'normalization_policy_id', 'normalization_policy_version', 'threshold_policy_id',
        'threshold_policy_sha256', 'settings_sha256', 'capability_inventory_byte_sha256',
        'resolution_identity', 'value_sources', 'effective_settings_sha256',
    ] as const;
    const payload = fmClosedProjection(value, 'effective_settings', keys, keys);
    if (payload.schema_name !== 'frustrampnn_effective_settings' || payload.schema_version !== 1
        || payload.normalization_policy_id !== 'frustrampnn_structure_normalizer'
        || payload.normalization_policy_version !== 1
        || payload.threshold_policy_id !== 'frustrampnn_class_v1') {
        throw new Error('effective_settings schema or policy identity is invalid');
    }
    const resolution = fmClosedProjection(
        payload.resolution_identity,
        'effective_settings.resolution_identity',
        ['source_artifact_sha256', 'structure_map_schema_name', 'structure_map_schema_version', 'structure_map_sha256', 'normalized_pdb_sha256'],
        ['source_artifact_sha256', 'structure_map_schema_name', 'structure_map_schema_version', 'structure_map_sha256', 'normalized_pdb_sha256'],
    );
    if (!Array.isArray(payload.resolved_chains)) throw new Error('effective_settings.resolved_chains must be an array');
    const resolvedChains = payload.resolved_chains.map((item, chainIndex) => {
        const chainLabel = `effective_settings.resolved_chains[${chainIndex}]`;
        const chain = fmClosedProjection(item, chainLabel, ['entity', 'pdb_chain_id', 'residues'], ['entity', 'pdb_chain_id', 'residues']);
        const entityWire = fmClosedProjection(chain.entity, `${chainLabel}.entity`, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id'], ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id']);
        const pdbChainId = fmString(chain.pdb_chain_id, `${chainLabel}.pdb_chain_id`);
        if (!Array.isArray(chain.residues)) throw new Error(`${chainLabel}.residues must be an array`);
        const entity: FrustraMpnnInspectableEntity = {
            entity_instance_id: fmString(entityWire.entity_instance_id, `${chainLabel}.entity.entity_instance_id`),
            source_entity_id: fmNullableString(entityWire.source_entity_id, `${chainLabel}.entity.source_entity_id`),
            label_asym_id: fmNullableString(entityWire.label_asym_id, `${chainLabel}.entity.label_asym_id`),
            auth_asym_id: fmString(entityWire.auth_asym_id, `${chainLabel}.entity.auth_asym_id`),
            pdb_chain_id: pdbChainId,
        };
        return {
            entity,
            pdb_chain_id: pdbChainId,
            residues: chain.residues.map((residueValue, residueIndex) => {
                const residueLabel = `${chainLabel}.residues[${residueIndex}]`;
                const residueKeys = ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt', 'pdb_chain_id', 'pdb_residue_id', 'pdb_insertion_code', 'model_position', 'residue_name'] as const;
                const residue = fmClosedProjection(residueValue, residueLabel, residueKeys, residueKeys);
                const wt = fmString(residue.wt, `${residueLabel}.wt`);
                if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt)) throw new Error(`${residueLabel}.wt is invalid`);
                const pdbResidueId = fmInteger(residue.pdb_residue_id, `${residueLabel}.pdb_residue_id`, -999);
                if (pdbResidueId > 9999) throw new Error(`${residueLabel}.pdb_residue_id is invalid`);
                return {
                    entity_instance_id: fmString(residue.entity_instance_id, `${residueLabel}.entity_instance_id`),
                    source_entity_id: fmNullableString(residue.source_entity_id, `${residueLabel}.source_entity_id`),
                    label_asym_id: fmNullableString(residue.label_asym_id, `${residueLabel}.label_asym_id`),
                    label_seq_id: fmOptionalInteger(residue.label_seq_id, `${residueLabel}.label_seq_id`, 1),
                    auth_asym_id: fmString(residue.auth_asym_id, `${residueLabel}.auth_asym_id`),
                    auth_seq_id: fmInteger(residue.auth_seq_id, `${residueLabel}.auth_seq_id`),
                    insertion_code: fmString(residue.insertion_code, `${residueLabel}.insertion_code`, true),
                    sequence_index: fmInteger(residue.sequence_index, `${residueLabel}.sequence_index`, 1),
                    wt,
                    pdb_chain_id: fmString(residue.pdb_chain_id, `${residueLabel}.pdb_chain_id`),
                    pdb_residue_id: pdbResidueId,
                    pdb_insertion_code: fmPdbInsertionCode(residue.pdb_insertion_code, `${residueLabel}.pdb_insertion_code`),
                    model_position: fmInteger(residue.model_position, `${residueLabel}.model_position`, 0),
                    residue_name: fmResidueName(residue.residue_name, `${residueLabel}.residue_name`),
                };
            }),
        };
    });
    const valueSources = fmClosedProjection(payload.value_sources, 'effective_settings.value_sources', ['protein_selection', 'source_structure', 'classification_policy'], ['protein_selection', 'source_structure', 'classification_policy']);
    const proteinSources = fmClosedProjection(valueSources.protein_selection, 'effective_settings.value_sources.protein_selection', ['mode', 'entities', 'regions', 'residues'], ['mode', 'entities', 'residues']);
    const structureSources = fmClosedProjection(valueSources.source_structure, 'effective_settings.value_sources.source_structure', ['selected_model_number', 'preferred_altloc'], ['selected_model_number', 'preferred_altloc']);
    const classificationSources = fmClosedProjection(valueSources.classification_policy, 'effective_settings.value_sources.classification_policy', ['mode', 'high_max', 'minimal_min'], ['mode', 'high_max', 'minimal_min']);
    const settingsValueOrigin = fmValueOrigin(
        payload.settings_value_origin,
        'effective_settings.settings_value_origin',
    );
    return {
        schema_name: 'frustrampnn_effective_settings',
        schema_version: 1,
        requested_settings: parsePersistedRequestedSettingsProjection(payload.requested_settings, 'effective_settings.requested_settings'),
        settings_value_origin: settingsValueOrigin,
        resolved_chains: resolvedChains,
        normalization_policy_id: 'frustrampnn_structure_normalizer',
        normalization_policy_version: 1,
        threshold_policy_id: 'frustrampnn_class_v1',
        threshold_policy_sha256: fmSha256(payload.threshold_policy_sha256, 'effective_settings.threshold_policy_sha256'),
        settings_sha256: fmSha256(payload.settings_sha256, 'effective_settings.settings_sha256'),
        capability_inventory_byte_sha256: fmSha256(payload.capability_inventory_byte_sha256, 'effective_settings.capability_inventory_byte_sha256'),
        resolution_identity: {
            source_artifact_sha256: fmSha256(resolution.source_artifact_sha256, 'effective_settings.resolution_identity.source_artifact_sha256'),
            structure_map_schema_name: fmString(resolution.structure_map_schema_name, 'effective_settings.resolution_identity.structure_map_schema_name'),
            structure_map_schema_version: fmInteger(resolution.structure_map_schema_version, 'effective_settings.resolution_identity.structure_map_schema_version', 1),
            structure_map_sha256: fmSha256(resolution.structure_map_sha256, 'effective_settings.resolution_identity.structure_map_sha256'),
            normalized_pdb_sha256: fmSha256(resolution.normalized_pdb_sha256, 'effective_settings.resolution_identity.normalized_pdb_sha256'),
        },
        value_sources: {
            protein_selection: {
                mode: fmValueOrigin(proteinSources.mode, 'effective_settings.value_sources.protein_selection.mode'),
                entities: fmValueOrigin(proteinSources.entities, 'effective_settings.value_sources.protein_selection.entities'),
                regions: proteinSources.regions === undefined
                    ? settingsValueOrigin
                    : fmValueOrigin(proteinSources.regions, 'effective_settings.value_sources.protein_selection.regions'),
                residues: fmValueOrigin(proteinSources.residues, 'effective_settings.value_sources.protein_selection.residues'),
            },
            source_structure: {
                selected_model_number: fmValueOrigin(structureSources.selected_model_number, 'effective_settings.value_sources.source_structure.selected_model_number'),
                preferred_altloc: fmValueOrigin(structureSources.preferred_altloc, 'effective_settings.value_sources.source_structure.preferred_altloc'),
            },
            classification_policy: {
                mode: fmValueOrigin(classificationSources.mode, 'effective_settings.value_sources.classification_policy.mode'),
                high_max: fmValueOrigin(classificationSources.high_max, 'effective_settings.value_sources.classification_policy.high_max'),
                minimal_min: fmValueOrigin(classificationSources.minimal_min, 'effective_settings.value_sources.classification_policy.minimal_min'),
            },
        },
        effective_settings_sha256: fmSha256(payload.effective_settings_sha256, 'effective_settings.effective_settings_sha256'),
    };
};

const CHILD_RECEIPT_KEYS = [
    'job_id', 'child_job_id', 'result_job_id', 'name', 'parent_job_id', 'source_parent_job_id',
    'trigger', 'status', 'created_at', 'started_at', 'completed_at', 'settings_value_origin',
    'requested_settings', 'requested_settings_sha256', 'candidates', 'results',
] as const;
const CHILD_CANDIDATE_KEYS = [
    'selection_ordinal', 'design_id', 'source_job_id', 'candidate_id', 'invocation_id',
    'source_artifact_id', 'source_artifact_sha256', 'component_request_sha256',
    'normalized_pdb_sha256', 'structure_map_sha256', 'settings_value_origin',
    'requested_settings_sha256', 'effective_settings', 'effective_settings_sha256',
    'capability_inventory_byte_sha256', 'classification_policy_sha256',
    'execution_configuration_sha256', 'runtime_identity_sha256', 'producer',
] as const;
const RECEIPT_PRODUCER_KEYS = [
    'producer_stage', 'producer_id', 'source_stage_family', 'source_stage_mode', 'artifact_class',
    'parent_job_id', 'parent_invocation_id', 'parent_landscape_sha256', 'guidance_id',
    'protein_sequence_sha256',
] as const;
const CHILD_RESULT_KEYS = [
    'candidate_id', 'design_id', 'source_artifact_id', 'source_artifact_sha256', 'invocation_id',
    'request_sha256', 'status', 'manifest_sha256', 'gpu_provenance',
] as const;
const HANDOFF_METADATA_KEYS = [
    'parent_landscape_sha256', 'parent_candidate_id', 'guidance_id', 'producer_id',
] as const;

const parseReceiptProducer = (value: unknown, label: string): FrustraMpnnReceiptProducer => {
    const payload = fmClosedProjection(value, label, RECEIPT_PRODUCER_KEYS, RECEIPT_PRODUCER_KEYS);
    return {
        producer_stage: fmNullableString(payload.producer_stage, `${label}.producer_stage`),
        producer_id: fmNullableString(payload.producer_id, `${label}.producer_id`),
        source_stage_family: fmNullableString(payload.source_stage_family, `${label}.source_stage_family`),
        source_stage_mode: fmNullableString(payload.source_stage_mode, `${label}.source_stage_mode`),
        artifact_class: fmNullableString(payload.artifact_class, `${label}.artifact_class`),
        parent_job_id: fmNullableString(payload.parent_job_id, `${label}.parent_job_id`),
        parent_invocation_id: fmNullableString(payload.parent_invocation_id, `${label}.parent_invocation_id`),
        parent_landscape_sha256: fmOptionalSha256(payload.parent_landscape_sha256, `${label}.parent_landscape_sha256`),
        guidance_id: fmNullableString(payload.guidance_id, `${label}.guidance_id`),
        protein_sequence_sha256: fmOptionalSha256(payload.protein_sequence_sha256, `${label}.protein_sequence_sha256`),
    };
};

export const parseFrustraMpnnChildReceipt = (
    value: unknown,
    expectHandoff = false,
): FrustraMpnnChildReceipt => {
    const keys = expectHandoff ? [...CHILD_RECEIPT_KEYS, 'handoff'] : CHILD_RECEIPT_KEYS;
    const payload = fmClosedProjection(value, 'FrustraMPNN child receipt', keys, keys);
    if (!Array.isArray(payload.candidates) || !Array.isArray(payload.results)) {
        throw new Error('FrustraMPNN child receipt candidates and results must be arrays');
    }
    const origin = fmValueOrigin(payload.settings_value_origin, 'child receipt.settings_value_origin');
    const candidates = payload.candidates.map((item, index): FrustraMpnnChildCandidateReceipt => {
        const label = `child receipt.candidates[${index}]`;
        const candidate = fmClosedProjection(item, label, CHILD_CANDIDATE_KEYS, CHILD_CANDIDATE_KEYS);
        const candidateOrigin = candidate.settings_value_origin === null
            ? null
            : fmValueOrigin(candidate.settings_value_origin, `${label}.settings_value_origin`);
        return {
            selection_ordinal: fmOptionalInteger(candidate.selection_ordinal, `${label}.selection_ordinal`),
            design_id: fmNullableString(candidate.design_id, `${label}.design_id`),
            source_job_id: fmNullableString(candidate.source_job_id, `${label}.source_job_id`),
            candidate_id: fmNullableString(candidate.candidate_id, `${label}.candidate_id`),
            invocation_id: fmNullableString(candidate.invocation_id, `${label}.invocation_id`),
            source_artifact_id: fmNullableString(candidate.source_artifact_id, `${label}.source_artifact_id`),
            source_artifact_sha256: fmOptionalSha256(candidate.source_artifact_sha256, `${label}.source_artifact_sha256`),
            component_request_sha256: fmOptionalSha256(candidate.component_request_sha256, `${label}.component_request_sha256`),
            normalized_pdb_sha256: fmOptionalSha256(candidate.normalized_pdb_sha256, `${label}.normalized_pdb_sha256`),
            structure_map_sha256: fmOptionalSha256(candidate.structure_map_sha256, `${label}.structure_map_sha256`),
            settings_value_origin: candidateOrigin,
            requested_settings_sha256: fmOptionalSha256(candidate.requested_settings_sha256, `${label}.requested_settings_sha256`),
            effective_settings: candidate.effective_settings === null ? null : parseFrustraMpnnEffectiveSettingsProjection(candidate.effective_settings),
            effective_settings_sha256: fmOptionalSha256(candidate.effective_settings_sha256, `${label}.effective_settings_sha256`),
            capability_inventory_byte_sha256: fmOptionalSha256(candidate.capability_inventory_byte_sha256, `${label}.capability_inventory_byte_sha256`),
            classification_policy_sha256: fmOptionalSha256(candidate.classification_policy_sha256, `${label}.classification_policy_sha256`),
            execution_configuration_sha256: fmOptionalSha256(candidate.execution_configuration_sha256, `${label}.execution_configuration_sha256`),
            runtime_identity_sha256: fmOptionalSha256(candidate.runtime_identity_sha256, `${label}.runtime_identity_sha256`),
            producer: candidate.producer === null ? null : parseReceiptProducer(candidate.producer, `${label}.producer`),
        };
    });
    const results = payload.results.map((item, index): FrustraMpnnReceiptResult => {
        const label = `child receipt.results[${index}]`;
        const result = fmClosedProjection(item, label, CHILD_RESULT_KEYS, CHILD_RESULT_KEYS);
        const status = result.status;
        if (status !== null && status !== 'succeeded' && status !== 'failed' && status !== 'not_run') {
            throw new Error(`${label}.status is invalid`);
        }
        return {
            candidate_id: fmString(result.candidate_id, `${label}.candidate_id`),
            design_id: fmNullableString(result.design_id, `${label}.design_id`),
            source_artifact_id: fmNullableString(result.source_artifact_id, `${label}.source_artifact_id`),
            source_artifact_sha256: fmSha256(result.source_artifact_sha256, `${label}.source_artifact_sha256`),
            invocation_id: fmString(result.invocation_id, `${label}.invocation_id`),
            request_sha256: fmSha256(result.request_sha256, `${label}.request_sha256`),
            status,
            manifest_sha256: fmSha256(result.manifest_sha256, `${label}.manifest_sha256`),
            gpu_provenance: result.gpu_provenance === null ? null : fmGpuProvenanceProjection(result.gpu_provenance, `${label}.gpu_provenance`),
        };
    });
    const handoff = expectHandoff
        ? fmClosedProjection(payload.handoff, 'child receipt.handoff', HANDOFF_METADATA_KEYS, HANDOFF_METADATA_KEYS)
        : null;
    return {
        job_id: fmString(payload.job_id, 'child receipt.job_id'),
        child_job_id: fmString(payload.child_job_id, 'child receipt.child_job_id'),
        result_job_id: fmString(payload.result_job_id, 'child receipt.result_job_id'),
        name: fmString(payload.name, 'child receipt.name'),
        parent_job_id: fmNullableString(payload.parent_job_id, 'child receipt.parent_job_id'),
        source_parent_job_id: fmNullableString(payload.source_parent_job_id, 'child receipt.source_parent_job_id'),
        trigger: fmString(payload.trigger, 'child receipt.trigger'),
        status: fmString(payload.status, 'child receipt.status'),
        created_at: fmOptionalString(payload.created_at, 'child receipt.created_at'),
        started_at: fmOptionalString(payload.started_at, 'child receipt.started_at'),
        completed_at: fmOptionalString(payload.completed_at, 'child receipt.completed_at'),
        settings_value_origin: origin,
        requested_settings: parsePersistedRequestedSettingsProjection(payload.requested_settings, 'child receipt.requested_settings'),
        requested_settings_sha256: fmSha256(payload.requested_settings_sha256, 'child receipt.requested_settings_sha256'),
        candidates,
        results,
        ...(handoff ? {
            handoff: {
                parent_landscape_sha256: fmSha256(handoff.parent_landscape_sha256, 'child receipt.handoff.parent_landscape_sha256'),
                parent_candidate_id: fmString(handoff.parent_candidate_id, 'child receipt.handoff.parent_candidate_id'),
                guidance_id: fmNullableString(handoff.guidance_id, 'child receipt.handoff.guidance_id'),
                producer_id: fmString(handoff.producer_id, 'child receipt.handoff.producer_id'),
            },
        } : {}),
    };
};

const STATISTIC_NAMES = ['count', 'mean', 'median', 'sample_sd', 'min', 'max', 'q1', 'q3', 'iqr'] as const;
const STATISTICS_RESIDUE_KEYS = ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt', 'pdb_chain_id', 'model_position'] as const;
const STATISTICS_SUPPORT_COUNT_KEYS = [
    'source_residue_count', 'selected_residue_count', 'observed_residue_count', 'scoreable_residue_count',
    'excluded_residue_count', 'missing_residue_count', 'mapping_missing_residue_count',
    'selected_missing_residue_count', 'fully_scoreable_residue_count', 'partially_scoreable_residue_count',
    'expected_slot_count', 'observed_slot_count', 'scoreable_slot_count', 'excluded_slot_count',
    'mapping_missing_slot_count', 'missing_slot_count',
] as const;
const STATISTICS_GROUP_SUPPORT_KEYS = [
    'selected_residue_count', 'observed_residue_count', 'fully_scoreable_residue_count',
    'expected_slot_count', 'observed_slot_count', 'scoreable_slot_count',
] as const;

const fmSchemaNullableString = (value: unknown, label: string): string | null => (
    value === null ? null : fmString(value, label, true)
);

const fmFraction = (value: unknown, label: string): number => {
    const parsed = fmFinite(value, label);
    if (parsed < 0 || parsed > 1) throw new Error(`${label} must be between 0 and 1`);
    return parsed;
};

const fmNullableFraction = (value: unknown, label: string): number | null => (
    value === null ? null : fmFraction(value, label)
);

const parseStatisticsClass = (value: unknown, label: string): FrustraMpnnClass => {
    if (value !== 'high' && value !== 'neutral' && value !== 'minimal') throw new Error(`${label} is invalid`);
    return value;
};

const parseDenominator = (value: unknown, label: string): FrustraMpnnDenominator => {
    const payload = fmClosedProjection(value, label, ['kind', 'count'], ['kind', 'count']);
    return { kind: fmString(payload.kind, `${label}.kind`), count: fmInteger(payload.count, `${label}.count`, 0) };
};

const parseFractionMetric = (value: unknown, label: string): FrustraMpnnFractionMetric => {
    const payload = fmClosedProjection(value, label, ['value', 'denominator', 'missingness_reason'], ['value', 'denominator', 'missingness_reason']);
    return {
        value: fmNullableFraction(payload.value, `${label}.value`),
        denominator: parseDenominator(payload.denominator, `${label}.denominator`),
        missingness_reason: fmSchemaNullableString(payload.missingness_reason, `${label}.missingness_reason`),
    };
};

const parseDistribution = (value: unknown, label: string): FrustraMpnnDistribution => {
    const payload = fmClosedProjection(value, label, [...STATISTIC_NAMES, 'denominators', 'missingness_reasons'], [...STATISTIC_NAMES, 'denominators', 'missingness_reasons']);
    const denominators = fmClosedProjection(payload.denominators, `${label}.denominators`, STATISTIC_NAMES, STATISTIC_NAMES);
    const missingness = fmClosedProjection(payload.missingness_reasons, `${label}.missingness_reasons`, STATISTIC_NAMES, STATISTIC_NAMES);
    const parsedDenominators = {} as FrustraMpnnDistribution['denominators'];
    const parsedMissingness = {} as FrustraMpnnDistribution['missingness_reasons'];
    for (const name of STATISTIC_NAMES) {
        parsedDenominators[name] = parseDenominator(denominators[name], `${label}.denominators.${name}`);
        parsedMissingness[name] = fmSchemaNullableString(missingness[name], `${label}.missingness_reasons.${name}`);
    }
    return {
        count: fmInteger(payload.count, `${label}.count`, 0),
        mean: fmNullableFinite(payload.mean, `${label}.mean`),
        median: fmNullableFinite(payload.median, `${label}.median`),
        sample_sd: fmNullableFinite(payload.sample_sd, `${label}.sample_sd`),
        min: fmNullableFinite(payload.min, `${label}.min`),
        max: fmNullableFinite(payload.max, `${label}.max`),
        q1: fmNullableFinite(payload.q1, `${label}.q1`),
        q3: fmNullableFinite(payload.q3, `${label}.q3`),
        iqr: fmNullableFinite(payload.iqr, `${label}.iqr`),
        denominators: parsedDenominators,
        missingness_reasons: parsedMissingness,
    };
};

const parseClassBurden = (value: unknown, label: string): FrustraMpnnClassBurden => {
    const keys = ['support_count', 'counts', 'fractions', 'denominator', 'missingness_reason'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const counts = fmClosedProjection(payload.counts, `${label}.counts`, ['high', 'neutral', 'minimal'], ['high', 'neutral', 'minimal']);
    const fractions = fmClosedProjection(payload.fractions, `${label}.fractions`, ['high', 'neutral', 'minimal'], ['high', 'neutral', 'minimal']);
    return {
        support_count: fmInteger(payload.support_count, `${label}.support_count`, 0),
        counts: {
            high: fmInteger(counts.high, `${label}.counts.high`, 0),
            neutral: fmInteger(counts.neutral, `${label}.counts.neutral`, 0),
            minimal: fmInteger(counts.minimal, `${label}.counts.minimal`, 0),
        },
        fractions: {
            high: fmNullableFraction(fractions.high, `${label}.fractions.high`),
            neutral: fmNullableFraction(fractions.neutral, `${label}.fractions.neutral`),
            minimal: fmNullableFraction(fractions.minimal, `${label}.fractions.minimal`),
        },
        denominator: parseDenominator(payload.denominator, `${label}.denominator`),
        missingness_reason: fmSchemaNullableString(payload.missingness_reason, `${label}.missingness_reason`),
    };
};

const parseStatisticsReasonCounts = (value: unknown, label: string): FrustraMpnnStatisticsSupport['exclusion_reasons'] => {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
    return value.map((item, index) => {
        const itemLabel = `${label}[${index}]`;
        const payload = fmClosedProjection(item, itemLabel, ['authority', 'status', 'reason_code', 'reason', 'count'], ['authority', 'status', 'reason_code', 'reason', 'count']);
        if (payload.authority !== 'structure_map_row' && payload.authority !== 'excluded_record' && payload.authority !== 'landscape') throw new Error(`${itemLabel}.authority is invalid`);
        return {
            authority: payload.authority,
            status: fmString(payload.status, `${itemLabel}.status`),
            reason_code: fmString(payload.reason_code, `${itemLabel}.reason_code`),
            reason: fmString(payload.reason, `${itemLabel}.reason`),
            count: fmInteger(payload.count, `${itemLabel}.count`, 1),
        };
    });
};

const parseStatisticsSupport = (value: unknown, label: string): FrustraMpnnStatisticsSupport => {
    const keys = [...STATISTICS_SUPPORT_COUNT_KEYS, 'residue_fractions', 'slot_fractions', 'exclusion_reasons', 'missing_reasons'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const residueFractionKeys = ['selected', 'observed', 'scoreable', 'excluded', 'missing', 'selected_missing'] as const;
    const slotFractionKeys = ['observed', 'scoreable', 'excluded', 'missing', 'selected_missing'] as const;
    const residueFractions = fmClosedProjection(payload.residue_fractions, `${label}.residue_fractions`, residueFractionKeys, residueFractionKeys);
    const slotFractions = fmClosedProjection(payload.slot_fractions, `${label}.slot_fractions`, slotFractionKeys, slotFractionKeys);
    const counts = {} as Pick<FrustraMpnnStatisticsSupport, typeof STATISTICS_SUPPORT_COUNT_KEYS[number]>;
    for (const key of STATISTICS_SUPPORT_COUNT_KEYS) counts[key] = fmInteger(payload[key], `${label}.${key}`, 0);
    return {
        ...counts,
        residue_fractions: {
            selected: parseFractionMetric(residueFractions.selected, `${label}.residue_fractions.selected`),
            observed: parseFractionMetric(residueFractions.observed, `${label}.residue_fractions.observed`),
            scoreable: parseFractionMetric(residueFractions.scoreable, `${label}.residue_fractions.scoreable`),
            excluded: parseFractionMetric(residueFractions.excluded, `${label}.residue_fractions.excluded`),
            missing: parseFractionMetric(residueFractions.missing, `${label}.residue_fractions.missing`),
            selected_missing: parseFractionMetric(residueFractions.selected_missing, `${label}.residue_fractions.selected_missing`),
        },
        slot_fractions: {
            observed: parseFractionMetric(slotFractions.observed, `${label}.slot_fractions.observed`),
            scoreable: parseFractionMetric(slotFractions.scoreable, `${label}.slot_fractions.scoreable`),
            excluded: parseFractionMetric(slotFractions.excluded, `${label}.slot_fractions.excluded`),
            missing: parseFractionMetric(slotFractions.missing, `${label}.slot_fractions.missing`),
            selected_missing: parseFractionMetric(slotFractions.selected_missing, `${label}.slot_fractions.selected_missing`),
        },
        exclusion_reasons: parseStatisticsReasonCounts(payload.exclusion_reasons, `${label}.exclusion_reasons`),
        missing_reasons: parseStatisticsReasonCounts(payload.missing_reasons, `${label}.missing_reasons`),
    };
};

const parseGroupSupport = (value: unknown, label: string): FrustraMpnnGroupSupport => {
    const payload = fmClosedProjection(value, label, STATISTICS_GROUP_SUPPORT_KEYS, STATISTICS_GROUP_SUPPORT_KEYS);
    return {
        selected_residue_count: fmInteger(payload.selected_residue_count, `${label}.selected_residue_count`, 0),
        observed_residue_count: fmInteger(payload.observed_residue_count, `${label}.observed_residue_count`, 0),
        fully_scoreable_residue_count: fmInteger(payload.fully_scoreable_residue_count, `${label}.fully_scoreable_residue_count`, 0),
        expected_slot_count: fmInteger(payload.expected_slot_count, `${label}.expected_slot_count`, 0),
        observed_slot_count: fmInteger(payload.observed_slot_count, `${label}.observed_slot_count`, 0),
        scoreable_slot_count: fmInteger(payload.scoreable_slot_count, `${label}.scoreable_slot_count`, 0),
    };
};

const parseStatisticsResidueFields = (payload: Record<string, unknown>, label: string): FrustraMpnnStatisticsResidueIdentity => {
    const wt = fmString(payload.wt, `${label}.wt`);
    const pdbChainId = fmString(payload.pdb_chain_id, `${label}.pdb_chain_id`);
    const insertionCode = fmString(payload.insertion_code, `${label}.insertion_code`, true);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt) || !/^[A-Za-z0-9]$/.test(pdbChainId) || insertionCode.length > 1) throw new Error(`${label} residue identity is invalid`);
    return {
        entity_instance_id: fmString(payload.entity_instance_id, `${label}.entity_instance_id`),
        source_entity_id: fmSchemaNullableString(payload.source_entity_id, `${label}.source_entity_id`),
        label_asym_id: fmSchemaNullableString(payload.label_asym_id, `${label}.label_asym_id`),
        auth_asym_id: fmString(payload.auth_asym_id, `${label}.auth_asym_id`),
        auth_seq_id: fmInteger(payload.auth_seq_id, `${label}.auth_seq_id`),
        insertion_code: insertionCode,
        sequence_index: fmInteger(payload.sequence_index, `${label}.sequence_index`, 1),
        wt,
        pdb_chain_id: pdbChainId,
        model_position: fmInteger(payload.model_position, `${label}.model_position`, 0),
    };
};

const parseStatisticsResidueIdentity = (value: unknown, label: string): FrustraMpnnStatisticsResidueIdentity => {
    const payload = fmClosedProjection(value, label, STATISTICS_RESIDUE_KEYS, STATISTICS_RESIDUE_KEYS);
    return parseStatisticsResidueFields(payload, label);
};

const parseStatisticsBasis = (value: unknown): FrustraMpnnStatistics['comparison_compatibility_basis'] => {
    const label = 'statistics.comparison_compatibility_basis';
    const payload = fmClosedProjection(value, label, ['schema_name', 'schema_version', 'raw_score_semantics', 'classification_policy'], ['schema_name', 'schema_version', 'raw_score_semantics', 'classification_policy']);
    if (payload.schema_name !== 'frustrampnn_comparison_compatibility_basis' || payload.schema_version !== 2) throw new Error(`${label} schema identity is invalid`);
    const raw = fmClosedProjection(payload.raw_score_semantics, `${label}.raw_score_semantics`, ['model', 'tool', 'capability', 'output_schema', 'canonical_amino_acid_order', 'normalization'], ['model', 'tool', 'capability', 'output_schema', 'canonical_amino_acid_order', 'normalization']);
    const model = fmClosedProjection(raw.model, `${label}.raw_score_semantics.model`, ['checkpoint_id', 'checkpoint_sha256'], ['checkpoint_id', 'checkpoint_sha256']);
    const tool = fmClosedProjection(raw.tool, `${label}.raw_score_semantics.tool`, ['tool_id', 'tool_version'], ['tool_id', 'tool_version']);
    const capability = fmClosedProjection(raw.capability, `${label}.raw_score_semantics.capability`, ['schema_name', 'schema_version', 'content_sha256'], ['schema_name', 'schema_version', 'content_sha256']);
    const output = fmClosedProjection(raw.output_schema, `${label}.raw_score_semantics.output_schema`, ['component_id', 'component_contract_version', 'landscape_schema_name', 'landscape_schema_version', 'score_field'], ['component_id', 'component_contract_version', 'landscape_schema_name', 'landscape_schema_version', 'score_field']);
    const normalization = fmClosedProjection(raw.normalization, `${label}.raw_score_semantics.normalization`, ['normalizer_version', 'identity_authority', 'identity_domain', 'selected_source_model', 'altloc_policy', 'normalization_policy_id', 'normalization_policy_version'], ['normalizer_version', 'identity_authority', 'identity_domain', 'selected_source_model', 'altloc_policy', 'normalization_policy_id', 'normalization_policy_version']);
    const classification = fmClosedProjection(payload.classification_policy, `${label}.classification_policy`, ['policy_id', 'policy_sha256', 'policy'], ['policy_id', 'policy_sha256', 'policy']);
    const policy = fmClosedProjection(classification.policy, `${label}.classification_policy.policy`, ['mode', 'high_max', 'minimal_min'], ['mode', 'high_max', 'minimal_min']);
    if (capability.schema_name !== 'frustrampnn_capability_inventory' || capability.schema_version !== 1
        || output.component_id !== 'frustrampnn' || output.component_contract_version !== '2.0'
        || output.landscape_schema_name !== 'frustrampnn_landscape' || output.landscape_schema_version !== 2
        || output.score_field !== 'score' || raw.canonical_amino_acid_order !== 'ACDEFGHIKLMNPQRSTVWY'
        || normalization.normalization_policy_id !== 'frustrampnn_structure_normalizer'
        || normalization.normalization_policy_version !== 1
        || (policy.mode !== 'canonical' && policy.mode !== 'custom')) throw new Error(`${label} contains an invalid closed identity`);
    const highMax = fmFinite(policy.high_max, `${label}.classification_policy.policy.high_max`);
    const minimalMin = fmFinite(policy.minimal_min, `${label}.classification_policy.policy.minimal_min`);
    if (policy.mode === 'canonical' && (highMax !== -1 || minimalMin !== 0.58)) throw new Error(`${label} canonical policy thresholds are invalid`);
    return {
        schema_name: 'frustrampnn_comparison_compatibility_basis', schema_version: 2,
        raw_score_semantics: {
            model: { checkpoint_id: fmString(model.checkpoint_id, `${label}.raw_score_semantics.model.checkpoint_id`), checkpoint_sha256: fmSha256(model.checkpoint_sha256, `${label}.raw_score_semantics.model.checkpoint_sha256`) },
            tool: { tool_id: fmString(tool.tool_id, `${label}.raw_score_semantics.tool.tool_id`), tool_version: fmString(tool.tool_version, `${label}.raw_score_semantics.tool.tool_version`) },
            capability: { schema_name: 'frustrampnn_capability_inventory', schema_version: 1, content_sha256: fmSha256(capability.content_sha256, `${label}.raw_score_semantics.capability.content_sha256`) },
            output_schema: { component_id: 'frustrampnn', component_contract_version: '2.0', landscape_schema_name: 'frustrampnn_landscape', landscape_schema_version: 2, score_field: 'score' },
            canonical_amino_acid_order: 'ACDEFGHIKLMNPQRSTVWY',
            normalization: {
                normalizer_version: fmString(normalization.normalizer_version, `${label}.raw_score_semantics.normalization.normalizer_version`),
                identity_authority: fmString(normalization.identity_authority, `${label}.raw_score_semantics.normalization.identity_authority`),
                identity_domain: fmString(normalization.identity_domain, `${label}.raw_score_semantics.normalization.identity_domain`),
                selected_source_model: fmInteger(normalization.selected_source_model, `${label}.raw_score_semantics.normalization.selected_source_model`, 1),
                altloc_policy: fmString(normalization.altloc_policy, `${label}.raw_score_semantics.normalization.altloc_policy`),
                normalization_policy_id: 'frustrampnn_structure_normalizer', normalization_policy_version: 1,
            },
        },
        classification_policy: {
            policy_id: fmString(classification.policy_id, `${label}.classification_policy.policy_id`),
            policy_sha256: fmSha256(classification.policy_sha256, `${label}.classification_policy.policy_sha256`),
            policy: { mode: policy.mode, high_max: highMax, minimal_min: minimalMin },
        },
    };
};

const parseRankedAlternative = (value: unknown, index: number, direction: string): FrustraMpnnRankedAlternative => {
    const label = `statistics.ranked_non_native_alternatives.${direction}[${index}]`;
    const keys = [...STATISTICS_RESIDUE_KEYS, 'mutation_aa', 'score', 'score_class', 'native_score', 'delta', 'rank'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const mutation = fmString(payload.mutation_aa, `${label}.mutation_aa`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(mutation)) throw new Error(`${label}.mutation_aa is invalid`);
    return {
        ...parseStatisticsResidueFields(payload, label),
        mutation_aa: mutation,
        score: fmFinite(payload.score, `${label}.score`),
        score_class: parseStatisticsClass(payload.score_class, `${label}.score_class`),
        native_score: fmFinite(payload.native_score, `${label}.native_score`),
        delta: fmFinite(payload.delta, `${label}.delta`),
        rank: fmInteger(payload.rank, `${label}.rank`, 1),
    };
};

export const parseFrustraMpnnStatistics = (value: unknown): FrustraMpnnStatistics => {
    const keys = [
        'schema_name', 'schema_version', 'hash_semantics', 'invocation_id', 'parent_job_id', 'candidate_id',
        'target_id', 'landscape_sha256', 'source_artifact_sha256', 'normalized_pdb_sha256', 'settings_sha256',
        'effective_settings_sha256', 'capability_inventory_content_sha256', 'capability_inventory_byte_sha256',
        'configuration_sha256', 'runtime_identity_sha256', 'classification_policy_sha256', 'execution_plan_sha256',
        'comparison_compatibility_id', 'statistics_sha256', 'structure_map', 'output_contract_version',
        'canonical_amino_acid_order', 'comparison_compatibility_basis', 'support', 'distributions', 'per_residue',
        'per_mutation_amino_acid', 'per_chain', 'per_entity', 'native_vs_alternative',
        'contiguous_native_class_regions', 'ranked_non_native_alternatives', 'class_burden',
    ] as const;
    const payload = fmClosedProjection(value, 'statistics', keys, keys);
    if (payload.schema_name !== 'frustrampnn_statistics' || payload.schema_version !== 1
        || payload.hash_semantics !== 'sha256(rfc8785(document_without_top_level_statistics_sha256))'
        || payload.output_contract_version !== '2.0'
        || payload.canonical_amino_acid_order !== 'ACDEFGHIKLMNPQRSTVWY') throw new Error('statistics schema identity is invalid');
    const structureMap = fmClosedProjection(payload.structure_map, 'statistics.structure_map', ['schema_name', 'schema_version', 'sha256'], ['schema_name', 'schema_version', 'sha256']);
    if (structureMap.schema_name !== 'frustrampnn_structure_map' || structureMap.schema_version !== 1) throw new Error('statistics.structure_map schema identity is invalid');
    const distributions = fmClosedProjection(payload.distributions, 'statistics.distributions', ['overall', 'native', 'non_native'], ['overall', 'native', 'non_native']);
    const burdens = fmClosedProjection(payload.class_burden, 'statistics.class_burden', ['all', 'native', 'non_native'], ['all', 'native', 'non_native']);
    const nativeAlternative = fmClosedProjection(payload.native_vs_alternative, 'statistics.native_vs_alternative', ['native_mean', 'alternative_mean', 'alternative_minus_native', 'denominators', 'missingness_reasons'], ['native_mean', 'alternative_mean', 'alternative_minus_native', 'denominators', 'missingness_reasons']);
    const nativeAlternativeDenominators = fmClosedProjection(nativeAlternative.denominators, 'statistics.native_vs_alternative.denominators', ['native_mean', 'alternative_mean'], ['native_mean', 'alternative_mean']);
    const nativeAlternativeMissingness = fmClosedProjection(nativeAlternative.missingness_reasons, 'statistics.native_vs_alternative.missingness_reasons', ['native_mean', 'alternative_mean'], ['native_mean', 'alternative_mean']);
    if (!Array.isArray(payload.per_chain) || payload.per_chain.length < 1 || !Array.isArray(payload.per_entity) || payload.per_entity.length < 1
        || !Array.isArray(payload.per_residue) || payload.per_residue.length < 1
        || !Array.isArray(payload.per_mutation_amino_acid) || payload.per_mutation_amino_acid.length !== 20
        || !Array.isArray(payload.contiguous_native_class_regions) || payload.contiguous_native_class_regions.length < 1) throw new Error('statistics grouped summaries have invalid cardinality');
    const perChain = payload.per_chain.map((item, index) => {
        const label = `statistics.per_chain[${index}]`;
        const row = fmClosedProjection(item, label, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id', 'support', 'all', 'native', 'non_native'], ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id', 'support', 'all', 'native', 'non_native']);
        return {
            entity_instance_id: fmString(row.entity_instance_id, `${label}.entity_instance_id`, true),
            source_entity_id: fmSchemaNullableString(row.source_entity_id, `${label}.source_entity_id`),
            label_asym_id: fmSchemaNullableString(row.label_asym_id, `${label}.label_asym_id`),
            auth_asym_id: fmString(row.auth_asym_id, `${label}.auth_asym_id`, true),
            pdb_chain_id: fmString(row.pdb_chain_id, `${label}.pdb_chain_id`, true),
            support: parseGroupSupport(row.support, `${label}.support`),
            all: parseDistribution(row.all, `${label}.all`), native: parseDistribution(row.native, `${label}.native`), non_native: parseDistribution(row.non_native, `${label}.non_native`),
        };
    });
    const perEntity = payload.per_entity.map((item, index) => {
        const label = `statistics.per_entity[${index}]`;
        const row = fmClosedProjection(item, label, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'support', 'all', 'native', 'non_native'], ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'support', 'all', 'native', 'non_native']);
        return {
            entity_instance_id: fmString(row.entity_instance_id, `${label}.entity_instance_id`, true),
            source_entity_id: fmSchemaNullableString(row.source_entity_id, `${label}.source_entity_id`),
            label_asym_id: fmSchemaNullableString(row.label_asym_id, `${label}.label_asym_id`),
            support: parseGroupSupport(row.support, `${label}.support`),
            all: parseDistribution(row.all, `${label}.all`), native: parseDistribution(row.native, `${label}.native`), non_native: parseDistribution(row.non_native, `${label}.non_native`),
        };
    });
    const perResidue = payload.per_residue.map((item, index) => {
        const label = `statistics.per_residue[${index}]`;
        const keysForRow = [...STATISTICS_RESIDUE_KEYS, 'native_score', 'native_class', 'all', 'non_native', 'alternative_class_burden'] as const;
        const row = fmClosedProjection(item, label, keysForRow, keysForRow);
        return {
            ...parseStatisticsResidueFields(row, label), native_score: fmFinite(row.native_score, `${label}.native_score`),
            native_class: parseStatisticsClass(row.native_class, `${label}.native_class`),
            all: parseDistribution(row.all, `${label}.all`), non_native: parseDistribution(row.non_native, `${label}.non_native`),
            alternative_class_burden: parseClassBurden(row.alternative_class_burden, `${label}.alternative_class_burden`),
        };
    });
    const perMutation = payload.per_mutation_amino_acid.map((item, index) => {
        const label = `statistics.per_mutation_amino_acid[${index}]`;
        const row = fmClosedProjection(item, label, ['mutation_aa', 'distribution', 'class_composition'], ['mutation_aa', 'distribution', 'class_composition']);
        const mutation = fmString(row.mutation_aa, `${label}.mutation_aa`);
        if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(mutation)) throw new Error(`${label}.mutation_aa is invalid`);
        return { mutation_aa: mutation, distribution: parseDistribution(row.distribution, `${label}.distribution`), class_composition: parseClassBurden(row.class_composition, `${label}.class_composition`) };
    });
    const regions = payload.contiguous_native_class_regions.map((item, index) => {
        const label = `statistics.contiguous_native_class_regions[${index}]`;
        const row = fmClosedProjection(item, label, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id', 'native_class', 'start', 'end', 'length'], ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id', 'native_class', 'start', 'end', 'length']);
        return {
            entity_instance_id: fmString(row.entity_instance_id, `${label}.entity_instance_id`), source_entity_id: fmSchemaNullableString(row.source_entity_id, `${label}.source_entity_id`),
            label_asym_id: fmSchemaNullableString(row.label_asym_id, `${label}.label_asym_id`), auth_asym_id: fmString(row.auth_asym_id, `${label}.auth_asym_id`),
            pdb_chain_id: fmString(row.pdb_chain_id, `${label}.pdb_chain_id`), native_class: parseStatisticsClass(row.native_class, `${label}.native_class`),
            start: parseStatisticsResidueIdentity(row.start, `${label}.start`), end: parseStatisticsResidueIdentity(row.end, `${label}.end`), length: fmInteger(row.length, `${label}.length`, 1),
        };
    });
    const ranked = fmClosedProjection(payload.ranked_non_native_alternatives, 'statistics.ranked_non_native_alternatives', ['support_count', 'omitted_count', 'best_to_worst', 'worst_to_best'], ['support_count', 'omitted_count', 'best_to_worst', 'worst_to_best']);
    if (!Array.isArray(ranked.best_to_worst) || !Array.isArray(ranked.worst_to_best)) throw new Error('statistics ranked alternatives must be arrays');
    return {
        schema_name: 'frustrampnn_statistics', schema_version: 1,
        hash_semantics: 'sha256(rfc8785(document_without_top_level_statistics_sha256))',
        invocation_id: fmString(payload.invocation_id, 'statistics.invocation_id'), parent_job_id: fmString(payload.parent_job_id, 'statistics.parent_job_id'),
        candidate_id: fmString(payload.candidate_id, 'statistics.candidate_id'), target_id: fmString(payload.target_id, 'statistics.target_id'),
        landscape_sha256: fmSha256(payload.landscape_sha256, 'statistics.landscape_sha256'), source_artifact_sha256: fmSha256(payload.source_artifact_sha256, 'statistics.source_artifact_sha256'),
        normalized_pdb_sha256: fmSha256(payload.normalized_pdb_sha256, 'statistics.normalized_pdb_sha256'), settings_sha256: fmSha256(payload.settings_sha256, 'statistics.settings_sha256'),
        effective_settings_sha256: fmSha256(payload.effective_settings_sha256, 'statistics.effective_settings_sha256'), capability_inventory_content_sha256: fmSha256(payload.capability_inventory_content_sha256, 'statistics.capability_inventory_content_sha256'),
        capability_inventory_byte_sha256: fmSha256(payload.capability_inventory_byte_sha256, 'statistics.capability_inventory_byte_sha256'), configuration_sha256: fmSha256(payload.configuration_sha256, 'statistics.configuration_sha256'),
        runtime_identity_sha256: fmSha256(payload.runtime_identity_sha256, 'statistics.runtime_identity_sha256'), classification_policy_sha256: fmSha256(payload.classification_policy_sha256, 'statistics.classification_policy_sha256'),
        execution_plan_sha256: fmSha256(payload.execution_plan_sha256, 'statistics.execution_plan_sha256'), comparison_compatibility_id: fmSha256(payload.comparison_compatibility_id, 'statistics.comparison_compatibility_id'),
        statistics_sha256: fmSha256(payload.statistics_sha256, 'statistics.statistics_sha256'), structure_map: { schema_name: 'frustrampnn_structure_map', schema_version: 1, sha256: fmSha256(structureMap.sha256, 'statistics.structure_map.sha256') },
        output_contract_version: '2.0', canonical_amino_acid_order: 'ACDEFGHIKLMNPQRSTVWY', comparison_compatibility_basis: parseStatisticsBasis(payload.comparison_compatibility_basis),
        support: parseStatisticsSupport(payload.support, 'statistics.support'),
        distributions: { overall: parseDistribution(distributions.overall, 'statistics.distributions.overall'), native: parseDistribution(distributions.native, 'statistics.distributions.native'), non_native: parseDistribution(distributions.non_native, 'statistics.distributions.non_native') },
        class_burden: { all: parseClassBurden(burdens.all, 'statistics.class_burden.all'), native: parseClassBurden(burdens.native, 'statistics.class_burden.native'), non_native: parseClassBurden(burdens.non_native, 'statistics.class_burden.non_native') },
        native_vs_alternative: {
            native_mean: fmNullableFinite(nativeAlternative.native_mean, 'statistics.native_vs_alternative.native_mean'), alternative_mean: fmNullableFinite(nativeAlternative.alternative_mean, 'statistics.native_vs_alternative.alternative_mean'),
            alternative_minus_native: parseDistribution(nativeAlternative.alternative_minus_native, 'statistics.native_vs_alternative.alternative_minus_native'),
            denominators: { native_mean: parseDenominator(nativeAlternativeDenominators.native_mean, 'statistics.native_vs_alternative.denominators.native_mean'), alternative_mean: parseDenominator(nativeAlternativeDenominators.alternative_mean, 'statistics.native_vs_alternative.denominators.alternative_mean') },
            missingness_reasons: { native_mean: fmSchemaNullableString(nativeAlternativeMissingness.native_mean, 'statistics.native_vs_alternative.missingness_reasons.native_mean'), alternative_mean: fmSchemaNullableString(nativeAlternativeMissingness.alternative_mean, 'statistics.native_vs_alternative.missingness_reasons.alternative_mean') },
        },
        per_chain: perChain, per_entity: perEntity, per_residue: perResidue, per_mutation_amino_acid: perMutation,
        contiguous_native_class_regions: regions,
        ranked_non_native_alternatives: {
            support_count: fmInteger(ranked.support_count, 'statistics.ranked_non_native_alternatives.support_count', 0),
            omitted_count: fmInteger(ranked.omitted_count, 'statistics.ranked_non_native_alternatives.omitted_count', 0),
            best_to_worst: ranked.best_to_worst.map((item, index) => parseRankedAlternative(item, index, 'best_to_worst')),
            worst_to_best: ranked.worst_to_best.map((item, index) => parseRankedAlternative(item, index, 'worst_to_best')),
        },
    };
};

const RESULT_ITEM_KEYS = [
    'invocation_id', 'parent_job_id', 'parent_workflow_id', 'candidate_id', 'design_id',
    'requiredness', 'source_artifact_id', 'source_artifact_sha256', 'request_sha256',
    'manifest_sha256', 'summary_sha256', 'created_at', 'authority_version', 'availability',
    'statistics_available', 'missing_fields', 'settings_sha256', 'effective_settings_sha256',
    'effective_settings_json', 'capability_inventory_sha256', 'statistics_sha256',
    'statistics_json', 'comparison_compatibility_id', 'status', 'component_contract_version',
    'runtime_identity', 'runtime_identity_sha256', 'gpu_provenance', 'failure_class', 'reopen_destination',
] as const;
const RESULT_DETAIL_KEYS = [...RESULT_ITEM_KEYS, 'summary', 'terminal_result', 'execution_receipt'] as const;
const MISSING_FIELDS = new Set<FrustraMpnnMissingField>([
    'settings_sha256', 'effective_settings_sha256', 'effective_settings_json',
    'capability_inventory_sha256', 'statistics_sha256', 'statistics_json',
    'comparison_compatibility_id',
]);

const parseResultItem = (value: unknown, detail: boolean): FrustraMpnnResultListItem => {
    const payload = fmClosedProjection(value, 'FrustraMPNN result', detail ? RESULT_DETAIL_KEYS : RESULT_ITEM_KEYS, detail ? RESULT_DETAIL_KEYS : RESULT_ITEM_KEYS);
    const authority = payload.authority_version;
    if (authority !== 'v2' && authority !== 'historical_v1') throw new Error('result authority_version is invalid');
    const status = payload.status;
    if (status !== 'succeeded' && status !== 'failed' && status !== 'not_run') throw new Error('result status is invalid');
    const contract = payload.component_contract_version;
    if (contract !== '1.0' && contract !== '2.0') throw new Error('result component contract is invalid');
    if (!Array.isArray(payload.missing_fields)) throw new Error('result missing_fields must be an array');
    const missingFields = payload.missing_fields.map((field) => {
        if (!MISSING_FIELDS.has(field as FrustraMpnnMissingField)) throw new Error(`result missing_fields contains unsupported field ${String(field)}`);
        return field as FrustraMpnnMissingField;
    });
    const statistics = payload.statistics_json === null ? null : parseFrustraMpnnStatistics(payload.statistics_json);
    const reopen = fmClosedProjection(payload.reopen_destination, 'result.reopen_destination', ['surface', 'params'], ['surface', 'params']);
    if (reopen.surface !== 'frustrampnn-workbench') throw new Error('result reopen surface is invalid');
    const reopenParams = fmClosedProjection(reopen.params, 'result.reopen_destination.params', ['job_id', 'invocation_id'], ['job_id', 'invocation_id']);
    return {
        invocation_id: fmString(payload.invocation_id, 'result.invocation_id'),
        parent_job_id: fmString(payload.parent_job_id, 'result.parent_job_id'),
        parent_workflow_id: fmString(payload.parent_workflow_id, 'result.parent_workflow_id'),
        candidate_id: fmString(payload.candidate_id, 'result.candidate_id'),
        design_id: fmNullableString(payload.design_id, 'result.design_id'),
        requiredness: fmString(payload.requiredness, 'result.requiredness'),
        source_artifact_id: fmNullableString(payload.source_artifact_id, 'result.source_artifact_id'),
        source_artifact_sha256: fmSha256(payload.source_artifact_sha256, 'result.source_artifact_sha256'),
        request_sha256: fmSha256(payload.request_sha256, 'result.request_sha256'),
        manifest_sha256: fmSha256(payload.manifest_sha256, 'result.manifest_sha256'),
        summary_sha256: fmSha256(payload.summary_sha256, 'result.summary_sha256'),
        created_at: fmString(payload.created_at, 'result.created_at'),
        authority_version: authority,
        availability: fmBoolean(payload.availability, 'result.availability'),
        statistics_available: fmBoolean(payload.statistics_available, 'result.statistics_available'),
        missing_fields: missingFields,
        settings_sha256: fmOptionalSha256(payload.settings_sha256, 'result.settings_sha256'),
        effective_settings_sha256: fmOptionalSha256(payload.effective_settings_sha256, 'result.effective_settings_sha256'),
        effective_settings_json: payload.effective_settings_json === null ? null : parseFrustraMpnnEffectiveSettingsProjection(payload.effective_settings_json),
        capability_inventory_sha256: fmOptionalSha256(payload.capability_inventory_sha256, 'result.capability_inventory_sha256'),
        statistics_sha256: fmOptionalSha256(payload.statistics_sha256, 'result.statistics_sha256'),
        statistics_json: statistics,
        comparison_compatibility_id: fmOptionalSha256(payload.comparison_compatibility_id, 'result.comparison_compatibility_id'),
        status,
        component_contract_version: contract,
        runtime_identity: fmRuntimeIdentityProjection(payload.runtime_identity),
        runtime_identity_sha256: fmOptionalSha256(payload.runtime_identity_sha256, 'result.runtime_identity_sha256'),
        gpu_provenance: payload.gpu_provenance === null ? null : fmGpuProvenanceProjection(payload.gpu_provenance),
        failure_class: payload.failure_class === null ? null : fmString(payload.failure_class, 'result.failure_class') as FrustraMpnnFailureClass,
        reopen_destination: {
            surface: 'frustrampnn-workbench',
            params: {
                job_id: fmString(reopenParams.job_id, 'result.reopen_destination.params.job_id'),
                invocation_id: fmString(reopenParams.invocation_id, 'result.reopen_destination.params.invocation_id'),
            },
        },
    };
};

const parseSummaryCounts = (value: unknown, label: string): FrustraMpnnClassCounts => {
    const payload = fmClosedProjection(value, label, ['high', 'neutral', 'minimal'], ['high', 'neutral', 'minimal']);
    return {
        high: fmInteger(payload.high, `${label}.high`, 0),
        neutral: fmInteger(payload.neutral, `${label}.neutral`, 0),
        minimal: fmInteger(payload.minimal, `${label}.minimal`, 0),
    };
};

const parseSummaryFractions = (value: unknown, label: string): FrustraMpnnClassFractions => {
    const payload = fmClosedProjection(value, label, ['high', 'neutral', 'minimal'], ['high', 'neutral', 'minimal']);
    const parsed = {
        high: fmFinite(payload.high, `${label}.high`),
        neutral: fmFinite(payload.neutral, `${label}.neutral`),
        minimal: fmFinite(payload.minimal, `${label}.minimal`),
    };
    if (Object.values(parsed).some((fraction) => fraction < 0 || fraction > 1)) throw new Error(`${label} contains an invalid fraction`);
    return parsed;
};

const parseFrustraMpnnSummary = (value: unknown): FrustraMpnnSummary => {
    const payload = fmRecord(value, 'result.summary');
    const commonKeys = [
        'schema_name', 'schema_version', 'target_id', 'parent_job_id', 'candidate_id', 'landscape_sha256',
        'residue_support', 'slot_support', 'missingness_by_reason', 'native_slot_counts',
        'native_slot_fractions', 'complete_landscape_counts', 'complete_landscape_fractions',
        'support_by_entity_chain', 'threshold_policy', 'threshold_policy_sha256',
    ] as const;
    const v1Keys = [...commonKeys, 'configuration_id', 'configuration_sha256'] as const;
    const v2Keys = [
        ...commonKeys, 'execution_configuration_id', 'execution_configuration_sha256',
        'requested_settings_sha256', 'effective_settings_sha256', 'runtime_identity_sha256',
        'source_artifact_sha256', 'structure_map_sha256', 'normalized_pdb_sha256', 'threshold_policy_id',
    ] as const;
    if (payload.schema_name !== 'frustrampnn_summary' || (payload.schema_version !== 1 && payload.schema_version !== 2)) {
        throw new Error('result summary schema identity is invalid');
    }
    fmClosedProjection(payload, 'result.summary', payload.schema_version === 1 ? v1Keys : v2Keys, payload.schema_version === 1 ? v1Keys : v2Keys);
    const residue = fmClosedProjection(payload.residue_support, 'result.summary.residue_support', ['expected', 'mapped', 'scoreable', 'excluded', 'ambiguous'], ['expected', 'mapped', 'scoreable', 'excluded', 'ambiguous']);
    const slots = fmClosedProjection(payload.slot_support, 'result.summary.slot_support', ['expected', 'observed', 'scoreable'], ['expected', 'observed', 'scoreable']);
    const missingnessWire = fmRecord(payload.missingness_by_reason, 'result.summary.missingness_by_reason');
    const missingness: Record<string, number> = {};
    for (const [reason, count] of Object.entries(missingnessWire)) {
        missingness[fmString(reason, 'result.summary missingness reason')] = fmInteger(count, `result.summary.missingness_by_reason.${reason}`, 1);
    }
    if (payload.schema_version === 2 && Object.keys(missingness).length !== 0) throw new Error('v2 result summary missingness must be empty');
    if (!Array.isArray(payload.support_by_entity_chain)) throw new Error('result.summary.support_by_entity_chain must be an array');
    if (payload.schema_version === 2 && payload.support_by_entity_chain.length < 1) throw new Error('result.summary.support_by_entity_chain must contain at least one chain');
    const support = payload.support_by_entity_chain.map((item, index) => {
        const label = `result.summary.support_by_entity_chain[${index}]`;
        const row = fmClosedProjection(item, label, ['entity_instance_id', 'auth_asym_id', 'expected_residues', 'mapped_residues', 'scoreable_residues', 'expected_slots', 'observed_slots', 'scoreable_slots'], ['entity_instance_id', 'auth_asym_id', 'expected_residues', 'mapped_residues', 'scoreable_residues', 'expected_slots', 'observed_slots', 'scoreable_slots']);
        return {
            entity_instance_id: fmString(row.entity_instance_id, `${label}.entity_instance_id`),
            auth_asym_id: fmString(row.auth_asym_id, `${label}.auth_asym_id`),
            expected_residues: fmInteger(row.expected_residues, `${label}.expected_residues`, payload.schema_version === 2 ? 1 : 0),
            mapped_residues: fmInteger(row.mapped_residues, `${label}.mapped_residues`, payload.schema_version === 2 ? 1 : 0),
            scoreable_residues: fmInteger(row.scoreable_residues, `${label}.scoreable_residues`, payload.schema_version === 2 ? 1 : 0),
            expected_slots: fmInteger(row.expected_slots, `${label}.expected_slots`, payload.schema_version === 2 ? 20 : 0),
            observed_slots: fmInteger(row.observed_slots, `${label}.observed_slots`, payload.schema_version === 2 ? 20 : 0),
            scoreable_slots: fmInteger(row.scoreable_slots, `${label}.scoreable_slots`, payload.schema_version === 2 ? 20 : 0),
        };
    });
    const policyLabel = 'result.summary.threshold_policy';
    const policy = payload.schema_version === 1
        ? fmClosedProjection(payload.threshold_policy, policyLabel, ['id', 'high_max', 'minimal_min'], ['id', 'high_max', 'minimal_min'])
        : fmClosedProjection(payload.threshold_policy, policyLabel, ['mode', 'high_max', 'minimal_min'], ['mode', 'high_max', 'minimal_min']);
    if (payload.schema_version === 1 && policy.id !== 'frustrampnn_class_v1') throw new Error('v1 result summary threshold policy is invalid');
    if (payload.schema_version === 1 && (fmFinite(policy.high_max, `${policyLabel}.high_max`) !== -1 || fmFinite(policy.minimal_min, `${policyLabel}.minimal_min`) !== 0.58)) throw new Error('v1 result summary threshold_policy values are invalid');
    if (payload.schema_version === 2 && policy.mode !== 'canonical' && policy.mode !== 'custom') throw new Error('v2 result summary threshold policy is invalid');
    const common = {
        schema_name: 'frustrampnn_summary' as const,
        schema_version: payload.schema_version,
        target_id: fmString(payload.target_id, 'result.summary.target_id'),
        parent_job_id: fmString(payload.parent_job_id, 'result.summary.parent_job_id'),
        candidate_id: fmString(payload.candidate_id, 'result.summary.candidate_id'),
        landscape_sha256: fmSha256(payload.landscape_sha256, 'result.summary.landscape_sha256'),
        residue_support: {
            expected: fmInteger(residue.expected, 'result.summary.residue_support.expected', payload.schema_version === 2 ? 1 : 0),
            mapped: fmInteger(residue.mapped, 'result.summary.residue_support.mapped', payload.schema_version === 2 ? 1 : 0),
            scoreable: fmInteger(residue.scoreable, 'result.summary.residue_support.scoreable', payload.schema_version === 2 ? 1 : 0),
            excluded: fmInteger(residue.excluded, 'result.summary.residue_support.excluded', 0),
            ambiguous: fmInteger(residue.ambiguous, 'result.summary.residue_support.ambiguous', 0),
        },
        slot_support: {
            expected: fmInteger(slots.expected, 'result.summary.slot_support.expected', payload.schema_version === 2 ? 20 : 0),
            observed: fmInteger(slots.observed, 'result.summary.slot_support.observed', payload.schema_version === 2 ? 20 : 0),
            scoreable: fmInteger(slots.scoreable, 'result.summary.slot_support.scoreable', payload.schema_version === 2 ? 20 : 0),
        },
        missingness_by_reason: missingness,
        native_slot_counts: parseSummaryCounts(payload.native_slot_counts, 'result.summary.native_slot_counts'),
        native_slot_fractions: parseSummaryFractions(payload.native_slot_fractions, 'result.summary.native_slot_fractions'),
        complete_landscape_counts: parseSummaryCounts(payload.complete_landscape_counts, 'result.summary.complete_landscape_counts'),
        complete_landscape_fractions: parseSummaryFractions(payload.complete_landscape_fractions, 'result.summary.complete_landscape_fractions'),
        support_by_entity_chain: support,
        threshold_policy: payload.schema_version === 1
            ? { id: 'frustrampnn_class_v1' as const, high_max: fmFinite(policy.high_max, `${policyLabel}.high_max`), minimal_min: fmFinite(policy.minimal_min, `${policyLabel}.minimal_min`) }
            : { mode: policy.mode as 'canonical' | 'custom', high_max: fmFinite(policy.high_max, `${policyLabel}.high_max`), minimal_min: fmFinite(policy.minimal_min, `${policyLabel}.minimal_min`) },
        threshold_policy_sha256: fmSha256(payload.threshold_policy_sha256, 'result.summary.threshold_policy_sha256'),
    };
    return payload.schema_version === 1 ? {
        ...common,
        schema_version: 1,
        threshold_policy: { id: 'frustrampnn_class_v1' as const, high_max: fmFinite(policy.high_max, `${policyLabel}.high_max`), minimal_min: fmFinite(policy.minimal_min, `${policyLabel}.minimal_min`) },
        configuration_id: (() => { if (payload.configuration_id !== 'frustrampnn_global_v1') throw new Error('v1 result summary configuration is invalid'); return 'frustrampnn_global_v1' as const; })(),
        configuration_sha256: fmSha256(payload.configuration_sha256, 'result.summary.configuration_sha256'),
    } : {
        ...common,
        schema_version: 2,
        threshold_policy: { mode: policy.mode as 'canonical' | 'custom', high_max: fmFinite(policy.high_max, `${policyLabel}.high_max`), minimal_min: fmFinite(policy.minimal_min, `${policyLabel}.minimal_min`) },
        execution_configuration_id: (() => { if (payload.execution_configuration_id !== 'frustrampnn_execution_configuration_v2') throw new Error('v2 result summary execution configuration is invalid'); return 'frustrampnn_execution_configuration_v2' as const; })(),
        execution_configuration_sha256: fmSha256(payload.execution_configuration_sha256, 'result.summary.execution_configuration_sha256'),
        requested_settings_sha256: fmSha256(payload.requested_settings_sha256, 'result.summary.requested_settings_sha256'),
        effective_settings_sha256: fmSha256(payload.effective_settings_sha256, 'result.summary.effective_settings_sha256'),
        runtime_identity_sha256: fmSha256(payload.runtime_identity_sha256, 'result.summary.runtime_identity_sha256'),
        source_artifact_sha256: fmSha256(payload.source_artifact_sha256, 'result.summary.source_artifact_sha256'),
        structure_map_sha256: fmSha256(payload.structure_map_sha256, 'result.summary.structure_map_sha256'),
        normalized_pdb_sha256: fmSha256(payload.normalized_pdb_sha256, 'result.summary.normalized_pdb_sha256'),
        threshold_policy_id: (() => { if (payload.threshold_policy_id !== 'frustrampnn_class_v1') throw new Error('v2 result summary threshold policy id is invalid'); return 'frustrampnn_class_v1' as const; })(),
    };
};

const parseTerminalResultProjection = (value: unknown): FrustraMpnnTerminalResult => {
    const keys = ['schema_name', 'schema_version', 'request_sha256', 'invocation_id', 'component_id', 'component_contract_version', 'candidate_id', 'parent_job_id', 'parent_workflow_id', 'status', 'failure_class', 'source_artifact', 'runtime_identity', 'artifacts', 'result_payload', 'started_at', 'ended_at', 'duration_seconds', 'gpu_provenance'] as const;
    const payload = fmClosedProjection(value, 'result.terminal_result', keys, keys);
    const status = payload.status;
    if (status !== null && status !== 'succeeded' && status !== 'failed' && status !== 'not_run') throw new Error('terminal result status is invalid');
    if (!Array.isArray(payload.artifacts)) throw new Error('terminal result artifacts must be an array');
    const artifacts = payload.artifacts.map((item, index): FrustraMpnnTerminalArtifact => {
        const label = `result.terminal_result.artifacts[${index}]`;
        const artifact = fmClosedProjection(item, label, ['role', 'schema_name', 'schema_version', 'sha256', 'bytes', 'cardinality'], ['role', 'schema_name', 'schema_version', 'sha256', 'bytes', 'cardinality']);
        const cardinality = artifact.cardinality === null ? null : fmClosedProjection(artifact.cardinality, `${label}.cardinality`, ['kind', 'count'], ['kind', 'count']);
        return {
            role: fmNullableString(artifact.role, `${label}.role`),
            schema_name: fmNullableString(artifact.schema_name, `${label}.schema_name`),
            schema_version: fmOptionalInteger(artifact.schema_version, `${label}.schema_version`, 1),
            sha256: fmSha256(artifact.sha256, `${label}.sha256`),
            bytes: fmInteger(artifact.bytes, `${label}.bytes`, 0),
            cardinality: cardinality === null ? null : {
                kind: fmString(cardinality.kind, `${label}.cardinality.kind`) as FrustraMpnnTerminalArtifact['cardinality'] extends { kind: infer K } ? K : never,
                count: fmInteger(cardinality.count, `${label}.cardinality.count`, 0),
            },
        };
    });
    const source = payload.source_artifact === null ? null : fmClosedProjection(payload.source_artifact, 'result.terminal_result.source_artifact', ['artifact_id', 'sha256', 'media_type', 'producer_stage'], ['artifact_id', 'sha256', 'media_type', 'producer_stage']);
    const resultPayload = payload.result_payload === null ? null : fmClosedProjection(payload.result_payload, 'result.terminal_result.result_payload', ['schema_name', 'schema_version', 'sha256'], ['schema_name', 'schema_version', 'sha256']);
    return {
        schema_name: fmNullableString(payload.schema_name, 'result.terminal_result.schema_name'),
        schema_version: fmOptionalInteger(payload.schema_version, 'result.terminal_result.schema_version', 1),
        request_sha256: fmOptionalSha256(payload.request_sha256, 'result.terminal_result.request_sha256'),
        invocation_id: fmNullableString(payload.invocation_id, 'result.terminal_result.invocation_id'),
        component_id: fmNullableString(payload.component_id, 'result.terminal_result.component_id'),
        component_contract_version: fmNullableString(payload.component_contract_version, 'result.terminal_result.component_contract_version'),
        candidate_id: fmNullableString(payload.candidate_id, 'result.terminal_result.candidate_id'),
        parent_job_id: fmNullableString(payload.parent_job_id, 'result.terminal_result.parent_job_id'),
        parent_workflow_id: fmNullableString(payload.parent_workflow_id, 'result.terminal_result.parent_workflow_id'),
        status,
        failure_class: fmNullableString(payload.failure_class, 'result.terminal_result.failure_class'),
        source_artifact: source === null ? null : {
            artifact_id: fmNullableString(source.artifact_id, 'result.terminal_result.source_artifact.artifact_id'),
            sha256: fmOptionalSha256(source.sha256, 'result.terminal_result.source_artifact.sha256'),
            media_type: fmNullableString(source.media_type, 'result.terminal_result.source_artifact.media_type'),
            producer_stage: fmNullableString(source.producer_stage, 'result.terminal_result.source_artifact.producer_stage'),
        },
        runtime_identity: payload.runtime_identity === null ? null : fmRuntimeIdentityProjection(payload.runtime_identity),
        artifacts,
        result_payload: resultPayload === null ? null : {
            schema_name: fmNullableString(resultPayload.schema_name, 'result.terminal_result.result_payload.schema_name'),
            schema_version: fmOptionalInteger(resultPayload.schema_version, 'result.terminal_result.result_payload.schema_version', 1),
            sha256: fmOptionalSha256(resultPayload.sha256, 'result.terminal_result.result_payload.sha256'),
        },
        started_at: fmOptionalString(payload.started_at, 'result.terminal_result.started_at'),
        ended_at: fmOptionalString(payload.ended_at, 'result.terminal_result.ended_at'),
        duration_seconds: payload.duration_seconds === null ? null : fmFinite(payload.duration_seconds, 'result.terminal_result.duration_seconds'),
        gpu_provenance: payload.gpu_provenance === null ? null : fmGpuProvenanceProjection(payload.gpu_provenance, 'result.terminal_result.gpu_provenance'),
    };
};

const parseExecutionReceiptProjection = (value: unknown): FrustraMpnnExecutionReceiptProjection => {
    const required = ['schema_name', 'schema_version', 'invocation_id', 'execution_configuration_sha256', 'requested_settings_sha256', 'effective_settings_sha256', 'runtime_identity_sha256', 'source_artifact_sha256', 'structure_map_sha256', 'normalized_pdb_sha256', 'command_count', 'gpu_provenance', 'started_at', 'ended_at', 'duration_seconds'] as const;
    const optional = ['merged_raw_csv_sha256', 'landscape_sha256', 'summary_sha256'] as const;
    const payload = fmClosedProjection(value, 'execution_receipt', [...required, ...optional], required);
    return {
        schema_name: fmNullableString(payload.schema_name, 'execution_receipt.schema_name'),
        schema_version: fmOptionalInteger(payload.schema_version, 'execution_receipt.schema_version', 1),
        invocation_id: fmString(payload.invocation_id, 'execution_receipt.invocation_id'),
        execution_configuration_sha256: fmOptionalSha256(payload.execution_configuration_sha256, 'execution_receipt.execution_configuration_sha256'),
        requested_settings_sha256: fmOptionalSha256(payload.requested_settings_sha256, 'execution_receipt.requested_settings_sha256'),
        effective_settings_sha256: fmOptionalSha256(payload.effective_settings_sha256, 'execution_receipt.effective_settings_sha256'),
        runtime_identity_sha256: fmOptionalSha256(payload.runtime_identity_sha256, 'execution_receipt.runtime_identity_sha256'),
        source_artifact_sha256: fmOptionalSha256(payload.source_artifact_sha256, 'execution_receipt.source_artifact_sha256'),
        structure_map_sha256: fmOptionalSha256(payload.structure_map_sha256, 'execution_receipt.structure_map_sha256'),
        normalized_pdb_sha256: fmOptionalSha256(payload.normalized_pdb_sha256, 'execution_receipt.normalized_pdb_sha256'),
        command_count: fmOptionalInteger(payload.command_count, 'execution_receipt.command_count'),
        ...(payload.merged_raw_csv_sha256 !== undefined ? { merged_raw_csv_sha256: fmSha256(payload.merged_raw_csv_sha256, 'execution_receipt.merged_raw_csv_sha256') } : {}),
        ...(payload.landscape_sha256 !== undefined ? { landscape_sha256: fmSha256(payload.landscape_sha256, 'execution_receipt.landscape_sha256') } : {}),
        ...(payload.summary_sha256 !== undefined ? { summary_sha256: fmSha256(payload.summary_sha256, 'execution_receipt.summary_sha256') } : {}),
        gpu_provenance: payload.gpu_provenance === null ? null : fmGpuProvenanceProjection(payload.gpu_provenance, 'execution_receipt.gpu_provenance'),
        started_at: fmOptionalString(payload.started_at, 'execution_receipt.started_at'),
        ended_at: fmOptionalString(payload.ended_at, 'execution_receipt.ended_at'),
        duration_seconds: payload.duration_seconds === null ? null : fmFinite(payload.duration_seconds, 'execution_receipt.duration_seconds'),
    };
};

export const parseFrustraMpnnResultDetail = (value: unknown): FrustraMpnnResultDetail => {
    const payload = fmRecord(value, 'FrustraMPNN result detail');
    const common = parseResultItem(payload, true);
    return {
        ...common,
        summary: parseFrustraMpnnSummary(payload.summary),
        terminal_result: parseTerminalResultProjection(payload.terminal_result),
        execution_receipt: payload.execution_receipt === null ? null : parseExecutionReceiptProjection(payload.execution_receipt),
    };
};

export const parseFrustraMpnnResultList = (value: unknown): FrustraMpnnResultList => {
    const payload = fmClosedProjection(value, 'FrustraMPNN result list', ['items', 'total', 'limit', 'offset'], ['items', 'total', 'limit', 'offset']);
    if (!Array.isArray(payload.items)) throw new Error('result list items must be an array');
    return {
        items: payload.items.map((item) => parseResultItem(item, false)),
        total: fmInteger(payload.total, 'result list total', 0),
        limit: fmInteger(payload.limit, 'result list limit', 1),
        offset: fmInteger(payload.offset, 'result list offset', 0),
    };
};

const STATISTICS_RESPONSE_KEYS = [
    'result_id', 'parent_job_id', 'candidate_id', 'invocation_id', 'authority_version',
    'availability', 'missing_fields', 'settings_sha256', 'effective_settings_sha256',
    'effective_settings_json', 'capability_inventory_sha256', 'statistics_sha256',
    'statistics_json', 'comparison_compatibility_id', 'statistics',
] as const;

export const parseFrustraMpnnStatisticsResponse = (value: unknown): FrustraMpnnStatisticsResponse => {
    const payload = fmClosedProjection(value, 'FrustraMPNN statistics response', STATISTICS_RESPONSE_KEYS, STATISTICS_RESPONSE_KEYS);
    const authority = payload.authority_version;
    if (authority !== 'v2' && authority !== 'historical_v1') throw new Error('statistics authority_version is invalid');
    if (!Array.isArray(payload.missing_fields)) throw new Error('statistics missing_fields must be an array');
    const missingFields = payload.missing_fields.map((field) => {
        if (!MISSING_FIELDS.has(field as FrustraMpnnMissingField)) throw new Error(`statistics missing_fields contains unsupported field ${String(field)}`);
        return field as FrustraMpnnMissingField;
    });
    return {
        result_id: fmString(payload.result_id, 'statistics result_id'),
        parent_job_id: fmString(payload.parent_job_id, 'statistics parent_job_id'),
        candidate_id: fmString(payload.candidate_id, 'statistics candidate_id'),
        invocation_id: fmString(payload.invocation_id, 'statistics invocation_id'),
        authority_version: authority,
        availability: fmBoolean(payload.availability, 'statistics availability'),
        missing_fields: missingFields,
        settings_sha256: fmOptionalSha256(payload.settings_sha256, 'statistics settings_sha256'),
        effective_settings_sha256: fmOptionalSha256(payload.effective_settings_sha256, 'statistics effective_settings_sha256'),
        effective_settings_json: payload.effective_settings_json === null ? null : parseFrustraMpnnEffectiveSettingsProjection(payload.effective_settings_json),
        capability_inventory_sha256: fmOptionalSha256(payload.capability_inventory_sha256, 'statistics capability_inventory_sha256'),
        statistics_sha256: fmOptionalSha256(payload.statistics_sha256, 'statistics statistics_sha256'),
        statistics_json: payload.statistics_json === null ? null : parseFrustraMpnnStatistics(payload.statistics_json),
        comparison_compatibility_id: fmOptionalSha256(payload.comparison_compatibility_id, 'statistics comparison_compatibility_id'),
        statistics: payload.statistics === null ? null : parseFrustraMpnnStatistics(payload.statistics),
    };
};

const STATISTICS_QUERY_ROW_KEYS = [
    'dataset', 'level', 'key', 'availability', 'unavailable_reason', 'distribution',
    'native_distribution', 'non_native_distribution', 'class_burden', 'native_score',
    'native_class', 'support',
] as const;

const parseStatisticsQueryKey = (
    value: unknown,
    level: FrustraMpnnStatisticsQueryLevel,
    label: string,
): Record<string, string | number | null> | null => {
    if (value === null) {
        if (level === 'overview') throw new Error(`${label} must be a non-null empty object for overview rows`);
        return null;
    }
    const keysByLevel: Record<FrustraMpnnStatisticsQueryLevel, readonly string[]> = {
        overview: [],
        residue: ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt', 'pdb_chain_id', 'model_position'],
        mutation_aa: ['mutation_aa'],
        chain: ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id'],
        entity: ['entity_instance_id', 'source_entity_id', 'label_asym_id'],
    };
    const keys = keysByLevel[level];
    const payload = fmClosedProjection(value, label, keys, keys);
    const result: Record<string, string | number | null> = {};
    for (const key of keys) {
        const item = payload[key];
        result[key] = typeof item === 'number'
            ? fmInteger(item, `${label}.${key}`)
            : item === null
                ? null
                : fmString(item, `${label}.${key}`, key === 'insertion_code');
    }
    if (level === 'mutation_aa' && !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(String(result.mutation_aa))) {
        throw new Error(`${label}.mutation_aa is invalid`);
    }
    return result;
};

const parseStatisticsQuerySupport = (
    value: unknown,
    level: FrustraMpnnStatisticsQueryLevel,
    label: string,
): FrustraMpnnStatisticsSupport | FrustraMpnnGroupSupport | null => {
    if (level === 'residue' || level === 'mutation_aa') {
        if (value !== null) throw new Error(`${label} must be null for ${level} rows`);
        return null;
    }
    if (value === null) return null;
    return level === 'overview' ? parseStatisticsSupport(value, label) : parseGroupSupport(value, label);
};

export const parseFrustraMpnnStatisticsQueryResponse = (value: unknown): FrustraMpnnStatisticsQueryResponse => {
    const payload = fmClosedProjection(value, 'FrustraMPNN statistics query response', ['items', 'total', 'limit', 'offset', 'next_offset'], ['items', 'total', 'limit', 'offset', 'next_offset']);
    if (!Array.isArray(payload.items)) throw new Error('statistics query items must be an array');
    const items = payload.items.map((item, index): FrustraMpnnStatisticsQueryRow => {
        const label = `statistics query items[${index}]`;
        const row = fmClosedProjection(item, label, STATISTICS_QUERY_ROW_KEYS, STATISTICS_QUERY_ROW_KEYS);
        if (!['overview', 'residue', 'mutation_aa', 'chain', 'entity'].includes(String(row.level))) {
            throw new Error(`${label}.level is invalid`);
        }
        const level = row.level as FrustraMpnnStatisticsQueryLevel;
        const common = {
            dataset: parseResultReference(row.dataset, `${label}.dataset`),
            availability: fmBoolean(row.availability, `${label}.availability`),
            unavailable_reason: fmNullableString(row.unavailable_reason, `${label}.unavailable_reason`),
            distribution: row.distribution === null ? null : parseDistribution(row.distribution, `${label}.distribution`),
            native_distribution: row.native_distribution === null ? null : parseDistribution(row.native_distribution, `${label}.native_distribution`),
            non_native_distribution: row.non_native_distribution === null ? null : parseDistribution(row.non_native_distribution, `${label}.non_native_distribution`),
            class_burden: row.class_burden === null ? null : parseClassBurden(row.class_burden, `${label}.class_burden`),
            native_score: fmNullableFinite(row.native_score, `${label}.native_score`),
            native_class: requireCanonicalClass(row.native_class),
        };
        const key = parseStatisticsQueryKey(row.key, level, `${label}.key`);
        const support = parseStatisticsQuerySupport(row.support, level, `${label}.support`);
        if (level === 'overview') return { ...common, level, key: key as Record<string, never>, support: support as FrustraMpnnStatisticsSupport | null };
        if (level === 'residue') return { ...common, level, key: key as unknown as FrustraMpnnStatisticsResidueKey | null, support: null };
        if (level === 'mutation_aa') return { ...common, level, key: key as unknown as FrustraMpnnStatisticsMutationAAKey | null, support: null };
        if (level === 'chain') return { ...common, level, key: key as unknown as FrustraMpnnStatisticsChainKey | null, support: support as FrustraMpnnGroupSupport | null };
        return { ...common, level: 'entity', key: key as unknown as FrustraMpnnStatisticsEntityKey | null, support: support as FrustraMpnnGroupSupport | null };
    });
    const total = fmInteger(payload.total, 'statistics query total', 0);
    const limit = fmInteger(payload.limit, 'statistics query limit', 1);
    const offset = fmInteger(payload.offset, 'statistics query offset', 0);
    const nextOffset = payload.next_offset === null ? null : fmInteger(payload.next_offset, 'statistics query next_offset', 0);
    if (limit > 500 || items.length > limit || offset + items.length > total) throw new Error('statistics query pagination is invalid');
    const expectedNextOffset = offset + items.length < total ? offset + items.length : null;
    if (nextOffset !== expectedNextOffset) throw new Error('statistics query next_offset pagination is inconsistent');
    return {
        items,
        total,
        limit,
        offset,
        next_offset: nextOffset,
    };
};

const parseComparisonDifference = (value: unknown, label: string): FrustraMpnnComparisonDifference => {
    const payload = fmClosedProjection(value, label, ['field_path', 'left', 'right'], ['field_path', 'left', 'right']);
    return {
        field_path: fmString(payload.field_path, `${label}.field_path`),
        left: payload.left,
        right: payload.right,
    };
};

const parseComparisonDifferences = (value: unknown, label: string): FrustraMpnnComparisonDifference[] => {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
    return value.map((item, index) => parseComparisonDifference(item, `${label}[${index}]`));
};

const parseComparisonIdentity = (value: unknown, label: string): FrustraMpnnComparisonIdentity => {
    const keys = ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const wt = fmString(payload.wt, `${label}.wt`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt)) throw new Error(`${label}.wt is invalid`);
    return {
        entity_instance_id: fmString(payload.entity_instance_id, `${label}.entity_instance_id`),
        source_entity_id: fmString(payload.source_entity_id, `${label}.source_entity_id`),
        label_asym_id: fmString(payload.label_asym_id, `${label}.label_asym_id`),
        auth_asym_id: fmString(payload.auth_asym_id, `${label}.auth_asym_id`),
        auth_seq_id: fmInteger(payload.auth_seq_id, `${label}.auth_seq_id`),
        insertion_code: fmString(payload.insertion_code, `${label}.insertion_code`, true),
        sequence_index: fmInteger(payload.sequence_index, `${label}.sequence_index`, 1),
        wt,
    };
};

const parseCompatibilityDomains = (value: unknown, label: string): FrustraMpnnCompatibilityDomains => {
    const payload = fmClosedProjection(value, label, ['raw_score', 'classification', 'identity_alignment'], ['raw_score', 'classification', 'identity_alignment']);
    const raw = fmClosedProjection(payload.raw_score, `${label}.raw_score`, ['status', 'reasons', 'differences'], ['status', 'reasons', 'differences']);
    if (raw.status !== 'compatible' && raw.status !== 'hard_incompatible' && raw.status !== 'unknown') throw new Error(`${label}.raw_score.status is invalid`);
    const classification = fmClosedProjection(payload.classification, `${label}.classification`, ['status', 'reasons', 'differences'], ['status', 'reasons', 'differences']);
    if (classification.status !== 'compatible' && classification.status !== 'policy_different' && classification.status !== 'unknown') throw new Error(`${label}.classification.status is invalid`);
    const alignment = fmClosedProjection(payload.identity_alignment, `${label}.identity_alignment`, ['status', 'reasons', 'differences', 'reference_identity_count', 'target_identity_count', 'aligned_identity_count'], ['status', 'reasons', 'differences', 'reference_identity_count', 'target_identity_count', 'aligned_identity_count']);
    if (alignment.status !== 'exact' && alignment.status !== 'partial' && alignment.status !== 'none') throw new Error(`${label}.identity_alignment.status is invalid`);
    if (!Array.isArray(alignment.differences)) throw new Error(`${label}.identity_alignment.differences must be an array`);
    return {
        raw_score: {
            status: raw.status,
            reasons: fmStringArray(raw.reasons, `${label}.raw_score.reasons`),
            differences: parseComparisonDifferences(raw.differences, `${label}.raw_score.differences`),
        },
        classification: {
            status: classification.status,
            reasons: fmStringArray(classification.reasons, `${label}.classification.reasons`),
            differences: parseComparisonDifferences(classification.differences, `${label}.classification.differences`),
        },
        identity_alignment: {
            status: alignment.status,
            reasons: fmStringArray(alignment.reasons, `${label}.identity_alignment.reasons`),
            differences: alignment.differences.map((item, index) => {
                const differenceLabel = `${label}.identity_alignment.differences[${index}]`;
                const difference = fmClosedProjection(item, differenceLabel, ['side', 'identity'], ['side', 'identity']);
                if (difference.side !== 'reference_only' && difference.side !== 'target_only') throw new Error(`${differenceLabel}.side is invalid`);
                return { side: difference.side, identity: parseComparisonIdentity(difference.identity, `${differenceLabel}.identity`) };
            }),
            reference_identity_count: fmInteger(alignment.reference_identity_count, `${label}.identity_alignment.reference_identity_count`, 0),
            target_identity_count: fmInteger(alignment.target_identity_count, `${label}.identity_alignment.target_identity_count`, 0),
            aligned_identity_count: fmInteger(alignment.aligned_identity_count, `${label}.identity_alignment.aligned_identity_count`, 0),
        },
    };
};

const parseCompatibilityMetadata = (value: unknown, label: string): FrustraMpnnCompatibilityMetadata => {
    const source = fmRecord(value, label);
    const payload = fmClosedProjection({
        compatibility_status: source.compatibility_status,
        left_comparison_compatibility_id: source.left_comparison_compatibility_id,
        right_comparison_compatibility_id: source.right_comparison_compatibility_id,
        override_used: source.override_used,
        compatibility_differences: source.compatibility_differences,
    }, label, ['compatibility_status', 'left_comparison_compatibility_id', 'right_comparison_compatibility_id', 'override_used', 'compatibility_differences'], ['compatibility_status', 'left_comparison_compatibility_id', 'right_comparison_compatibility_id', 'override_used', 'compatibility_differences']);
    if (payload.compatibility_status !== 'compatible' && payload.compatibility_status !== 'incompatible' && payload.compatibility_status !== 'unknown') throw new Error(`${label}.compatibility_status is invalid`);
    return {
        compatibility_status: payload.compatibility_status,
        left_comparison_compatibility_id: fmOptionalSha256(payload.left_comparison_compatibility_id, `${label}.left_comparison_compatibility_id`),
        right_comparison_compatibility_id: fmOptionalSha256(payload.right_comparison_compatibility_id, `${label}.right_comparison_compatibility_id`),
        override_used: fmBoolean(payload.override_used, `${label}.override_used`),
        compatibility_differences: parseComparisonDifferences(payload.compatibility_differences, `${label}.compatibility_differences`),
    };
};

const parsePairCompatibility = (value: unknown, label: string): FrustraMpnnPairCompatibility => {
    const keys = ['target_label', 'target_id', 'target_landscape_sha256', 'target_configuration_sha256', 'compatibility_status', 'left_comparison_compatibility_id', 'right_comparison_compatibility_id', 'override_used', 'compatibility_differences', 'compatibility_domains'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const targetLabel = fmString(payload.target_label, `${label}.target_label`);
    if (!/^target-[0-9]{4}$/.test(targetLabel)) throw new Error(`${label}.target_label is invalid`);
    return {
        target_label: targetLabel,
        target_id: fmNullableString(payload.target_id, `${label}.target_id`),
        target_landscape_sha256: fmSha256(payload.target_landscape_sha256, `${label}.target_landscape_sha256`),
        target_configuration_sha256: fmOptionalSha256(payload.target_configuration_sha256, `${label}.target_configuration_sha256`),
        ...parseCompatibilityMetadata(payload, label),
        compatibility_domains: parseCompatibilityDomains(payload.compatibility_domains, `${label}.compatibility_domains`),
    };
};

const parsePairCompatibilityArray = (value: unknown, label: string): FrustraMpnnPairCompatibility[] => {
    if (!Array.isArray(value) || value.length < 1) throw new Error(`${label} must be a non-empty array`);
    return value.map((item, index) => parsePairCompatibility(item, `${label}[${index}]`));
};

const parseComparisonSide = (value: unknown, label: string): FrustraMpnnComparisonSide => {
    const payload = fmClosedProjection(value, label, ['sequence_index', 'auth_seq_id', 'score', 'class', 'scoreable', 'status'], ['sequence_index', 'auth_seq_id', 'score', 'class', 'scoreable', 'status']);
    return {
        sequence_index: payload.sequence_index === null ? null : fmInteger(payload.sequence_index, `${label}.sequence_index`, 1),
        auth_seq_id: payload.auth_seq_id === null ? null : fmInteger(payload.auth_seq_id, `${label}.auth_seq_id`),
        score: fmNullableFinite(payload.score, `${label}.score`),
        class: requireCanonicalClass(payload.class),
        scoreable: fmBoolean(payload.scoreable, `${label}.scoreable`),
        status: fmString(payload.status, `${label}.status`),
    };
};

const parseComparisonResidueKey = (value: unknown, label: string): FrustraMpnnComparisonRow['residue_key'] => {
    const payload = fmClosedProjection(value, label, ['entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code'], ['entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code']);
    return {
        entity_instance_id: fmString(payload.entity_instance_id, `${label}.entity_instance_id`),
        auth_asym_id: fmString(payload.auth_asym_id, `${label}.auth_asym_id`),
        auth_seq_id: fmInteger(payload.auth_seq_id, `${label}.auth_seq_id`),
        insertion_code: fmString(payload.insertion_code, `${label}.insertion_code`, true),
    };
};

const parsePairComparisonRow = (value: unknown, index: number): FrustraMpnnComparisonRow => {
    const label = `comparison.rows[${index}]`;
    const keys = ['residue_key', 'sequence_index', 'mutation_aa', 'wt', 'mapping_state', 'missingness_state', 'biological_status', 'reference', 'target', 'raw_score_delta', 'classification_transition'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const missingnessStates = ['none', 'reference_unmapped', 'target_unmapped', 'both_unmapped', 'reference_missing', 'target_missing', 'both_missing'];
    const biologicalStatuses = ['biologically_scored', 'incompatible', 'unmapped', 'missing'];
    if ((payload.mapping_state !== 'mapped' && payload.mapping_state !== 'unmapped')
        || !missingnessStates.includes(String(payload.missingness_state))
        || !biologicalStatuses.includes(String(payload.biological_status))) throw new Error(`${label} state is invalid`);
    const mutation = fmString(payload.mutation_aa, `${label}.mutation_aa`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(mutation)) throw new Error(`${label}.mutation_aa is invalid`);
    return {
        residue_key: parseComparisonResidueKey(payload.residue_key, `${label}.residue_key`),
        sequence_index: payload.sequence_index === null ? null : fmInteger(payload.sequence_index, `${label}.sequence_index`, 1),
        mutation_aa: mutation,
        wt: fmNullableString(payload.wt, `${label}.wt`),
        mapping_state: payload.mapping_state,
        missingness_state: String(payload.missingness_state),
        biological_status: String(payload.biological_status),
        reference: parseComparisonSide(payload.reference, `${label}.reference`),
        target: parseComparisonSide(payload.target, `${label}.target`),
        raw_score_delta: fmNullableFinite(payload.raw_score_delta, `${label}.raw_score_delta`),
        classification_transition: fmNullableString(payload.classification_transition, `${label}.classification_transition`),
    };
};

const parseMultiComparisonRow = (value: unknown, index: number): FrustraMpnnMultiComparisonRow => {
    const label = `comparison.rows[${index}]`;
    const keys = ['residue_key', 'sequence_index', 'mutation_aa', 'mapping_state', 'missingness_state', 'missingness_by_target', 'biological_status', 'reference', 'targets', 'raw_score_deltas', 'classification_transitions'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    const biologicalStatuses = ['biologically_scored', 'partially_scored', 'incompatible', 'missing', 'unmapped'];
    if ((payload.mapping_state !== 'mapped' && payload.mapping_state !== 'unmapped')
        || (payload.missingness_state !== 'none' && payload.missingness_state !== 'per_target')
        || !biologicalStatuses.includes(String(payload.biological_status))) throw new Error(`${label} state is invalid`);
    if (!Array.isArray(payload.targets) || !Array.isArray(payload.raw_score_deltas) || !Array.isArray(payload.classification_transitions)) throw new Error(`${label} target arrays are invalid`);
    const missingnessByTarget = fmStringArray(payload.missingness_by_target, `${label}.missingness_by_target`);
    const targetCount = payload.targets.length;
    if (targetCount < 1 || targetCount > FRUSTRAMPNN_MULTI_TARGET_LIMIT
        || missingnessByTarget.length !== targetCount || payload.raw_score_deltas.length !== targetCount
        || payload.classification_transitions.length !== targetCount) throw new Error(`${label} target arrays are inconsistent`);
    const mutation = fmString(payload.mutation_aa, `${label}.mutation_aa`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(mutation)) throw new Error(`${label}.mutation_aa is invalid`);
    return {
        residue_key: parseComparisonResidueKey(payload.residue_key, `${label}.residue_key`),
        sequence_index: payload.sequence_index === null ? null : fmInteger(payload.sequence_index, `${label}.sequence_index`, 1),
        mutation_aa: mutation,
        mapping_state: payload.mapping_state,
        missingness_state: String(payload.missingness_state),
        missingness_by_target: missingnessByTarget,
        biological_status: String(payload.biological_status),
        reference: payload.reference === null ? null : parseComparisonSide(payload.reference, `${label}.reference`),
        targets: payload.targets.map((item, targetIndex) => item === null ? null : parseComparisonSide(item, `${label}.targets[${targetIndex}]`)),
        raw_score_deltas: payload.raw_score_deltas.map((item, targetIndex) => fmNullableFinite(item, `${label}.raw_score_deltas[${targetIndex}]`)),
        classification_transitions: payload.classification_transitions.map((item, targetIndex) => fmNullableString(item, `${label}.classification_transitions[${targetIndex}]`)),
    };
};

const parseResultReference = (value: unknown, label: string): FrustraMpnnResultReference => {
    const payload = fmClosedProjection(value, label, ['parent_job_id', 'invocation_id'], ['parent_job_id', 'invocation_id']);
    return { parent_job_id: fmString(payload.parent_job_id, `${label}.parent_job_id`), invocation_id: fmString(payload.invocation_id, `${label}.invocation_id`) };
};

const parseSourceResultReference = (value: unknown, label: string): FrustraMpnnSourceResultReference => {
    const payload = fmClosedProjection(value, label, ['role', 'target_label', 'parent_job_id', 'invocation_id', 'landscape_sha256', 'configuration_sha256'], ['role', 'target_label', 'parent_job_id', 'invocation_id', 'landscape_sha256', 'configuration_sha256']);
    if (payload.role !== 'reference' && payload.role !== 'target') throw new Error(`${label}.role is invalid`);
    return {
        role: payload.role,
        target_label: fmNullableString(payload.target_label, `${label}.target_label`),
        parent_job_id: fmString(payload.parent_job_id, `${label}.parent_job_id`),
        invocation_id: fmString(payload.invocation_id, `${label}.invocation_id`),
        landscape_sha256: fmSha256(payload.landscape_sha256, `${label}.landscape_sha256`),
        configuration_sha256: fmOptionalSha256(payload.configuration_sha256, `${label}.configuration_sha256`),
    };
};

const parsePairComparisonSummary = (value: unknown): FrustraMpnnPairComparisonSummary => {
    const keys = ['total_rows', 'biologically_scored', 'incompatible', 'unmapped', 'missing_reference', 'missing_target', 'missing_both', 'transitions'] as const;
    const payload = fmClosedProjection(value, 'comparison.summary', keys, keys);
    return Object.fromEntries(keys.map((key) => [key, fmInteger(payload[key], `comparison.summary.${key}`, 0)])) as unknown as FrustraMpnnPairComparisonSummary;
};

const parseMultiComparisonSummary = (value: unknown): FrustraMpnnMultiComparisonSummary => {
    const keys = ['target_count', 'total_rows', 'biologically_scored', 'partially_scored', 'missing', 'unmapped', 'incompatible', 'transitions'] as const;
    const payload = fmClosedProjection(value, 'comparison.summary', keys, keys);
    const summary = Object.fromEntries(keys.map((key) => [key, fmInteger(payload[key], `comparison.summary.${key}`, key === 'target_count' ? 1 : 0)])) as unknown as FrustraMpnnMultiComparisonSummary;
    if (summary.target_count > FRUSTRAMPNN_MULTI_TARGET_LIMIT) throw new Error('comparison.summary.target_count exceeds the supported maximum');
    return summary;
};

const PAIR_COMPARISON_KEYS = [
    'schema_name', 'schema_version', 'comparison_id', 'comparison_sha256', 'reference_landscape_sha256',
    'target_landscape_sha256', 'configuration_id', 'configuration_sha256', 'reference_configuration_sha256',
    'target_configuration_sha256', 'comparability', 'compatibility_domains', 'summary', 'rows', 'persisted',
    'created_at', 'reference', 'target', 'compatibility_status', 'left_comparison_compatibility_id',
    'right_comparison_compatibility_id', 'override_used', 'compatibility_differences',
] as const;
const MULTI_COMPARISON_KEYS = [
    'schema_name', 'schema_version', 'comparison_mode', 'comparison_id', 'comparison_sha256',
    'reference_landscape_sha256', 'target_landscape_sha256', 'target_landscape_sha256s', 'target_labels',
    'configuration_id', 'configuration_sha256', 'reference_configuration_sha256', 'target_configuration_sha256s',
    'pair_compatibility', 'source_result_references', 'comparability', 'summary', 'rows', 'persisted',
    'created_at', 'reference', 'target', 'compatibility_status', 'left_comparison_compatibility_id',
    'right_comparison_compatibility_id', 'override_used', 'compatibility_differences',
] as const;

export const parseFrustraMpnnComparison = (value: unknown): FrustraMpnnComparison => {
    const wire = fmRecord(value, 'comparison');
    const isPair = wire.schema_name === 'frustrampnn_comparison';
    const isMulti = wire.schema_name === 'frustrampnn_multistate_comparison';
    if (!isPair && !isMulti) throw new Error('comparison schema identity is invalid');
    const payload = fmClosedProjection(wire, 'comparison', isPair ? PAIR_COMPARISON_KEYS : MULTI_COMPARISON_KEYS, isPair ? PAIR_COMPARISON_KEYS : MULTI_COMPARISON_KEYS);
    if (payload.schema_version !== 1 || payload.persisted !== true) throw new Error('comparison schema version or persistence identity is invalid');
    if (!Array.isArray(payload.rows)) throw new Error('comparison.rows must be an array');
    const common = {
        schema_version: 1 as const,
        comparison_id: fmString(payload.comparison_id, 'comparison.comparison_id'),
        comparison_sha256: fmSha256(payload.comparison_sha256, 'comparison.comparison_sha256'),
        reference_landscape_sha256: fmSha256(payload.reference_landscape_sha256, 'comparison.reference_landscape_sha256'),
        target_landscape_sha256: fmSha256(payload.target_landscape_sha256, 'comparison.target_landscape_sha256'),
        configuration_id: fmNullableString(payload.configuration_id, 'comparison.configuration_id'),
        configuration_sha256: fmOptionalSha256(payload.configuration_sha256, 'comparison.configuration_sha256'),
        reference_configuration_sha256: fmOptionalSha256(payload.reference_configuration_sha256, 'comparison.reference_configuration_sha256'),
        persisted: true as const,
        created_at: fmString(payload.created_at, 'comparison.created_at'),
        reference: parseResultReference(payload.reference, 'comparison.reference'),
        target: parseResultReference(payload.target, 'comparison.target'),
        ...parseCompatibilityMetadata(payload, 'comparison'),
    };
    const comparabilityWire = fmRecord(payload.comparability, 'comparison.comparability');
    if (comparabilityWire.status !== 'comparable' && comparabilityWire.status !== 'incompatible') throw new Error('comparison.comparability.status is invalid');
    if (isPair) {
        const comparability = fmClosedProjection(comparabilityWire, 'comparison.comparability', ['status', 'reasons', 'reference_configuration_id', 'target_configuration_id', 'reference_configuration_sha256', 'target_configuration_sha256'], ['status', 'reasons', 'reference_configuration_id', 'target_configuration_id', 'reference_configuration_sha256', 'target_configuration_sha256']);
        const domains = parseCompatibilityDomains(payload.compatibility_domains, 'comparison.compatibility_domains');
        const rows = payload.rows.map(parsePairComparisonRow);
        if (domains.raw_score.status !== 'compatible' && rows.some((row) => row.raw_score_delta !== null)) throw new Error('unsafe raw score delta is present while raw-score compatibility is suppressed');
        if ((domains.raw_score.status !== 'compatible' || domains.classification.status !== 'compatible') && rows.some((row) => row.classification_transition !== null)) throw new Error('unsafe classification transition is present while classification compatibility is suppressed');
        if (common.override_used && rows.some((row) => row.raw_score_delta !== null || row.classification_transition !== null)) {
            throw new Error('compatibility override responses must not contain score deltas or classification transitions');
        }
        return {
            schema_name: 'frustrampnn_comparison',
            ...common,
            summary: parsePairComparisonSummary(payload.summary),
            target_configuration_sha256: fmOptionalSha256(payload.target_configuration_sha256, 'comparison.target_configuration_sha256'),
            comparability: {
                status: comparability.status as 'comparable' | 'incompatible',
                reasons: fmStringArray(comparability.reasons, 'comparison.comparability.reasons'),
                reference_configuration_id: fmNullableString(comparability.reference_configuration_id, 'comparison.comparability.reference_configuration_id'),
                target_configuration_id: fmNullableString(comparability.target_configuration_id, 'comparison.comparability.target_configuration_id'),
                reference_configuration_sha256: fmOptionalSha256(comparability.reference_configuration_sha256, 'comparison.comparability.reference_configuration_sha256'),
                target_configuration_sha256: fmOptionalSha256(comparability.target_configuration_sha256, 'comparison.comparability.target_configuration_sha256'),
            },
            compatibility_domains: domains,
            rows,
        };
    }

    const comparabilityKeys = ['status', 'reasons', 'target_count', 'pair_compatibility', 'compatibility_status', 'left_comparison_compatibility_id', 'right_comparison_compatibility_id', 'override_used', 'compatibility_differences'] as const;
    const comparability = fmClosedProjection(comparabilityWire, 'comparison.comparability', comparabilityKeys, comparabilityKeys);
    const pairCompatibility = parsePairCompatibilityArray(payload.pair_compatibility, 'comparison.pair_compatibility');
    const comparabilityPairs = parsePairCompatibilityArray(comparability.pair_compatibility, 'comparison.comparability.pair_compatibility');
    const targetLabels = fmStringArray(payload.target_labels, 'comparison.target_labels');
    const targetHashes = fmStringArray(payload.target_landscape_sha256s, 'comparison.target_landscape_sha256s').map((item, index) => fmSha256(item, `comparison.target_landscape_sha256s[${index}]`));
    if (!Array.isArray(payload.target_configuration_sha256s) || !Array.isArray(payload.source_result_references)) throw new Error('multi comparison target configuration or source references are invalid');
    const targetConfigurationHashes = payload.target_configuration_sha256s.map((item, index) => fmOptionalSha256(item, `comparison.target_configuration_sha256s[${index}]`));
    const rows = payload.rows.map(parseMultiComparisonRow);
    const targetCount = fmInteger(comparability.target_count, 'comparison.comparability.target_count', 1);
    if (targetLabels.length !== targetCount || targetHashes.length !== targetCount || targetConfigurationHashes.length !== targetCount || pairCompatibility.length !== targetCount || comparabilityPairs.length !== targetCount) throw new Error('multi comparison target arrays are inconsistent');
    for (const row of rows) {
        if (row.targets.length !== targetCount || row.raw_score_deltas.length !== targetCount || row.classification_transitions.length !== targetCount || row.missingness_by_target.length !== targetCount) throw new Error('multi comparison row target arrays are inconsistent');
        pairCompatibility.forEach((pair, index) => {
            if (pair.compatibility_domains.raw_score.status !== 'compatible' && row.raw_score_deltas[index] !== null) throw new Error(`unsafe raw score delta is present for ${pair.target_label}`);
            if ((pair.compatibility_domains.raw_score.status !== 'compatible' || pair.compatibility_domains.classification.status !== 'compatible') && row.classification_transitions[index] !== null) throw new Error(`unsafe classification transition is present for ${pair.target_label}`);
            if (pair.override_used && (row.raw_score_deltas[index] !== null || row.classification_transitions[index] !== null)) throw new Error(`compatibility override for ${pair.target_label} must not contain score deltas or classification transitions`);
        });
    }
    const summary = parseMultiComparisonSummary(payload.summary);
    if (summary.target_count !== targetCount) throw new Error('comparison.summary.target_count is inconsistent with comparability');
    return {
        schema_name: 'frustrampnn_multistate_comparison',
        ...common,
        summary,
        comparison_mode: (() => { if (payload.comparison_mode !== 'multi_state') throw new Error('comparison.comparison_mode is invalid'); return 'multi_state' as const; })(),
        target_landscape_sha256s: targetHashes,
        target_labels: targetLabels,
        target_configuration_sha256s: targetConfigurationHashes,
        pair_compatibility: pairCompatibility,
        source_result_references: payload.source_result_references.map((item, index) => parseSourceResultReference(item, `comparison.source_result_references[${index}]`)),
        comparability: {
            status: comparability.status as 'comparable' | 'incompatible',
            reasons: fmStringArray(comparability.reasons, 'comparison.comparability.reasons'),
            target_count: targetCount,
            pair_compatibility: comparabilityPairs,
            ...parseCompatibilityMetadata(comparability, 'comparison.comparability'),
        },
        rows,
    };
};

export interface FrustraMpnnGuidancePlan {
    schema_name: 'frustrampnn_guidance';
    schema_version: 1;
    guidance_id: string;
    guidance_sha256: string;
    source_comparison_id?: string;
    source_landscape_sha256: string;
    configuration_id: string;
    configuration_sha256: string;
    region: {
        region_type: 'residue_set' | 'sequence_span' | 'pocket' | 'interface' | 'contact_set' | 'loop' | 'domain' | 'mapped_region';
        requested_residues: Array<{ entity_instance_id: string | null; auth_asym_id: string; auth_seq_id: number; insertion_code: string }>;
        resolved_residues: Array<{ auth_asym_id: string; auth_seq_id: number; insertion_code: string }>;
        unresolved_residues: Array<{ auth_asym_id: string; auth_seq_id: number; insertion_code: string }>;
        region_sha256: string;
        mapping_method: string | null;
        source_artifact_sha256: string | null;
        mapping_artifact_sha256: string | null;
        start: number | null;
        end: number | null;
    };
    objective: {
        objective_type: 'score_aggregate' | 'class_count' | 'class_transition';
        direction: 'higher_is_better' | 'lower_is_better';
        aggregation: 'mean' | 'median' | 'min' | 'max';
        target_class: FrustraMpnnClass | null;
        reference_class: FrustraMpnnClass | null;
    };
    constraints: { prohibited_mutations: string[] };
    ranking: { mode: 'lexicographic'; tie_break: 'sequence_index_then_mutation' | null };
    ranked_slots: Array<{
        entity_instance_id: string | null;
        auth_asym_id: string;
        auth_seq_id: number;
        insertion_code: string;
        sequence_index: number;
        wt: string | null;
        mutation_aa: string;
        score: number;
        class: FrustraMpnnClass | null;
        scoreable: true;
        rationale: string;
        rank: number;
    }>;
    rationale: string;
    decision_support_only: true;
    instrument_control: false;
    observed_outcome: null;
    persisted: true;
    created_at: string;
}

interface FrustraMpnnLandscapeWireRow {
    id: string;
    invocation_id: string;
    candidate_id: string;
    target_id: string;
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    pdb_chain_id: string | null;
    model_position: number | null;
    wt: string;
    mutation_aa: string;
    score: number | null;
    class: FrustraMpnnClass | null;
    score_class: FrustraMpnnClass | null;
    scoreable: boolean;
    status: FrustraMpnnSlotStatus;
    reason: string | null;
    native: boolean;
    provenance: {
        schema_name?: string | null;
        schema_version?: number | null;
        landscape_sha256: string;
        structure_map_sha256: string;
        normalized_pdb_sha256: string;
        raw_csv_sha256: string;
        threshold_policy: { id?: string | null; mode?: 'canonical' | 'custom' | null; high_max: number; minimal_min: number };
        threshold_policy_sha256: string;
        execution_configuration_sha256?: string | null;
        requested_settings_sha256?: string | null;
        effective_settings_sha256?: string | null;
        runtime_identity_sha256?: string | null;
        source_artifact_sha256?: string | null;
        threshold_policy_id?: string | null;
    };
    residue: {
        entity_instance_id: string;
        source_entity_id: string | null;
        label_asym_id: string | null;
        auth_asym_id: string;
        label_seq_id: number | null;
        auth_seq_id: number;
        insertion_code: string;
        sequence_index: number;
        pdb_chain_id: string;
        pdb_residue_id: number | null;
        pdb_insertion_code: string | null;
        model_position: number;
        residue_name: string | null;
        wt: string | null;
    } | null;
}

interface FrustraMpnnLandscapeWirePage {
    items: FrustraMpnnLandscapeWireRow[];
    candidate_id: string;
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
}

export interface FrustraMpnnLandscapeFilters {
    target_id?: string;
    entity_instance_id?: string;
    auth_asym_id?: string;
    auth_seq_id?: number;
    insertion_code?: string;
    sequence_index?: number;
    mutation_aa?: string;
    status?: FrustraMpnnSlotStatus;
}

const requireCanonicalClass = (value: unknown): FrustraMpnnClass | null => {
    if (value == null) return null;
    if (value === 'high' || value === 'neutral' || value === 'minimal') return value;
    throw new Error(`Persisted FrustraMPNN row has unsupported class ${String(value)}`);
};

const parseFrustraMpnnLandscapeWirePage = (value: unknown): FrustraMpnnLandscapeWirePage => {
    const page = fmClosedProjection(value, 'FrustraMPNN landscape page', ['items', 'candidate_id', 'total', 'limit', 'offset', 'next_offset'], ['items', 'candidate_id', 'total', 'limit', 'offset', 'next_offset']);
    if (!Array.isArray(page.items)) throw new Error('FrustraMPNN landscape page.items must be an array');
    const items = page.items.map((item, index): FrustraMpnnLandscapeWireRow => {
        const label = `FrustraMPNN landscape page.items[${index}]`;
        const rowKeys = ['id', 'invocation_id', 'candidate_id', 'target_id', 'entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'pdb_chain_id', 'model_position', 'wt', 'mutation_aa', 'score', 'score_class', 'class', 'scoreable', 'status', 'reason', 'native', 'provenance', 'residue'] as const;
        const row = fmClosedProjection(item, label, rowKeys, rowKeys);
        const provenanceKeys = ['schema_name', 'schema_version', 'landscape_sha256', 'structure_map_sha256', 'normalized_pdb_sha256', 'raw_csv_sha256', 'threshold_policy', 'threshold_policy_sha256', 'execution_configuration_sha256', 'requested_settings_sha256', 'effective_settings_sha256', 'runtime_identity_sha256', 'source_artifact_sha256', 'threshold_policy_id'] as const;
        const provenanceRequired = ['landscape_sha256', 'structure_map_sha256', 'normalized_pdb_sha256', 'raw_csv_sha256', 'threshold_policy', 'threshold_policy_sha256'] as const;
        const provenance = fmClosedProjection(row.provenance, `${label}.provenance`, provenanceKeys, provenanceRequired);
        const threshold = fmClosedProjection(provenance.threshold_policy, `${label}.provenance.threshold_policy`, ['id', 'mode', 'high_max', 'minimal_min'], ['high_max', 'minimal_min']);
        if (threshold.mode !== undefined && threshold.mode !== null && threshold.mode !== 'canonical' && threshold.mode !== 'custom') throw new Error(`${label}.provenance.threshold_policy.mode is invalid`);
        const residueKeys = ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'label_seq_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'pdb_chain_id', 'pdb_residue_id', 'pdb_insertion_code', 'model_position', 'residue_name', 'wt'] as const;
        const residue = row.residue === null ? null : fmClosedProjection(row.residue, `${label}.residue`, residueKeys, residueKeys);
        const status = row.status;
        if (status !== 'ok' && status !== 'missing') throw new Error(`${label}.status is invalid`);
        const parsedProvenance: FrustraMpnnLandscapeWireRow['provenance'] = {
            ...(provenance.schema_name !== undefined ? { schema_name: fmNullableString(provenance.schema_name, `${label}.provenance.schema_name`) } : {}),
            ...(provenance.schema_version !== undefined ? { schema_version: fmOptionalInteger(provenance.schema_version, `${label}.provenance.schema_version`, 1) } : {}),
            landscape_sha256: fmSha256(provenance.landscape_sha256, `${label}.provenance.landscape_sha256`),
            structure_map_sha256: fmSha256(provenance.structure_map_sha256, `${label}.provenance.structure_map_sha256`),
            normalized_pdb_sha256: fmSha256(provenance.normalized_pdb_sha256, `${label}.provenance.normalized_pdb_sha256`),
            raw_csv_sha256: fmSha256(provenance.raw_csv_sha256, `${label}.provenance.raw_csv_sha256`),
            threshold_policy: {
                ...(threshold.id !== undefined ? { id: fmNullableString(threshold.id, `${label}.provenance.threshold_policy.id`) } : {}),
                ...(threshold.mode !== undefined ? { mode: threshold.mode as 'canonical' | 'custom' | null } : {}),
                high_max: fmFinite(threshold.high_max, `${label}.provenance.threshold_policy.high_max`),
                minimal_min: fmFinite(threshold.minimal_min, `${label}.provenance.threshold_policy.minimal_min`),
            },
            threshold_policy_sha256: fmSha256(provenance.threshold_policy_sha256, `${label}.provenance.threshold_policy_sha256`),
        };
        for (const key of ['execution_configuration_sha256', 'requested_settings_sha256', 'effective_settings_sha256', 'runtime_identity_sha256', 'source_artifact_sha256'] as const) {
            if (provenance[key] !== undefined) parsedProvenance[key] = fmOptionalSha256(provenance[key], `${label}.provenance.${key}`);
        }
        if (provenance.threshold_policy_id !== undefined) parsedProvenance.threshold_policy_id = fmNullableString(provenance.threshold_policy_id, `${label}.provenance.threshold_policy_id`);
        return {
            id: fmString(row.id, `${label}.id`),
            invocation_id: fmString(row.invocation_id, `${label}.invocation_id`),
            candidate_id: fmString(row.candidate_id, `${label}.candidate_id`),
            target_id: fmString(row.target_id, `${label}.target_id`),
            entity_instance_id: fmString(row.entity_instance_id, `${label}.entity_instance_id`),
            source_entity_id: fmNullableString(row.source_entity_id, `${label}.source_entity_id`),
            label_asym_id: fmNullableString(row.label_asym_id, `${label}.label_asym_id`),
            auth_asym_id: fmString(row.auth_asym_id, `${label}.auth_asym_id`),
            auth_seq_id: fmInteger(row.auth_seq_id, `${label}.auth_seq_id`),
            insertion_code: fmString(row.insertion_code, `${label}.insertion_code`, true),
            sequence_index: fmInteger(row.sequence_index, `${label}.sequence_index`, 1),
            pdb_chain_id: fmNullableString(row.pdb_chain_id, `${label}.pdb_chain_id`),
            model_position: fmOptionalInteger(row.model_position, `${label}.model_position`, 0),
            wt: fmString(row.wt, `${label}.wt`),
            mutation_aa: fmString(row.mutation_aa, `${label}.mutation_aa`),
            score: fmNullableFinite(row.score, `${label}.score`),
            score_class: requireCanonicalClass(row.score_class),
            class: requireCanonicalClass(row.class),
            scoreable: fmBoolean(row.scoreable, `${label}.scoreable`),
            status,
            reason: fmNullableString(row.reason, `${label}.reason`),
            native: fmBoolean(row.native, `${label}.native`),
            provenance: parsedProvenance,
            residue: residue === null ? null : {
                entity_instance_id: fmString(residue.entity_instance_id, `${label}.residue.entity_instance_id`),
                source_entity_id: fmNullableString(residue.source_entity_id, `${label}.residue.source_entity_id`),
                label_asym_id: fmNullableString(residue.label_asym_id, `${label}.residue.label_asym_id`),
                auth_asym_id: fmString(residue.auth_asym_id, `${label}.residue.auth_asym_id`),
                label_seq_id: fmOptionalInteger(residue.label_seq_id, `${label}.residue.label_seq_id`, 1),
                auth_seq_id: fmInteger(residue.auth_seq_id, `${label}.residue.auth_seq_id`),
                insertion_code: fmString(residue.insertion_code, `${label}.residue.insertion_code`, true),
                sequence_index: fmInteger(residue.sequence_index, `${label}.residue.sequence_index`, 1),
                pdb_chain_id: fmString(residue.pdb_chain_id, `${label}.residue.pdb_chain_id`),
                pdb_residue_id: fmOptionalInteger(residue.pdb_residue_id, `${label}.residue.pdb_residue_id`, -999),
                pdb_insertion_code: residue.pdb_insertion_code === null ? null : fmPdbInsertionCode(residue.pdb_insertion_code, `${label}.residue.pdb_insertion_code`),
                model_position: fmInteger(residue.model_position, `${label}.residue.model_position`, 0),
                residue_name: residue.residue_name === null ? null : fmResidueName(residue.residue_name, `${label}.residue.residue_name`),
                wt: residue.wt === null ? null : fmString(residue.wt, `${label}.residue.wt`),
            },
        };
    });
    return {
        items,
        candidate_id: fmString(page.candidate_id, 'FrustraMPNN landscape page.candidate_id'),
        total: fmInteger(page.total, 'FrustraMPNN landscape page.total', 0),
        limit: fmInteger(page.limit, 'FrustraMPNN landscape page.limit', 1),
        offset: fmInteger(page.offset, 'FrustraMPNN landscape page.offset', 0),
        next_offset: fmOptionalInteger(page.next_offset, 'FrustraMPNN landscape page.next_offset'),
    };
};

export interface NormalizedFrustraMpnnLandscapePage extends CmLandscapePage {
    total: number;
}

export const normalizeFrustraMpnnLandscapePage = (
    jobId: string,
    value: unknown,
): NormalizedFrustraMpnnLandscapePage => {
    const wire = parseFrustraMpnnLandscapeWirePage(value);
    if (!wire.candidate_id || !Number.isInteger(wire.offset) || wire.offset < 0
        || !Number.isInteger(wire.limit) || wire.limit < 1 || wire.limit > 500
        || !Number.isInteger(wire.total) || wire.total < 0
        || wire.items.length > wire.limit || wire.offset + wire.items.length > wire.total) {
        throw new Error('Persisted FrustraMPNN landscape envelope is invalid');
    }
    const expectedNextOffset = wire.offset + wire.items.length < wire.total
        ? wire.offset + wire.items.length
        : null;
    if (wire.next_offset !== expectedNextOffset) {
        throw new Error('Persisted FrustraMPNN landscape pagination is inconsistent');
    }
    const rows: CmLandscapeRow[] = wire.items.map((row) => {
        if (row.candidate_id !== wire.candidate_id || !row.entity_instance_id || !row.auth_asym_id
            || !Number.isInteger(row.auth_seq_id) || !Number.isInteger(row.sequence_index) || row.sequence_index < 1
            || row.insertion_code.length > 1 || !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(row.wt)
            || !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(row.mutation_aa)
            || !row.residue
            || row.residue.entity_instance_id !== row.entity_instance_id
            || row.residue.source_entity_id !== row.source_entity_id
            || row.residue.label_asym_id !== row.label_asym_id
            || row.residue.auth_asym_id !== row.auth_asym_id
            || row.residue.auth_seq_id !== row.auth_seq_id
            || row.residue.insertion_code !== row.insertion_code
            || row.residue.sequence_index !== row.sequence_index
            || row.residue.wt !== row.wt
            || row.residue.pdb_chain_id !== row.pdb_chain_id
            || row.residue.model_position !== row.model_position) {
            throw new Error('Persisted FrustraMPNN landscape identity is invalid');
        }
        const canonicalClass = requireCanonicalClass(row.class);
        if (canonicalClass !== requireCanonicalClass(row.score_class)) {
            throw new Error('Persisted FrustraMPNN landscape class fields conflict');
        }
        if (row.status === 'ok' && (!row.scoreable || typeof row.score !== 'number' || !Number.isFinite(row.score) || canonicalClass == null)) {
            throw new Error('Persisted FrustraMPNN scoreable slot is incomplete');
        }
        if (row.status === 'missing' && (row.scoreable || row.score != null || canonicalClass != null)) {
            throw new Error('Persisted FrustraMPNN missing slot contains a score or class');
        }
        return {
            candidate_id: row.candidate_id,
            entity_instance_id: row.entity_instance_id,
            source_entity_id: row.residue.source_entity_id,
            label_asym_id: row.residue.label_asym_id,
            label_seq_id: row.residue.label_seq_id,
            auth_asym_id: row.auth_asym_id,
            auth_seq_id: String(row.auth_seq_id),
            insertion_code: row.insertion_code,
            sequence_index: row.sequence_index,
            wt: row.wt,
            pdb_chain_id: row.residue.pdb_chain_id,
            pdb_residue_id: row.residue.pdb_residue_id,
            pdb_insertion_code: row.residue.pdb_insertion_code,
            model_position: row.residue.model_position,
            residue_name: row.residue.residue_name,
            mutation_aa: row.mutation_aa,
            score: row.score,
            class: canonicalClass,
            scoreable: row.scoreable,
            status: row.status,
            reason: row.reason,
            provenance: row.provenance,
        };
    });
    return {
        request_id: jobId,
        offset: wire.offset,
        limit: wire.limit,
        total: wire.total,
        candidate_id: wire.candidate_id,
        entity_instance_id: null,
        sequence_start: null,
        sequence_end: null,
        next_offset: wire.next_offset,
        rows,
    };
};

export const parseFrustraMpnnArtifactList = (value: unknown): FrustraMpnnArtifactList => {
    const payload = fmClosedProjection(value, 'FrustraMPNN artifact list', ['items', 'total'], ['items', 'total']);
    if (!Array.isArray(payload.items)) throw new Error('FrustraMPNN artifact list.items must be an array');
    const items = payload.items.map((item, index): FrustraMpnnArtifact => {
        const label = `FrustraMPNN artifact list.items[${index}]`;
        const keys = ['artifact_id', 'role', 'content_sha256', 'size_bytes', 'media_type', 'schema_name', 'schema_version', 'cardinality', 'download_url'] as const;
        const artifact = fmClosedProjection(item, label, keys, keys);
        const cardinality = artifact.cardinality === null ? null : fmClosedProjection(artifact.cardinality, `${label}.cardinality`, ['kind', 'count'], ['kind', 'count']);
        let parsedCardinality: FrustraMpnnTerminalArtifact['cardinality'] = null;
        if (cardinality !== null) {
            const kind = fmString(cardinality.kind, `${label}.cardinality.kind`);
            if (kind !== 'rows' && kind !== 'residues' && kind !== 'slots' && kind !== 'records') throw new Error(`${label}.cardinality.kind is invalid`);
            parsedCardinality = { kind, count: fmInteger(cardinality.count, `${label}.cardinality.count`, 0) };
        }
        return {
            artifact_id: fmString(artifact.artifact_id, `${label}.artifact_id`),
            role: fmString(artifact.role, `${label}.role`),
            content_sha256: fmSha256(artifact.content_sha256, `${label}.content_sha256`),
            size_bytes: fmInteger(artifact.size_bytes, `${label}.size_bytes`, 0),
            media_type: fmString(artifact.media_type, `${label}.media_type`),
            schema_name: fmNullableString(artifact.schema_name, `${label}.schema_name`),
            schema_version: fmOptionalInteger(artifact.schema_version, `${label}.schema_version`, 1),
            cardinality: parsedCardinality,
            download_url: fmString(artifact.download_url, `${label}.download_url`),
        };
    });
    const total = fmInteger(payload.total, 'FrustraMPNN artifact list.total', 0);
    if (total !== items.length) throw new Error('FrustraMPNN artifact list total is inconsistent');
    return { items, total };
};

export const analyzeFrustraMpnnUpload = async (
    file: File,
    settings: FrustraMpnnRequestedSettings,
    signal?: AbortSignal,
): Promise<FrustraMpnnChildReceipt> => {
    const form = new FormData();
    form.append('pdb_file', file);
    form.append('frustrampnn_settings', JSON.stringify(parseFrustraMpnnRequestedSettings(settings)));
    return parseFrustraMpnnChildReceipt((await api.post<unknown>(
        '/api/frustrampnn/jobs/uploads/analyze',
        form,
        { signal },
    )).data);
};

export const analyzeFrustraMpnnDesigns = async (
    parentJobId: string,
    payload: FrustraMpnnAnalyzeRequest,
    signal?: AbortSignal,
): Promise<FrustraMpnnChildReceipt> => {
    const normalizedPayload = {
        selections: payload.selections,
        frustrampnn_settings: parseFrustraMpnnRequestedSettings(payload.frustrampnn_settings),
    };
    return parseFrustraMpnnChildReceipt((
    await api.post<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(parentJobId)}/analyze`,
        normalizedPayload,
        { signal },
    )
    ).data);
};

export const reanalyzeFrustraMpnn = async (
    childJobId: string,
    settings: FrustraMpnnRequestedSettings,
    signal?: AbortSignal,
): Promise<FrustraMpnnChildReceipt> => parseFrustraMpnnChildReceipt((
    await api.post<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(childJobId)}/reanalyze`,
        { frustrampnn_settings: parseFrustraMpnnRequestedSettings(settings) },
        { signal },
    )
).data);

export const fetchFrustraMpnnReceipt = async (childJobId: string, signal?: AbortSignal): Promise<FrustraMpnnChildReceipt> => parseFrustraMpnnChildReceipt((
    await api.get<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(childJobId)}/receipt`,
        { signal },
    )
).data);

export const listFrustraMpnnResults = async (
    jobId: string,
    limit = 200,
    offset = 0,
    signal?: AbortSignal,
): Promise<FrustraMpnnResultList> => {
    const response = await api.get<unknown>(`/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/results`, {
        params: { limit, offset }, signal,
    });
    return parseFrustraMpnnResultList(response.data);
};

export const fetchFrustraMpnnResult = async (
    jobId: string,
    invocationId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnResultDetail> => {
    const response = await api.get<unknown>(
        `/api/frustrampnn/results/${encodeURIComponent(invocationId)}`,
        { params: { job_id: jobId }, signal },
    );
    return parseFrustraMpnnResultDetail(response.data);
};

export const fetchFrustraMpnnStatistics = async (
    jobId: string,
    invocationId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnStatisticsResponse> => {
    const response = await api.get<unknown>(
        `/api/frustrampnn/results/${encodeURIComponent(invocationId)}/statistics`,
        { params: { job_id: jobId }, signal },
    );
    return parseFrustraMpnnStatisticsResponse(response.data);
};

interface FrustraMpnnStatisticsQueryBase {
    datasets: FrustraMpnnResultReference[];
    limit?: number;
    offset?: number;
}

export type FrustraMpnnStatisticsQueryRequest =
    | (FrustraMpnnStatisticsQueryBase & { level: 'overview'; filters?: Record<never, never> })
    | (FrustraMpnnStatisticsQueryBase & {
        level: 'residue';
        filters?: {
            entity_instance_id?: string;
            source_entity_id?: string;
            label_asym_id?: string;
            auth_asym_id?: string;
            auth_seq_id?: number;
            insertion_code?: string;
            sequence_index?: number;
            wt?: string;
            pdb_chain_id?: string;
            model_position?: number;
        };
    })
    | (FrustraMpnnStatisticsQueryBase & { level: 'mutation_aa'; filters?: { mutation_aa?: string } })
    | (FrustraMpnnStatisticsQueryBase & {
        level: 'chain';
        filters?: {
            entity_instance_id?: string;
            source_entity_id?: string;
            label_asym_id?: string;
            auth_asym_id?: string;
            pdb_chain_id?: string;
        };
    })
    | (FrustraMpnnStatisticsQueryBase & {
        level: 'entity';
        filters?: { entity_instance_id?: string; source_entity_id?: string; label_asym_id?: string };
    });

const normalizeStatisticsQueryFilters = (request: FrustraMpnnStatisticsQueryRequest): Record<string, string | number> => {
    const filters = fmRecord(request.filters ?? {}, 'statistics query filters');
    const allowed = {
        overview: new Set<string>(),
        residue: new Set(['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt', 'pdb_chain_id', 'model_position']),
        mutation_aa: new Set(['mutation_aa']),
        chain: new Set(['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'pdb_chain_id']),
        entity: new Set(['entity_instance_id', 'source_entity_id', 'label_asym_id']),
    }[request.level];
    for (const key of Object.keys(filters)) {
        if (!allowed.has(key)) throw new Error(`statistics query ${request.level} filters do not allow ${key}`);
    }
    const normalized: Record<string, string | number> = {};
    for (const [key, value] of Object.entries(filters)) {
        if (['auth_seq_id', 'sequence_index', 'model_position'].includes(key)) {
            const minimum = key === 'sequence_index' ? 1 : key === 'model_position' ? 0 : Number.MIN_SAFE_INTEGER;
            normalized[key] = fmInteger(value, `statistics query filters.${key}`, minimum);
        } else {
            const text = fmString(value, `statistics query filters.${key}`);
            if (key === 'insertion_code' && text.length > 1) throw new Error('statistics query insertion_code is too long');
            if ((key === 'wt' || key === 'mutation_aa') && !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(text)) throw new Error(`statistics query ${key} is invalid`);
            normalized[key] = text;
        }
    }
    return normalized;
};

export const queryFrustraMpnnStatistics = async (
    request: FrustraMpnnStatisticsQueryRequest,
    signal?: AbortSignal,
): Promise<FrustraMpnnStatisticsQueryResponse> => {
    if (request.datasets.length < 1 || request.datasets.length > 50) {
        throw new Error('FrustraMPNN statistics query requires 1-50 datasets');
    }
    const limit = request.limit ?? 100;
    const offset = request.offset ?? 0;
    if (!Number.isInteger(limit) || limit < 1 || limit > 500 || !Number.isInteger(offset) || offset < 0 || offset > 1_000_000) {
        throw new Error('FrustraMPNN statistics query pagination is invalid');
    }
    const filters = normalizeStatisticsQueryFilters(request);
    const response = await api.post<unknown>('/api/frustrampnn/statistics/query', {
        datasets: request.datasets.map((dataset) => ({
            parent_job_id: fmString(dataset.parent_job_id, 'statistics query parent_job_id'),
            invocation_id: fmString(dataset.invocation_id, 'statistics query invocation_id'),
        })),
        level: request.level,
        filters,
        limit,
        offset,
    }, { signal });
    return parseFrustraMpnnStatisticsQueryResponse(response.data);
};

export const fetchFrustraMpnnLandscape = async (
    jobId: string,
    invocationId: string,
    offset: number,
    limit: number,
    filters: FrustraMpnnLandscapeFilters = {},
    signal?: AbortSignal,
): Promise<NormalizedFrustraMpnnLandscapePage> => {
    const wire = (
        await api.get<unknown>(
            `/api/frustrampnn/results/${encodeURIComponent(invocationId)}/landscape`,
            { params: { job_id: jobId, offset, limit, ...filters }, signal },
        )
    ).data;
    return normalizeFrustraMpnnLandscapePage(jobId, wire);
};

export const listFrustraMpnnArtifacts = async (
    jobId: string,
    invocationId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnArtifactList> => parseFrustraMpnnArtifactList((
    await api.get<unknown>(
        `/api/frustrampnn/results/${encodeURIComponent(invocationId)}/artifacts`,
        { params: { job_id: jobId }, signal },
    )
).data);

export const parseFrustraMpnnStructureMap = (value: unknown): FrustraMpnnStructureMap => {
    const label = 'FrustraMPNN structure map';
    const keys = [
        'schema_name', 'schema_version', 'target_id', 'parent_job_id', 'candidate_id',
        'source_format', 'source_sha256', 'source_bytes', 'identity_authority', 'identity_domain',
        'authority_artifact_sha256', 'normalized_pdb_sha256', 'selected_source_model', 'altloc_policy',
        'normalizer_version', 'model_ready_sequence', 'model_ready_sequence_sha256', 'excluded_records', 'rows',
    ] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    if (payload.schema_name !== 'frustrampnn_structure_map' || payload.schema_version !== 1
        || payload.normalizer_version !== 'frustrampnn_structure_normalizer_v1') {
        throw new Error(`${label} schema identity is invalid`);
    }
    if (payload.source_format !== 'pdb' && payload.source_format !== 'mmcif') throw new Error(`${label}.source_format is invalid`);
    const identityAuthorities = ['pdb_self_identity_v1', 'mmcif_atom_site_v1', 'producer_manifest_v1', 'cm_complex_snapshot_v1'] as const;
    if (!identityAuthorities.includes(payload.identity_authority as typeof identityAuthorities[number])) throw new Error(`${label}.identity_authority is invalid`);
    if (payload.identity_domain !== 'candidate_local' && payload.identity_domain !== 'source_authoritative') throw new Error(`${label}.identity_domain is invalid`);
    const altlocPolicy = fmString(payload.altloc_policy, `${label}.altloc_policy`);
    if (!/^blank_or_explicit:(?:<blank>|[A-Za-z0-9])$/.test(altlocPolicy)) throw new Error(`${label}.altloc_policy is invalid`);
    const modelReadySequence = fmString(payload.model_ready_sequence, `${label}.model_ready_sequence`);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]+$/.test(modelReadySequence)) throw new Error(`${label}.model_ready_sequence is invalid`);
    if (!Array.isArray(payload.excluded_records)) throw new Error(`${label}.excluded_records must be an array`);
    const excludedReasonCodes = ['non_protein_entity', 'not_selected', 'missing_backbone', 'nonstandard_residue', 'unsupported_record'] as const;
    const excludedRecords = payload.excluded_records.map((item, index) => {
        const itemLabel = `${label}.excluded_records[${index}]`;
        const record = fmClosedProjection(item, itemLabel, ['source_identity', 'reason_code', 'reason'], ['source_identity', 'reason_code', 'reason']);
        if (!excludedReasonCodes.includes(record.reason_code as typeof excludedReasonCodes[number])) throw new Error(`${itemLabel}.reason_code is invalid`);
        return {
            source_identity: fmString(record.source_identity, `${itemLabel}.source_identity`),
            reason_code: record.reason_code as typeof excludedReasonCodes[number],
            reason: fmString(record.reason, `${itemLabel}.reason`),
        };
    });
    if (!Array.isArray(payload.rows) || payload.rows.length < 1) throw new Error(`${label}.rows must contain at least one row`);
    const rowKeys = [
        'entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'label_seq_id',
        'auth_seq_id', 'insertion_code', 'sequence_index', 'pdb_chain_id', 'pdb_residue_id',
        'pdb_insertion_code', 'model_position', 'residue_name', 'wt', 'selected_model', 'selected_altloc',
        'backbone_complete', 'backbone_atoms', 'status', 'reason',
    ] as const;
    const rows = payload.rows.map((item, index) => {
        const rowLabel = `${label}.rows[${index}]`;
        const row = fmClosedProjection(item, rowLabel, rowKeys, rowKeys);
        const authSeqId = fmInteger(row.auth_seq_id, `${rowLabel}.auth_seq_id`, -999);
        const pdbResidueId = fmInteger(row.pdb_residue_id, `${rowLabel}.pdb_residue_id`, -999);
        if (authSeqId > 9999) throw new Error(`${rowLabel}.auth_seq_id is invalid`);
        if (pdbResidueId > 9999) throw new Error(`${rowLabel}.pdb_residue_id is invalid`);
        const insertionCode = fmString(row.insertion_code, `${rowLabel}.insertion_code`, true);
        const pdbInsertionCode = fmString(row.pdb_insertion_code, `${rowLabel}.pdb_insertion_code`, true);
        const pdbChainId = fmString(row.pdb_chain_id, `${rowLabel}.pdb_chain_id`);
        const residueName = fmString(row.residue_name, `${rowLabel}.residue_name`);
        const selectedAltloc = fmString(row.selected_altloc, `${rowLabel}.selected_altloc`, true);
        if (insertionCode.length > 1 || pdbInsertionCode.length > 1 || pdbChainId.length !== 1
            || residueName.length !== 3 || selectedAltloc.length > 1) throw new Error(`${rowLabel} bounded string identity is invalid`);
        const wt = fmSchemaNullableString(row.wt, `${rowLabel}.wt`);
        if (wt !== null && !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt)) throw new Error(`${rowLabel}.wt is invalid`);
        const statuses = ['mapped', 'missing_backbone', 'nonstandard_residue', 'excluded'] as const;
        if (!statuses.includes(row.status as typeof statuses[number])) throw new Error(`${rowLabel}.status is invalid`);
        const atoms = fmClosedProjection(row.backbone_atoms, `${rowLabel}.backbone_atoms`, ['N', 'CA', 'C', 'O'], ['N', 'CA', 'C', 'O']);
        const labelSeqId = row.label_seq_id === null ? null : fmInteger(row.label_seq_id, `${rowLabel}.label_seq_id`, 1);
        if (labelSeqId !== null && labelSeqId > 9999) throw new Error(`${rowLabel}.label_seq_id is invalid`);
        return {
            entity_instance_id: fmString(row.entity_instance_id, `${rowLabel}.entity_instance_id`),
            source_entity_id: fmSchemaNullableString(row.source_entity_id, `${rowLabel}.source_entity_id`),
            label_asym_id: fmSchemaNullableString(row.label_asym_id, `${rowLabel}.label_asym_id`),
            auth_asym_id: fmString(row.auth_asym_id, `${rowLabel}.auth_asym_id`),
            label_seq_id: labelSeqId,
            auth_seq_id: authSeqId,
            insertion_code: insertionCode,
            sequence_index: fmInteger(row.sequence_index, `${rowLabel}.sequence_index`, 1),
            pdb_chain_id: pdbChainId,
            pdb_residue_id: pdbResidueId,
            pdb_insertion_code: pdbInsertionCode,
            model_position: fmInteger(row.model_position, `${rowLabel}.model_position`, 0),
            residue_name: residueName,
            wt,
            selected_model: fmInteger(row.selected_model, `${rowLabel}.selected_model`, 1),
            selected_altloc: selectedAltloc,
            backbone_complete: fmBoolean(row.backbone_complete, `${rowLabel}.backbone_complete`),
            backbone_atoms: {
                N: fmSchemaNullableString(atoms.N, `${rowLabel}.backbone_atoms.N`),
                CA: fmSchemaNullableString(atoms.CA, `${rowLabel}.backbone_atoms.CA`),
                C: fmSchemaNullableString(atoms.C, `${rowLabel}.backbone_atoms.C`),
                O: fmSchemaNullableString(atoms.O, `${rowLabel}.backbone_atoms.O`),
            },
            status: row.status as typeof statuses[number],
            reason: fmSchemaNullableString(row.reason, `${rowLabel}.reason`),
        };
    });
    return {
        schema_name: 'frustrampnn_structure_map',
        schema_version: 1,
        target_id: fmString(payload.target_id, `${label}.target_id`),
        parent_job_id: fmString(payload.parent_job_id, `${label}.parent_job_id`),
        candidate_id: fmString(payload.candidate_id, `${label}.candidate_id`),
        source_format: payload.source_format,
        source_sha256: fmSha256(payload.source_sha256, `${label}.source_sha256`),
        source_bytes: fmInteger(payload.source_bytes, `${label}.source_bytes`, 1),
        identity_authority: payload.identity_authority as typeof identityAuthorities[number],
        identity_domain: payload.identity_domain,
        authority_artifact_sha256: fmSha256(payload.authority_artifact_sha256, `${label}.authority_artifact_sha256`),
        normalized_pdb_sha256: fmSha256(payload.normalized_pdb_sha256, `${label}.normalized_pdb_sha256`),
        selected_source_model: fmInteger(payload.selected_source_model, `${label}.selected_source_model`, 1),
        altloc_policy: altlocPolicy,
        normalizer_version: 'frustrampnn_structure_normalizer_v1',
        model_ready_sequence: modelReadySequence,
        model_ready_sequence_sha256: fmSha256(payload.model_ready_sequence_sha256, `${label}.model_ready_sequence_sha256`),
        excluded_records: excludedRecords,
        rows,
    };
};

export const fetchFrustraMpnnStructureMap = async (
    downloadUrl: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnStructureMap> => parseFrustraMpnnStructureMap((
    await api.get<unknown>(downloadUrl, { signal })
).data);

export const fetchFrustraMpnnComparison = async (
    referenceJobId: string,
    referenceInvocationId: string,
    targetJobId: string,
    targetInvocationId: string,
    allowIncompatible = false,
    signal?: AbortSignal,
): Promise<FrustraMpnnComparison> => {
    const response = await api.post<unknown>('/api/frustrampnn/comparisons', {
        reference_job_id: referenceJobId,
        reference_invocation_id: referenceInvocationId,
        target_job_id: targetJobId,
        target_invocation_id: targetInvocationId,
        allow_incompatible: allowIncompatible,
    }, { signal });
    return parseFrustraMpnnComparison(response.data);
};

export const createFrustraMpnnMultiComparison = async (
    reference: FrustraMpnnResultReference,
    targets: readonly FrustraMpnnResultReference[],
    allowIncompatible = false,
    signal?: AbortSignal,
): Promise<FrustraMpnnMultiComparison> => {
    if (targets.length < 1 || targets.length > FRUSTRAMPNN_MULTI_TARGET_LIMIT) {
        throw new Error(`FrustraMPNN multi comparison requires 1-${FRUSTRAMPNN_MULTI_TARGET_LIMIT} ordered targets`);
    }
    const keys = new Set<string>();
    const referenceKey = `${reference.parent_job_id}\u0000${reference.invocation_id}`;
    const normalizedTargets = targets.map((target) => {
        const key = `${target.parent_job_id}\u0000${target.invocation_id}`;
        if (key === referenceKey) throw new Error('The reference result cannot also be a multi-comparison target');
        if (keys.has(key)) throw new Error('FrustraMPNN multi-comparison targets must be unique');
        keys.add(key);
        return {
            reference_job_id: reference.parent_job_id,
            reference_invocation_id: reference.invocation_id,
            target_job_id: target.parent_job_id,
            target_invocation_id: target.invocation_id,
        };
    });
    const response = await api.post<unknown>('/api/frustrampnn/comparisons/multi', {
        reference_job_id: reference.parent_job_id,
        reference_invocation_id: reference.invocation_id,
        targets: normalizedTargets,
        allow_incompatible: allowIncompatible,
    }, { signal });
    const comparison = parseFrustraMpnnComparison(response.data);
    if (comparison.schema_name !== 'frustrampnn_multistate_comparison') {
        throw new Error('Multi-comparison endpoint returned an unexpected pair contract');
    }
    return comparison;
};

export const parseFrustraMpnnComparisonRowsPage = (value: unknown): FrustraMpnnComparisonRowsPage => {
    const label = 'comparison rows page';
    const keys = ['comparison_id', 'items', 'total', 'limit', 'offset', 'next_offset'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    if (!Array.isArray(payload.items)) throw new Error(`${label}.items must be an array`);
    const total = fmInteger(payload.total, `${label}.total`, 0);
    const limit = fmInteger(payload.limit, `${label}.limit`, 1);
    const offset = fmInteger(payload.offset, `${label}.offset`, 0);
    const nextOffset = payload.next_offset === null ? null : fmInteger(payload.next_offset, `${label}.next_offset`, 0);
    if (limit > 5000 || payload.items.length > limit || offset + payload.items.length > total) throw new Error(`${label} pagination is invalid`);
    const expectedNextOffset = offset + payload.items.length < total ? offset + payload.items.length : null;
    if (nextOffset !== expectedNextOffset) throw new Error(`${label} pagination is inconsistent`);
    const items = payload.items.map((item, index) => {
        const row = fmRecord(item, `${label}.items[${index}]`);
        if ('target' in row && !('targets' in row)) return { ...parsePairComparisonRow(row, index), kind: 'pair' as const };
        if ('targets' in row && !('target' in row)) return { ...parseMultiComparisonRow(row, index), kind: 'multi' as const };
        throw new Error(`${label}.items[${index}] has no valid pair/multi discriminant`);
    });
    return {
        comparison_id: fmString(payload.comparison_id, `${label}.comparison_id`),
        items,
        total,
        limit,
        offset,
        next_offset: nextOffset,
    };
};

export const fetchFrustraMpnnComparisonById = async (
    comparisonId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnComparison> => (
    await api.get<FrustraMpnnComparison>(`/api/frustrampnn/comparisons/${encodeURIComponent(comparisonId)}`, { signal })
).data;

export const fetchFrustraMpnnComparisonRows = async (
    comparisonId: string,
    limit = 5000,
    offset = 0,
    signal?: AbortSignal,
): Promise<FrustraMpnnComparisonRowsPage> => {
    const response = await api.get<unknown>(`/api/frustrampnn/comparisons/${encodeURIComponent(comparisonId)}/rows`, {
        params: { limit, offset }, signal,
    });
    return parseFrustraMpnnComparisonRowsPage(response.data);
};

export interface FrustraMpnnGuidanceResidueRequest {
    entity_instance_id?: string | null;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code?: string;
}

export type FrustraMpnnGuidanceRegionRequest =
    | { region_type: 'residue_set'; residues: FrustraMpnnGuidanceResidueRequest[] }
    | { region_type: 'sequence_span'; start: number; end: number; auth_asym_id?: string | null }
    | {
        region_type: 'pocket' | 'interface' | 'contact_set' | 'loop' | 'domain' | 'mapped_region';
        residues: FrustraMpnnGuidanceResidueRequest[];
        mapping_method: string;
        source_artifact_sha256?: string | null;
        mapping_artifact_sha256?: string | null;
    };

export type FrustraMpnnGuidanceObjectiveRequest =
    | {
        objective_type: 'score_aggregate';
        direction: 'higher_is_better' | 'lower_is_better';
        aggregation?: 'mean' | 'median' | 'min' | 'max';
        target_class?: 'high' | 'neutral' | 'minimal' | null;
        reference_class?: 'high' | 'neutral' | 'minimal' | null;
    }
    | {
        objective_type: 'class_count' | 'class_transition';
        direction: 'higher_is_better' | 'lower_is_better';
        aggregation?: 'mean' | 'median' | 'min' | 'max';
        target_class: 'high' | 'neutral' | 'minimal';
        reference_class?: 'high' | 'neutral' | 'minimal' | null;
    };

export interface FrustraMpnnGuidanceRequest {
    source_job_id: string;
    source_invocation_id: string;
    region: FrustraMpnnGuidanceRegionRequest;
    objective: FrustraMpnnGuidanceObjectiveRequest;
    constraints?: { prohibited_mutations?: string[] };
    ranking?: { mode?: 'lexicographic'; tie_break?: 'sequence_index_then_mutation' | null };
    rationale: string;
    guidance_id?: string;
}

export const parseFrustraMpnnGuidance = (value: unknown): FrustraMpnnGuidancePlan => {
    const label = 'FrustraMPNN guidance';
    const keys = [
        'schema_name', 'schema_version', 'guidance_id', 'guidance_sha256', 'source_landscape_sha256',
        'configuration_id', 'configuration_sha256', 'region', 'objective', 'constraints', 'ranking',
        'ranked_slots', 'rationale', 'decision_support_only', 'instrument_control', 'observed_outcome',
        'persisted', 'created_at',
    ] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    if (payload.schema_name !== 'frustrampnn_guidance' || payload.schema_version !== 1
        || payload.decision_support_only !== true || payload.instrument_control !== false
        || payload.observed_outcome !== null || payload.persisted !== true) throw new Error(`${label} closed identity is invalid`);

    const regionKeys = [
        'region_type', 'requested_residues', 'resolved_residues', 'unresolved_residues', 'region_sha256',
        'mapping_method', 'source_artifact_sha256', 'mapping_artifact_sha256', 'start', 'end',
    ] as const;
    const region = fmClosedProjection(payload.region, `${label}.region`, regionKeys, regionKeys);
    const regionTypes = ['residue_set', 'sequence_span', 'pocket', 'interface', 'contact_set', 'loop', 'domain', 'mapped_region'] as const;
    if (!regionTypes.includes(region.region_type as typeof regionTypes[number])) throw new Error(`${label}.region.region_type is invalid`);
    if (!Array.isArray(region.requested_residues) || !Array.isArray(region.resolved_residues) || !Array.isArray(region.unresolved_residues)) throw new Error(`${label}.region residue collections must be arrays`);
    const parseRegionResidue = (item: unknown, index: number, collection: 'requested_residues' | 'resolved_residues' | 'unresolved_residues') => {
        const itemLabel = `${label}.region.${collection}[${index}]`;
        const residueKeys = collection === 'requested_residues'
            ? ['entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code'] as const
            : ['auth_asym_id', 'auth_seq_id', 'insertion_code'] as const;
        const row = fmClosedProjection(item, itemLabel, residueKeys, residueKeys);
        return {
            ...(collection === 'requested_residues' ? { entity_instance_id: fmSchemaNullableString(row.entity_instance_id, `${itemLabel}.entity_instance_id`) } : {}),
            auth_asym_id: fmString(row.auth_asym_id, `${itemLabel}.auth_asym_id`),
            auth_seq_id: fmInteger(row.auth_seq_id, `${itemLabel}.auth_seq_id`),
            insertion_code: (() => {
                const insertionCode = fmString(row.insertion_code, `${itemLabel}.insertion_code`, true);
                if (insertionCode.length > 1) throw new Error(`${itemLabel}.insertion_code is invalid`);
                return insertionCode;
            })(),
        };
    };

    const objective = fmClosedProjection(payload.objective, `${label}.objective`, ['objective_type', 'direction', 'aggregation', 'target_class', 'reference_class'], ['objective_type', 'direction', 'aggregation', 'target_class', 'reference_class']);
    if (objective.objective_type !== 'score_aggregate' && objective.objective_type !== 'class_count' && objective.objective_type !== 'class_transition') throw new Error(`${label}.objective.objective_type is invalid`);
    if (objective.direction !== 'higher_is_better' && objective.direction !== 'lower_is_better') throw new Error(`${label}.objective.direction is invalid`);
    if (objective.aggregation !== 'mean' && objective.aggregation !== 'median' && objective.aggregation !== 'min' && objective.aggregation !== 'max') throw new Error(`${label}.objective.aggregation is invalid`);

    const constraints = fmClosedProjection(payload.constraints, `${label}.constraints`, ['prohibited_mutations'], ['prohibited_mutations']);
    const prohibitedMutations = fmStringArray(constraints.prohibited_mutations, `${label}.constraints.prohibited_mutations`);
    const ranking = fmClosedProjection(payload.ranking, `${label}.ranking`, ['mode', 'tie_break'], ['mode', 'tie_break']);
    if (ranking.mode !== 'lexicographic' || (ranking.tie_break !== null && ranking.tie_break !== 'sequence_index_then_mutation')) throw new Error(`${label}.ranking is invalid`);
    if (!Array.isArray(payload.ranked_slots)) throw new Error(`${label}.ranked_slots must be an array`);
    const rankedSlots = payload.ranked_slots.map((item, index) => {
        const itemLabel = `${label}.ranked_slots[${index}]`;
        const slotKeys = ['entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt', 'mutation_aa', 'score', 'class', 'scoreable', 'rationale', 'rank'] as const;
        const slot = fmClosedProjection(item, itemLabel, slotKeys, slotKeys);
        const mutation = fmString(slot.mutation_aa, `${itemLabel}.mutation_aa`);
        const wt = fmSchemaNullableString(slot.wt, `${itemLabel}.wt`);
        if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(mutation) || (wt !== null && !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(wt)) || slot.scoreable !== true) throw new Error(`${itemLabel} biological identity is invalid`);
        const insertionCode = fmString(slot.insertion_code, `${itemLabel}.insertion_code`, true);
        if (insertionCode.length > 1) throw new Error(`${itemLabel}.insertion_code is invalid`);
        return {
            entity_instance_id: fmSchemaNullableString(slot.entity_instance_id, `${itemLabel}.entity_instance_id`),
            auth_asym_id: fmString(slot.auth_asym_id, `${itemLabel}.auth_asym_id`),
            auth_seq_id: fmInteger(slot.auth_seq_id, `${itemLabel}.auth_seq_id`),
            insertion_code: insertionCode,
            sequence_index: fmInteger(slot.sequence_index, `${itemLabel}.sequence_index`, 1),
            wt,
            mutation_aa: mutation,
            score: fmFinite(slot.score, `${itemLabel}.score`),
            class: requireCanonicalClass(slot.class),
            scoreable: true as const,
            rationale: fmString(slot.rationale, `${itemLabel}.rationale`),
            rank: fmInteger(slot.rank, `${itemLabel}.rank`, 1),
        };
    });
    return {
        schema_name: 'frustrampnn_guidance', schema_version: 1,
        guidance_id: fmString(payload.guidance_id, `${label}.guidance_id`),
        guidance_sha256: fmSha256(payload.guidance_sha256, `${label}.guidance_sha256`),
        source_landscape_sha256: fmSha256(payload.source_landscape_sha256, `${label}.source_landscape_sha256`),
        configuration_id: fmString(payload.configuration_id, `${label}.configuration_id`),
        configuration_sha256: fmSha256(payload.configuration_sha256, `${label}.configuration_sha256`),
        region: {
            region_type: region.region_type as typeof regionTypes[number],
            requested_residues: region.requested_residues.map((item, index) => parseRegionResidue(item, index, 'requested_residues') as FrustraMpnnGuidancePlan['region']['requested_residues'][number]),
            resolved_residues: region.resolved_residues.map((item, index) => parseRegionResidue(item, index, 'resolved_residues')),
            unresolved_residues: region.unresolved_residues.map((item, index) => parseRegionResidue(item, index, 'unresolved_residues')),
            region_sha256: fmSha256(region.region_sha256, `${label}.region.region_sha256`),
            mapping_method: fmSchemaNullableString(region.mapping_method, `${label}.region.mapping_method`),
            source_artifact_sha256: fmOptionalSha256(region.source_artifact_sha256, `${label}.region.source_artifact_sha256`),
            mapping_artifact_sha256: fmOptionalSha256(region.mapping_artifact_sha256, `${label}.region.mapping_artifact_sha256`),
            start: fmOptionalInteger(region.start, `${label}.region.start`),
            end: fmOptionalInteger(region.end, `${label}.region.end`),
        },
        objective: {
            objective_type: objective.objective_type,
            direction: objective.direction,
            aggregation: objective.aggregation,
            target_class: requireCanonicalClass(objective.target_class),
            reference_class: requireCanonicalClass(objective.reference_class),
        },
        constraints: { prohibited_mutations: prohibitedMutations },
        ranking: { mode: 'lexicographic', tie_break: ranking.tie_break as FrustraMpnnGuidancePlan['ranking']['tie_break'] },
        ranked_slots: rankedSlots,
        rationale: fmString(payload.rationale, `${label}.rationale`),
        decision_support_only: true, instrument_control: false, observed_outcome: null, persisted: true,
        created_at: fmString(payload.created_at, `${label}.created_at`),
    };
};

const normalizeFrustraMpnnGuidanceRequest = (value: FrustraMpnnGuidanceRequest): FrustraMpnnGuidanceRequest => {
    const payload = fmClosedProjection(value, 'FrustraMPNN guidance request', ['source_job_id', 'source_invocation_id', 'region', 'objective', 'constraints', 'ranking', 'rationale', 'guidance_id'], ['source_job_id', 'source_invocation_id', 'region', 'objective', 'rationale']);
    const parseResidues = (input: unknown, label: string): FrustraMpnnGuidanceResidueRequest[] => {
        if (!Array.isArray(input) || input.length < 1) throw new Error(`${label} must contain at least one residue`);
        return input.map((item, index) => {
            const rowLabel = `${label}[${index}]`;
            const row = fmClosedProjection(item, rowLabel, ['entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code'], ['auth_asym_id', 'auth_seq_id']);
            const insertionCode = row.insertion_code === undefined ? '' : fmString(row.insertion_code, `${rowLabel}.insertion_code`, true);
            if (insertionCode.length > 1) throw new Error(`${rowLabel}.insertion_code is invalid`);
            return {
                ...(row.entity_instance_id === undefined ? {} : { entity_instance_id: fmSchemaNullableString(row.entity_instance_id, `${rowLabel}.entity_instance_id`) }),
                auth_asym_id: fmString(row.auth_asym_id, `${rowLabel}.auth_asym_id`),
                auth_seq_id: fmInteger(row.auth_seq_id, `${rowLabel}.auth_seq_id`),
                insertion_code: insertionCode,
            };
        });
    };
    const regionRecord = fmRecord(payload.region, 'FrustraMPNN guidance request.region');
    const regionType = fmString(regionRecord.region_type, 'FrustraMPNN guidance request.region.region_type');
    let region: FrustraMpnnGuidanceRegionRequest;
    if (regionType === 'residue_set') {
        const row = fmClosedProjection(regionRecord, 'FrustraMPNN guidance request.region', ['region_type', 'residues'], ['region_type', 'residues']);
        region = { region_type: 'residue_set', residues: parseResidues(row.residues, 'FrustraMPNN guidance request.region.residues') };
    } else if (regionType === 'sequence_span') {
        const row = fmClosedProjection(regionRecord, 'FrustraMPNN guidance request.region', ['region_type', 'start', 'end', 'auth_asym_id'], ['region_type', 'start', 'end']);
        const start = fmInteger(row.start, 'FrustraMPNN guidance request.region.start', 1);
        const end = fmInteger(row.end, 'FrustraMPNN guidance request.region.end', 1);
        if (start > end) throw new Error('FrustraMPNN guidance sequence span is invalid');
        region = { region_type: 'sequence_span', start, end, ...(row.auth_asym_id === undefined ? {} : { auth_asym_id: fmSchemaNullableString(row.auth_asym_id, 'FrustraMPNN guidance request.region.auth_asym_id') }) };
    } else if (['pocket', 'interface', 'contact_set', 'loop', 'domain', 'mapped_region'].includes(regionType)) {
        const row = fmClosedProjection(regionRecord, 'FrustraMPNN guidance request.region', ['region_type', 'residues', 'mapping_method', 'source_artifact_sha256', 'mapping_artifact_sha256'], ['region_type', 'residues', 'mapping_method']);
        const sourceSha = row.source_artifact_sha256 === undefined || row.source_artifact_sha256 === null ? null : fmSha256(row.source_artifact_sha256, 'FrustraMPNN guidance request.region.source_artifact_sha256');
        const mappingSha = row.mapping_artifact_sha256 === undefined || row.mapping_artifact_sha256 === null ? null : fmSha256(row.mapping_artifact_sha256, 'FrustraMPNN guidance request.region.mapping_artifact_sha256');
        if (sourceSha === null && mappingSha === null) throw new Error('FrustraMPNN structural guidance requires source or mapping authority');
        region = { region_type: regionType as 'pocket' | 'interface' | 'contact_set' | 'loop' | 'domain' | 'mapped_region', residues: parseResidues(row.residues, 'FrustraMPNN guidance request.region.residues'), mapping_method: fmString(row.mapping_method, 'FrustraMPNN guidance request.region.mapping_method'), source_artifact_sha256: sourceSha, mapping_artifact_sha256: mappingSha };
    } else throw new Error('FrustraMPNN guidance region_type is invalid');

    const objectiveRecord = fmRecord(payload.objective, 'FrustraMPNN guidance request.objective');
    const objectiveType = fmString(objectiveRecord.objective_type, 'FrustraMPNN guidance request.objective.objective_type');
    const objectiveRequired = objectiveType === 'score_aggregate' ? ['objective_type', 'direction'] as const : ['objective_type', 'direction', 'target_class'] as const;
    const objectiveRow = fmClosedProjection(objectiveRecord, 'FrustraMPNN guidance request.objective', ['objective_type', 'direction', 'aggregation', 'target_class', 'reference_class'], objectiveRequired);
    if (!['score_aggregate', 'class_count', 'class_transition'].includes(objectiveType)) throw new Error('FrustraMPNN guidance objective_type is invalid');
    const direction = fmString(objectiveRow.direction, 'FrustraMPNN guidance request.objective.direction');
    const aggregation = objectiveRow.aggregation === undefined ? 'mean' : fmString(objectiveRow.aggregation, 'FrustraMPNN guidance request.objective.aggregation');
    if (!['higher_is_better', 'lower_is_better'].includes(direction) || !['mean', 'median', 'min', 'max'].includes(aggregation)) throw new Error('FrustraMPNN guidance objective policy is invalid');
    const classValue = (raw: unknown, label: string) => {
        const result = fmSchemaNullableString(raw, label);
        if (result !== null && !['high', 'neutral', 'minimal'].includes(result)) throw new Error(`${label} is invalid`);
        return result as 'high' | 'neutral' | 'minimal' | null;
    };
    const targetClass = objectiveRow.target_class === undefined ? null : classValue(objectiveRow.target_class, 'FrustraMPNN guidance request.objective.target_class');
    const referenceClass = objectiveRow.reference_class === undefined ? null : classValue(objectiveRow.reference_class, 'FrustraMPNN guidance request.objective.reference_class');
    if (objectiveType !== 'score_aggregate' && targetClass === null) throw new Error('FrustraMPNN class guidance requires target_class');
    const objective = { objective_type: objectiveType, direction, aggregation, target_class: targetClass, reference_class: referenceClass } as FrustraMpnnGuidanceObjectiveRequest;

    const constraintsRecord = fmClosedProjection(payload.constraints ?? {}, 'FrustraMPNN guidance request.constraints', ['prohibited_mutations'], []);
    const prohibitedMutations = constraintsRecord.prohibited_mutations ?? [];
    if (!Array.isArray(prohibitedMutations)) throw new Error('FrustraMPNN guidance prohibited_mutations must be an array');
    const constraints = { prohibited_mutations: prohibitedMutations.map((item, index) => fmString(item, `FrustraMPNN guidance request.constraints.prohibited_mutations[${index}]`)) };
    const rankingRecord = fmClosedProjection(payload.ranking ?? {}, 'FrustraMPNN guidance request.ranking', ['mode', 'tie_break'], []);
    const rankingMode = rankingRecord.mode === undefined ? 'lexicographic' : fmString(rankingRecord.mode, 'FrustraMPNN guidance request.ranking.mode');
    const tieBreak = rankingRecord.tie_break === undefined ? null : fmSchemaNullableString(rankingRecord.tie_break, 'FrustraMPNN guidance request.ranking.tie_break');
    if (rankingMode !== 'lexicographic' || (tieBreak !== null && tieBreak !== 'sequence_index_then_mutation')) throw new Error('FrustraMPNN guidance ranking is invalid');
    return {
        source_job_id: fmString(payload.source_job_id, 'FrustraMPNN guidance request.source_job_id'),
        source_invocation_id: fmString(payload.source_invocation_id, 'FrustraMPNN guidance request.source_invocation_id'),
        region,
        objective,
        constraints,
        ranking: { mode: 'lexicographic', tie_break: tieBreak as 'sequence_index_then_mutation' | null },
        rationale: fmString(payload.rationale, 'FrustraMPNN guidance request.rationale'),
        ...(payload.guidance_id === undefined ? {} : { guidance_id: fmString(payload.guidance_id, 'FrustraMPNN guidance request.guidance_id') }),
    };
};

export const createFrustraMpnnGuidance = async (
    request: FrustraMpnnGuidanceRequest,
    signal?: AbortSignal,
): Promise<FrustraMpnnGuidancePlan> => parseFrustraMpnnGuidance((
    await api.post<unknown>('/api/frustrampnn/guidance', normalizeFrustraMpnnGuidanceRequest(request), { signal })
).data);

export const fetchFrustraMpnnGuidance = async (
    guidanceId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnGuidancePlan> => parseFrustraMpnnGuidance((
    await api.get<unknown>(`/api/frustrampnn/guidance/${encodeURIComponent(guidanceId)}`, { signal })
).data);

export interface FrustraMpnnCandidateHandoffRequest {
    candidate_id: string;
    producer_id: string;
    parent_job_id: string;
    parent_invocation_id: string;
    parent_landscape_sha256: string;
    guidance_id?: string;
    nucleotide_edit_set?: [];
    protein_sequence_sha256?: string;
    expected_structure_sha256?: string;
    frustrampnn_settings: FrustraMpnnRequestedSettings;
}

export const handoffFrustraMpnnCandidate = async (
    file: File,
    request: FrustraMpnnCandidateHandoffRequest,
    signal?: AbortSignal,
): Promise<FrustraMpnnChildReceipt> => {
    const form = new FormData();
    form.append('structure_file', file);
    for (const [key, value] of Object.entries(request)) {
        if (value == null) continue;
        const normalizedValue = key === 'frustrampnn_settings'
            ? parseFrustraMpnnRequestedSettings(value)
            : value;
        form.append(key, typeof normalizedValue === 'string' ? normalizedValue : JSON.stringify(normalizedValue));
    }
    return parseFrustraMpnnChildReceipt((await api.post<unknown>('/api/frustrampnn/candidates/handoff', form, { signal })).data, true);
};

export const fetchFrustraMpnnMultidimensionalPoints = async (
    datasetIds: string[] = [],
    limit = 1000,
    signal?: AbortSignal,
): Promise<FrustraMpnnResultPage> => {
    const response = await api.get<unknown>('/api/frustrampnn/analytics/points', {
        params: {
            level: 'result',
            limit,
            ...(datasetIds.length > 0 ? { dataset_ids: datasetIds.join(',') } : {}),
        },
        signal,
    });
    const page = parseFrustraMpnnMultidimensionalPage(response.data);
    if (page.level !== 'result') throw new Error('FrustraMPNN result analytics endpoint returned a cross-level contract');
    return page;
};

export interface FrustraMpnnSavedReviewWrite {
    title: string;
    notes: string;
    result_references: Array<{ parent_job_id: string; invocation_id: string }>;
    selected_residues: Array<{ auth_asym_id: string; auth_seq_id: string; insertion_code: string }>;
    filters: Record<string, string | number | boolean | null>;
    viewer_state: {
        active_metric_id: string;
        landscape_offset: number;
        metric_workbench_open: boolean;
        chart_x_axis: string;
        chart_y_axis: string;
        structure_camera: StructureCameraState | null;
        structure_representations: StructureRepresentationState[];
        structure_layers: StructureLayerState[];
    };
    tags: string[];
    supersedes_review_id: string | null;
}

export interface FrustraMpnnSavedReview extends FrustraMpnnSavedReviewWrite {
    schema_name: 'frustrampnn_saved_review';
    schema_version: 1;
    review_id: string;
    parent_job_id: string;
    invocation_id: string;
    landscape_sha256: string;
    effective_settings_sha256: string;
    review_sha256: string;
    created_at: string;
}

const parseReviewScalarRecord = (value: unknown, label: string): Record<string, string | number | boolean | null> => {
    const record = fmRecord(value, label);
    for (const [key, item] of Object.entries(record)) {
        if (item !== null && typeof item !== 'string' && typeof item !== 'boolean'
            && (typeof item !== 'number' || !Number.isFinite(item))) {
            throw new Error(`${label}.${key} must be a finite scalar`);
        }
    }
    return record as Record<string, string | number | boolean | null>;
};

const parseReviewViewerState = (value: unknown, label: string): FrustraMpnnSavedReviewWrite['viewer_state'] => {
    const keys = ['active_metric_id', 'landscape_offset', 'metric_workbench_open', 'chart_x_axis', 'chart_y_axis', 'structure_camera', 'structure_representations', 'structure_layers'] as const;
    const row = fmClosedProjection(value, label, keys, keys);
    const vector = (item: unknown, itemLabel: string): [number, number, number] => {
        if (!Array.isArray(item) || item.length !== 3) throw new Error(`${itemLabel} must contain exactly three coordinates`);
        return [fmFinite(item[0], `${itemLabel}[0]`), fmFinite(item[1], `${itemLabel}[1]`), fmFinite(item[2], `${itemLabel}[2]`)];
    };
    let camera: StructureCameraState | null = null;
    if (row.structure_camera !== null) {
        const item = fmClosedProjection(row.structure_camera, `${label}.structure_camera`, ['mode', 'target', 'position', 'up', 'radius'], ['mode']);
        if (item.mode !== 'perspective' && item.mode !== 'orthographic') throw new Error(`${label}.structure_camera.mode is invalid`);
        const radius = item.radius === undefined ? undefined : fmFinite(item.radius, `${label}.structure_camera.radius`);
        if (radius !== undefined && radius <= 0) throw new Error(`${label}.structure_camera.radius must be positive`);
        camera = {
            mode: item.mode,
            ...(item.target === undefined ? {} : { target: vector(item.target, `${label}.structure_camera.target`) }),
            ...(item.position === undefined ? {} : { position: vector(item.position, `${label}.structure_camera.position`) }),
            ...(item.up === undefined ? {} : { up: vector(item.up, `${label}.structure_camera.up`) }),
            ...(radius === undefined ? {} : { radius }),
        };
    }
    if (!Array.isArray(row.structure_representations) || !Array.isArray(row.structure_layers)) throw new Error(`${label} structure collections must be arrays`);
    const representationKinds = new Set(['cartoon', 'surface', 'ball-and-stick', 'spacefill', 'line', 'gaussian-surface']);
    const representations = row.structure_representations.map((entry, index): StructureRepresentationState => {
        const itemLabel = `${label}.structure_representations[${index}]`;
        const item = fmClosedProjection(entry, itemLabel, ['representationId', 'documentId', 'kind', 'visible', 'opacity', 'selectionSetId'], ['representationId', 'documentId', 'kind', 'visible', 'opacity']);
        const kind = fmString(item.kind, `${itemLabel}.kind`);
        if (!representationKinds.has(kind)) throw new Error(`${itemLabel}.kind is invalid`);
        const opacity = fmFinite(item.opacity, `${itemLabel}.opacity`);
        if (opacity < 0 || opacity > 1) throw new Error(`${itemLabel}.opacity must be between 0 and 1`);
        return { representationId: fmString(item.representationId, `${itemLabel}.representationId`), documentId: fmString(item.documentId, `${itemLabel}.documentId`), kind: kind as StructureRepresentationState['kind'], visible: fmBoolean(item.visible, `${itemLabel}.visible`), opacity, ...(item.selectionSetId === undefined ? {} : { selectionSetId: fmString(item.selectionSetId, `${itemLabel}.selectionSetId`) }) };
    });
    const layers = row.structure_layers.map((entry, index): StructureLayerState => {
        const itemLabel = `${label}.structure_layers[${index}]`;
        const item = fmClosedProjection(entry, itemLabel, ['layerId', 'metricId', 'selectionSetId', 'visible', 'opacity', 'order', 'palette'], ['layerId', 'visible', 'opacity', 'order']);
        const opacity = fmFinite(item.opacity, `${itemLabel}.opacity`);
        if (opacity < 0 || opacity > 1) throw new Error(`${itemLabel}.opacity must be between 0 and 1`);
        return { layerId: fmString(item.layerId, `${itemLabel}.layerId`), visible: fmBoolean(item.visible, `${itemLabel}.visible`), opacity, order: fmInteger(item.order, `${itemLabel}.order`, 0), ...(item.metricId === undefined ? {} : { metricId: fmString(item.metricId, `${itemLabel}.metricId`) }), ...(item.selectionSetId === undefined ? {} : { selectionSetId: fmString(item.selectionSetId, `${itemLabel}.selectionSetId`) }), ...(item.palette === undefined ? {} : { palette: fmString(item.palette, `${itemLabel}.palette`) }) };
    });
    return {
        active_metric_id: fmString(row.active_metric_id, `${label}.active_metric_id`),
        landscape_offset: fmInteger(row.landscape_offset, `${label}.landscape_offset`, 0),
        metric_workbench_open: fmBoolean(row.metric_workbench_open, `${label}.metric_workbench_open`),
        chart_x_axis: fmString(row.chart_x_axis, `${label}.chart_x_axis`),
        chart_y_axis: fmString(row.chart_y_axis, `${label}.chart_y_axis`),
        structure_camera: camera,
        structure_representations: representations,
        structure_layers: layers,
    };
};

export const parseFrustraMpnnSavedReview = (value: unknown): FrustraMpnnSavedReview => {
    const label = 'FrustraMPNN saved review';
    const keys = ['schema_name', 'schema_version', 'review_id', 'parent_job_id', 'invocation_id', 'landscape_sha256', 'effective_settings_sha256', 'review_sha256', 'supersedes_review_id', 'title', 'notes', 'result_references', 'selected_residues', 'filters', 'viewer_state', 'tags', 'created_at'] as const;
    const payload = fmClosedProjection(value, label, keys, keys);
    if (payload.schema_name !== 'frustrampnn_saved_review' || payload.schema_version !== 1) {
        throw new Error(`${label} identity is invalid`);
    }
    if (!Array.isArray(payload.result_references) || payload.result_references.length < 1) throw new Error(`${label}.result_references is invalid`);
    if (!Array.isArray(payload.selected_residues)) throw new Error(`${label}.selected_residues is invalid`);
    const references = payload.result_references.map((item, index) => {
        const row = fmClosedProjection(item, `${label}.result_references[${index}]`, ['parent_job_id', 'invocation_id'], ['parent_job_id', 'invocation_id']);
        return { parent_job_id: fmString(row.parent_job_id, `${label}.result_references[${index}].parent_job_id`), invocation_id: fmString(row.invocation_id, `${label}.result_references[${index}].invocation_id`) };
    });
    const residues = payload.selected_residues.map((item, index) => {
        const row = fmClosedProjection(item, `${label}.selected_residues[${index}]`, ['auth_asym_id', 'auth_seq_id', 'insertion_code'], ['auth_asym_id', 'auth_seq_id', 'insertion_code']);
        return { auth_asym_id: fmString(row.auth_asym_id, `${label}.selected_residues[${index}].auth_asym_id`), auth_seq_id: fmString(row.auth_seq_id, `${label}.selected_residues[${index}].auth_seq_id`), insertion_code: fmString(row.insertion_code, `${label}.selected_residues[${index}].insertion_code`, true) };
    });
    return {
        schema_name: 'frustrampnn_saved_review', schema_version: 1,
        review_id: fmString(payload.review_id, `${label}.review_id`),
        parent_job_id: fmString(payload.parent_job_id, `${label}.parent_job_id`),
        title: fmString(payload.title, `${label}.title`),
        notes: fmString(payload.notes, `${label}.notes`, true),
        result_references: references,
        selected_residues: residues,
        filters: parseReviewScalarRecord(payload.filters, `${label}.filters`),
        viewer_state: parseReviewViewerState(payload.viewer_state, `${label}.viewer_state`),
        tags: fmStringArray(payload.tags, `${label}.tags`),
        supersedes_review_id: payload.supersedes_review_id === null ? null : fmString(payload.supersedes_review_id, `${label}.supersedes_review_id`),
        invocation_id: fmString(payload.invocation_id, `${label}.invocation_id`),
        landscape_sha256: fmString(payload.landscape_sha256, `${label}.landscape_sha256`),
        effective_settings_sha256: fmString(payload.effective_settings_sha256, `${label}.effective_settings_sha256`),
        review_sha256: fmString(payload.review_sha256, `${label}.review_sha256`),
        created_at: fmString(payload.created_at, `${label}.created_at`),
    };
};

export interface FrustraMpnnSavedReviewList {
    items: FrustraMpnnSavedReview[];
    next_offset: number | null;
}

export const listFrustraMpnnSavedReviewPage = async (
    jobId: string,
    offset = 0,
    signal?: AbortSignal,
): Promise<FrustraMpnnSavedReviewList> => {
    const value = (await api.get<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/reviews`,
        { params: { limit: 100, offset }, signal },
    )).data;
    const payload = fmClosedProjection(value, 'FrustraMPNN saved review list', ['schema_name', 'schema_version', 'items', 'next_offset'], ['schema_name', 'schema_version', 'items', 'next_offset']);
    if (payload.schema_name !== 'frustrampnn_saved_review_list' || payload.schema_version !== 1 || !Array.isArray(payload.items)) throw new Error('FrustraMPNN saved review list identity is invalid');
    const nextOffset = payload.next_offset === null ? null : fmInteger(payload.next_offset, 'FrustraMPNN saved review list.next_offset', 0);
    return { items: payload.items.map(parseFrustraMpnnSavedReview), next_offset: nextOffset };
};

export const listFrustraMpnnSavedReviews = async (
    jobId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnSavedReview[]> => {
    const reviews: FrustraMpnnSavedReview[] = [];
    let offset = 0;
    for (;;) {
        const page = await listFrustraMpnnSavedReviewPage(jobId, offset, signal);
        reviews.push(...page.items);
        if (page.next_offset === null) return reviews;
        if (page.next_offset <= offset || reviews.length > 10_000) throw new Error('FrustraMPNN saved review pagination is invalid');
        offset = page.next_offset;
    }
};

export const createFrustraMpnnSavedReview = async (
    jobId: string,
    payload: FrustraMpnnSavedReviewWrite,
): Promise<FrustraMpnnSavedReview> => parseFrustraMpnnSavedReview((
    await api.post<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/reviews`,
        payload,
    )
).data);

export const updateFrustraMpnnSavedReview = async (
    jobId: string,
    reviewId: string,
    payload: FrustraMpnnSavedReviewWrite,
): Promise<FrustraMpnnSavedReview> => parseFrustraMpnnSavedReview((
    await api.post<unknown>(
        `/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/reviews`,
        { ...payload, supersedes_review_id: reviewId },
    )
).data);

export const deleteFrustraMpnnSavedReview = async (
    jobId: string,
    reviewId: string,
): Promise<void> => {
    await api.delete(`/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/reviews/${encodeURIComponent(reviewId)}`);
};

export interface FrustraMpnnCaptureReceipt {
    schema_name: 'frustrampnn_review_capture_receipt';
    schema_version: 1;
    artifact_id: string;
    review_id: string;
    parent_job_id: string;
    role: 'structure_view_capture';
    media_type: 'image/png';
    content_sha256: string;
    size_bytes: number;
    download_url: string;
}

export const persistFrustraMpnnReviewCapture = async (
    jobId: string,
    reviewId: string,
    png: Blob,
): Promise<FrustraMpnnCaptureReceipt> => {
    const digestBytes = await crypto.subtle.digest('SHA-256', await png.arrayBuffer());
    const expectedSha256 = Array.from(new Uint8Array(digestBytes), (byte) => byte.toString(16).padStart(2, '0')).join('');
    const response = await api.post<FrustraMpnnCaptureReceipt>(
        `/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/reviews/${encodeURIComponent(reviewId)}/captures`,
        png,
        { params: { expected_sha256: expectedSha256 }, headers: { 'Content-Type': 'image/png' } },
    );
    const receipt = response.data;
    if (
        receipt.schema_name !== 'frustrampnn_review_capture_receipt'
        || receipt.schema_version !== 1
        || receipt.review_id !== reviewId
        || receipt.parent_job_id !== jobId
        || receipt.role !== 'structure_view_capture'
        || receipt.media_type !== 'image/png'
        || receipt.content_sha256 !== expectedSha256
        || !Number.isInteger(receipt.size_bytes)
        || receipt.size_bytes !== png.size
        || typeof receipt.artifact_id !== 'string'
        || typeof receipt.download_url !== 'string'
    ) throw new Error('FrustraMPNN review capture receipt is invalid');
    return receipt;
};

export interface FrustraMpnnExportReceipt {
    schema_name: 'frustrampnn_export_receipt';
    schema_version: 1;
    export_id: string;
    parent_job_id: string;
    invocation_id: string;
    format: 'json' | 'csv';
    content_sha256: string;
    row_count: number;
    total_matching_rows: number;
    complete: boolean;
    download_url: string;
}

export const createFrustraMpnnGovernedExport = async (
    jobId: string,
    payload: { review_id: string; invocation_id: string; format: 'json' | 'csv'; limit?: number; auth_asym_id?: string; mutation_aa?: string; status?: string },
): Promise<FrustraMpnnExportReceipt> => (
    await api.post<FrustraMpnnExportReceipt>(`/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/exports`, payload)
).data;
