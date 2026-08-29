import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    cancelDomainRunGroup,
    cloneDomainRunIntent,
    createDomainWorkflowPlan,
    getDomainRunGroup,
    getDomainWorkflowPlan,
    getNgsMolBioBinding,
    initializeNgsMolBioBinding,
    issuePreparedLaunchContext,
    internalRouteHref,
    launchDomainRunGroup,
    listDomainCapabilities,
    listDomainWorkflowPlanRevisions,
    listDomainWorkflowPlans,
    prepareDomainWorkflowPlanRevision,
    projectManagerErrorMessage,
    publishDomainWorkflowPlanRevision,
    reopenDomainResult,
    replaceDomainWorkflowPlanDraft,
    resubmitDomainRunGroup,
    retryDomainRunGroup,
    reverifyNgsMolBioBinding,
    type DomainCapabilityLaunchMode,
    type DomainResultSurface,
    type DomainRunGroup,
    type DomainWorkflowPlanHead,
    type DomainWorkflowPreparation,
    type DomainWorkflowRun,
    type JsonObject,
    type JsonValue,
    type PreparationLaunchRequest,
    type PreparedLaunchContext,
    type RunCloneReceipt,
} from '../../lib/projectManager';
import {
    type DomainStateRevisionPayload,
    fetchMolBioNgsDomainState,
    initializeMolBioNgsDomainState,
    saveMolBioNgsStateRevision,
} from '../../lib/api';
import ExperimentReferenceLinks from './ExperimentReferenceLinks';

interface DomainWorkflowOperatorProps {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
    domainRevisionId: string | null;
    selectedStateRevisionId: string | null;
    currentStateRevisionId: string | null;
    projectReturnUri: string;
    contextHref: (path: string, extras?: Record<string, string | null | undefined>) => string;
    inputDatasetRevisionIds: string[];
    initialRunGroupId?: string | null;
}

const INPUT_CLASS = 'w-full rounded-md border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary';
const BUTTON_CLASS = 'rounded-md border border-border-primary bg-surface-secondary px-3 py-2 text-sm font-medium text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';
const PRIMARY_BUTTON_CLASS = 'rounded-md bg-primary px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';
const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'awaiting_input']);

const EMPTY_DOMAIN_STATE_PAYLOAD: DomainStateRevisionPayload = {
    schema: 'bms.molbio-ngs.domain-state-revision.v1',
    design: {
        sample_revision_ids: [],
        conditions: [],
        replicates: [],
        expected_molecule_roles: [],
    },
    reference_policy: {
        required_roles: [],
        coordinate_policy: 'exact_revision',
    },
    acquisition_policy: {
        platform: 'none',
        required_terminal_manifest: false,
    },
    analysis_policy: {
        allowed_workflow_ids: [],
        required_manifest_schemas: [],
    },
    assessment_policy: {
        rule_id: 'server-owned-rule',
        completion_is_scientific_pass: false,
    },
    notes: 'Initial empty state for a Project Manager governed Domain workflow.',
};

interface SelectedPreparation {
    preparation: DomainWorkflowPreparation;
    planId: string;
    planRevisionId: string;
    launchMode: DomainCapabilityLaunchMode;
    sourceDestination: string;
    authorityKey: string;
}

interface IssuedLaunchHandoff {
    launchContext: PreparedLaunchContext;
    sourceDestination: string;
}

function isDomainCapabilityLaunchMode(value: unknown): value is DomainCapabilityLaunchMode {
    return value === 'typed_launcher_handoff' || value === 'managed_materialization';
}

function preparationSelectionError(
    selections: SelectedPreparation[],
    currentAuthorityKey: string,
): string | null {
    if (selections.length === 0) return 'Prepare and select at least one immutable preparation.';
    const preparationIds = selections.map((selection) => selection.preparation.preparation_id);
    if (new Set(preparationIds).size !== preparationIds.length) {
        return 'Selected preparations must be unique.';
    }
    for (const selection of selections) {
        if (!selection.planId || !selection.planRevisionId) {
            return 'A selected preparation is missing its immutable Plan mapping.';
        }
        if (selection.authorityKey !== currentAuthorityKey) {
            return 'Selected preparations do not share the current exact Domain and state authority.';
        }
        if (selection.preparation.workflow_revision_id !== selection.planRevisionId) {
            return 'A selected preparation no longer matches its immutable Plan revision mapping.';
        }
        if (selection.preparation.status !== 'valid') {
            return `Preparation ${selection.preparation.preparation_id} is not valid.`;
        }
        if (!isDomainCapabilityLaunchMode(selection.launchMode)) {
            return `Preparation ${selection.preparation.preparation_id} has no explicit supported launch mode.`;
        }
        if (
            selection.launchMode === 'typed_launcher_handoff'
            && (!selection.sourceDestination.startsWith('/') || selection.sourceDestination.startsWith('//'))
        ) {
            return `Preparation ${selection.preparation.preparation_id} has no safe canonical native launcher destination.`;
        }
    }
    return null;
}

function eligibleFailedRuns(group: DomainRunGroup | undefined): DomainWorkflowRun[] {
    if (!group || group.state !== 'failed') return [];
    return group.runs.filter((run) => {
        const latestAttempt = run.attempts[run.attempts.length - 1];
        return run.state === 'failed' && latestAttempt?.state === 'failed';
    });
}

function retryPreparationMappingError(
    group: DomainRunGroup | undefined,
    selections: SelectedPreparation[],
    preparationByRunId: Record<string, string>,
    currentAuthorityKey: string,
): string | null {
    const selectionError = preparationSelectionError(selections, currentAuthorityKey);
    if (selectionError) return selectionError;
    if (!group || group.state !== 'failed') return 'Load a reconciled failed Run Group before retrying.';
    const failedRuns = group.runs.filter((run) => run.state === 'failed');
    const eligibleRuns = eligibleFailedRuns(group);
    if (eligibleRuns.length === 0) return 'The loaded Run Group has no eligible failed runs.';
    if (eligibleRuns.length !== failedRuns.length) {
        return 'At least one failed run lacks an exact latest failed-attempt authority.';
    }
    const mappedPreparationIds = eligibleRuns
        .map((run) => preparationByRunId[run.run_id])
        .filter((preparationId): preparationId is string => Boolean(preparationId));
    if (mappedPreparationIds.length !== eligibleRuns.length) {
        return 'Map every eligible failed run to an immutable selected preparation.';
    }
    const selectedPreparationIds = new Set(
        selections.map((selection) => selection.preparation.preparation_id),
    );
    if (mappedPreparationIds.some((preparationId) => !selectedPreparationIds.has(preparationId))) {
        return 'Retry mappings may use only selected immutable preparations.';
    }
    if ([...selectedPreparationIds].some((preparationId) => !mappedPreparationIds.includes(preparationId))) {
        return 'Every selected immutable preparation must be mapped to at least one eligible failed run.';
    }
    return null;
}

function authorityErrorMessage(error: unknown): string {
    if (typeof error === 'string') return error;
    const message = projectManagerErrorMessage(error);
    const normalized = message.toLowerCase();
    if (normalized.includes('stale') || normalized.includes('revision changed') || normalized.includes('generation changed')) {
        return `Stale authority: ${message}. Refresh the immutable selectors before retrying.`;
    }
    if (normalized.includes('replacement_preparation_required')) {
        return 'The binding or Domain revision changed. Prepare the selected immutable Plan revision again before retrying or resubmitting.';
    }
    if (normalized.includes('connector') || normalized.includes('binding')) {
        return `Binding authority unavailable: ${message}`;
    }
    if (normalized.includes('canonical job authority')) {
        return `Canonical Job authority unavailable: ${message}`;
    }
    return message;
}

function ErrorBanner({ error }: { error: unknown }) {
    if (!error) return null;
    return (
        <div role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {authorityErrorMessage(error)}
        </div>
    );
}

function KeyValue({ label, value }: { label: string; value: string | number | null | undefined }) {
    return (
        <div className="min-w-0">
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-content-muted">{label}</dt>
            <dd className="mt-0.5 break-all font-mono text-xs text-content-secondary">{value ?? 'Unavailable'}</dd>
        </div>
    );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
    return (
        <section className="rounded-lg border border-border-primary bg-surface-secondary p-4">
            <h3 className="mb-3 text-sm font-semibold text-content-primary">{title}</h3>
            {children}
        </section>
    );
}

export type ParameterValue = string | number | boolean | ParameterValue[] | null;
export type ParameterValues = Record<string, ParameterValue>;
export type ParameterKind = 'boolean' | 'string' | 'number' | 'integer' | 'array';
export type ParameterUiControl =
    | 'bounded_integer'
    | 'bounded_number'
    | 'checkbox'
    | 'optional_integer'
    | 'read_only'
    | 'select'
    | 'typed_control'
    | 'typed_source_selector';
export type ParameterPrecision =
    | 'boolean'
    | 'exact_utf8_or_enum'
    | 'float64'
    | 'integer'
    | 'ordered_exact_items'
    | 'union_exact';
export type ParameterDefaultPolicy =
    | { kind: 'schema_default'; canonicalText: 'schema_default' }
    | {
        kind: 'required_explicit_or_authority_bound';
        canonicalText: 'required_explicit_or_authority_bound';
    }
    | {
        kind: 'contextual_defaults';
        canonicalText: string;
        entries: Array<{ context: string; value: string | number | boolean | null }>;
    };
export type ParameterPersistedRepresentation = 'requested_and_effective';
export interface ParameterSupportedRuntimeRange {
    canonicalText: string;
    runtimeClause: string;
    biomodstackSourceSha: string;
}

export interface ParameterField {
    name: string;
    label: string;
    description?: string;
    kind: ParameterKind;
    required: boolean;
    nullable: boolean;
    fixedValue?: ParameterValue;
    defaultValue?: ParameterValue;
    enumValues?: string[];
    minimum?: number;
    maximum?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
    item?: Omit<ParameterField, 'name' | 'label' | 'required'>;
    minItems?: number;
    maxItems?: number;
    uniqueItems?: boolean;
    uiControl?: ParameterUiControl;
    units?: string;
    precision?: ParameterPrecision;
    applicability?: string;
    defaultPolicy?: ParameterDefaultPolicy;
    incompatibilities?: string[];
    nativeKey?: string;
    persistedRepresentation?: ParameterPersistedRepresentation;
    reproducibilityEffect?: 'changes_output';
    scientificMeaning?: string;
    supportedRuntimeRange?: ParameterSupportedRuntimeRange;
}

type ParameterSchemaResult =
    | {
        fields: ParameterField[];
        schemaId: string;
        sourcePin: string;
        unknownFieldsPolicy: 'reject_before_preparation';
        error: null;
    }
    | {
        fields: null;
        schemaId: null;
        sourcePin: null;
        unknownFieldsPolicy: null;
        error: string;
    };

const PARAMETER_ANNOTATIONS = [
    'x-bms-applicability',
    'x-bms-default-policy',
    'x-bms-incompatibilities',
    'x-bms-native-key',
    'x-bms-persisted-representation',
    'x-bms-precision',
    'x-bms-reproducibility-effect',
    'x-bms-scientific-meaning',
    'x-bms-supported-runtime-range',
    'x-bms-ui-control',
    'x-bms-units',
];

const SUPPORTED_UI_CONTROLS = new Set<ParameterUiControl>([
    'bounded_integer',
    'bounded_number',
    'checkbox',
    'optional_integer',
    'read_only',
    'select',
    'typed_control',
    'typed_source_selector',
]);
const SUPPORTED_PRECISIONS = new Set<ParameterPrecision>([
    'boolean',
    'exact_utf8_or_enum',
    'float64',
    'integer',
    'ordered_exact_items',
    'union_exact',
]);

function isObject(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function rejectUnknownSchemaKeys(
    schema: Record<string, unknown>,
    allowed: ReadonlySet<string>,
    label: string,
): void {
    const unknown = Object.keys(schema).filter((key) => !allowed.has(key));
    if (unknown.length) throw new Error(`${label} uses unsupported schema fields: ${unknown.join(', ')}`);
}

function annotationValue(
    name: string,
    schema: Record<string, unknown>,
    outerSchema: Record<string, unknown> | null,
    key: string,
): unknown {
    const innerDeclared = Object.prototype.hasOwnProperty.call(schema, key);
    const outerDeclared = Boolean(outerSchema) && Object.prototype.hasOwnProperty.call(outerSchema, key);
    if (innerDeclared && outerDeclared && JSON.stringify(schema[key]) !== JSON.stringify(outerSchema?.[key])) {
        throw new Error(`${name}.${key} diverges between nullable wrapper and value schema`);
    }
    return outerDeclared ? outerSchema?.[key] : schema[key];
}

function canonicalBoundedAnnotationString(value: unknown, label: string, maximumLength: number): string | undefined {
    if (value === undefined) return undefined;
    if (
        typeof value !== 'string'
        || !value
        || value !== value.trim()
        || value.length > maximumLength
        || /[\u0000-\u001f\u007f]/.test(value)
    ) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function parseDefaultPolicy(value: unknown, label: string): ParameterDefaultPolicy | undefined {
    const text = canonicalBoundedAnnotationString(value, label, 512);
    if (text === undefined) return undefined;
    if (text === 'schema_default') return { kind: 'schema_default', canonicalText: text };
    if (text === 'required_explicit_or_authority_bound') {
        return { kind: 'required_explicit_or_authority_bound', canonicalText: text };
    }
    const entries = text.split(';');
    if (entries.length < 2 || entries.length > 16) throw new Error(`${label} is unsupported`);
    const contexts = new Set<string>();
    const parsedEntries = entries.map((entry) => {
        const match = /^([a-z][a-z0-9_.-]{0,63})=([A-Za-z0-9][A-Za-z0-9_.+/-]{0,127})$/.exec(entry);
        if (!match || contexts.has(match[1])) throw new Error(`${label} is invalid`);
        contexts.add(match[1]);
        const rawValue = match[2];
        let parsedValue: string | number | boolean | null = rawValue;
        if (rawValue === 'true') parsedValue = true;
        else if (rawValue === 'false') parsedValue = false;
        else if (rawValue === 'null') parsedValue = null;
        else if (/^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(rawValue)) {
            parsedValue = Number(rawValue);
            if (!Number.isFinite(parsedValue)) throw new Error(`${label} is invalid`);
        }
        return { context: match[1], value: parsedValue };
    });
    return { kind: 'contextual_defaults', canonicalText: text, entries: parsedEntries };
}

function parseNativeKey(value: unknown, label: string): string | undefined {
    const text = canonicalBoundedAnnotationString(value, label, 512);
    if (text !== undefined && !/^[a-z][a-z0-9_]*(?:\+[a-z][a-z0-9_]*)*$/.test(text)) {
        throw new Error(`${label} is invalid`);
    }
    return text;
}

function parseSupportedRuntimeRange(
    value: unknown,
    label: string,
): ParameterSupportedRuntimeRange | undefined {
    const text = canonicalBoundedAnnotationString(value, label, 512);
    if (text === undefined) return undefined;
    const clauses = text.split('; ');
    if (clauses.length !== 2) throw new Error(`${label} is unsupported`);
    const [runtimeClause, sourceClause] = clauses;
    if (!/^[a-z][a-z0-9_+./-]*(?:=[A-Za-z0-9][A-Za-z0-9._+/-]*)?$/.test(runtimeClause)) {
        throw new Error(`${label} runtime clause is invalid`);
    }
    const sourceMatch = /^biomodstack_source=([0-9a-f]{40})$/.exec(sourceClause);
    if (!sourceMatch) throw new Error(`${label} source pin is invalid`);
    return {
        canonicalText: text,
        runtimeClause,
        biomodstackSourceSha: sourceMatch[1],
    };
}

function parseParameterAnnotations(
    name: string,
    schema: Record<string, unknown>,
    outerSchema: Record<string, unknown> | null,
): Pick<ParameterField,
    | 'uiControl'
    | 'units'
    | 'precision'
    | 'applicability'
    | 'defaultPolicy'
    | 'incompatibilities'
    | 'nativeKey'
    | 'persistedRepresentation'
    | 'reproducibilityEffect'
    | 'scientificMeaning'
    | 'supportedRuntimeRange'> {
    const uiControlValue = annotationValue(name, schema, outerSchema, 'x-bms-ui-control');
    if (uiControlValue !== undefined && (
        typeof uiControlValue !== 'string'
        || !SUPPORTED_UI_CONTROLS.has(uiControlValue as ParameterUiControl)
    )) {
        throw new Error(`${name}.x-bms-ui-control is unsupported`);
    }
    const precisionValue = annotationValue(name, schema, outerSchema, 'x-bms-precision');
    if (precisionValue !== undefined && (
        typeof precisionValue !== 'string'
        || !SUPPORTED_PRECISIONS.has(precisionValue as ParameterPrecision)
    )) {
        throw new Error(`${name}.x-bms-precision is unsupported`);
    }
    const persistedRepresentationValue = annotationValue(
        name,
        schema,
        outerSchema,
        'x-bms-persisted-representation',
    );
    if (persistedRepresentationValue !== undefined && persistedRepresentationValue !== 'requested_and_effective') {
        throw new Error(`${name}.x-bms-persisted-representation is unsupported`);
    }
    const reproducibilityValue = annotationValue(
        name,
        schema,
        outerSchema,
        'x-bms-reproducibility-effect',
    );
    if (reproducibilityValue !== undefined && reproducibilityValue !== 'changes_output') {
        throw new Error(`${name}.x-bms-reproducibility-effect is unsupported`);
    }
    const incompatibilityValue = annotationValue(name, schema, outerSchema, 'x-bms-incompatibilities');
    if (incompatibilityValue !== undefined && (
        !Array.isArray(incompatibilityValue)
        || incompatibilityValue.length > 64
        || incompatibilityValue.some((item) => (
            typeof item !== 'string'
            || !item
            || item !== item.trim()
            || item.length > 256
            || /[\u0000-\u001f\u007f]/.test(item)
        ))
        || new Set(incompatibilityValue).size !== incompatibilityValue.length
    )) {
        throw new Error(`${name}.x-bms-incompatibilities is invalid`);
    }
    return {
        uiControl: uiControlValue as ParameterUiControl | undefined,
        units: canonicalBoundedAnnotationString(
            annotationValue(name, schema, outerSchema, 'x-bms-units'),
            `${name}.x-bms-units`,
            64,
        ),
        precision: precisionValue as ParameterPrecision | undefined,
        applicability: canonicalBoundedAnnotationString(
            annotationValue(name, schema, outerSchema, 'x-bms-applicability'),
            `${name}.x-bms-applicability`,
            512,
        ),
        defaultPolicy: parseDefaultPolicy(
            annotationValue(name, schema, outerSchema, 'x-bms-default-policy'),
            `${name}.x-bms-default-policy`,
        ),
        incompatibilities: incompatibilityValue as string[] | undefined,
        nativeKey: parseNativeKey(
            annotationValue(name, schema, outerSchema, 'x-bms-native-key'),
            `${name}.x-bms-native-key`,
        ),
        persistedRepresentation: persistedRepresentationValue as ParameterPersistedRepresentation | undefined,
        reproducibilityEffect: reproducibilityValue as 'changes_output' | undefined,
        scientificMeaning: canonicalBoundedAnnotationString(
            annotationValue(name, schema, outerSchema, 'x-bms-scientific-meaning'),
            `${name}.x-bms-scientific-meaning`,
            512,
        ),
        supportedRuntimeRange: parseSupportedRuntimeRange(
            annotationValue(name, schema, outerSchema, 'x-bms-supported-runtime-range'),
            `${name}.x-bms-supported-runtime-range`,
        ),
    };
}

function validateDeclaredUiControl(field: ParameterField): void {
    const control = field.uiControl;
    if (!control || control === 'typed_control') return;
    if (control === 'checkbox' && field.kind !== 'boolean') {
        throw new Error(`${field.name}.x-bms-ui-control checkbox requires a boolean`);
    }
    if ((control === 'select' || control === 'typed_source_selector') && (
        field.kind !== 'string' || !field.enumValues
    )) {
        throw new Error(`${field.name}.x-bms-ui-control ${control} requires a string enum`);
    }
    if (control === 'bounded_integer' && field.kind !== 'integer') {
        throw new Error(`${field.name}.x-bms-ui-control bounded_integer requires an integer`);
    }
    if (control === 'bounded_number' && field.kind !== 'number') {
        throw new Error(`${field.name}.x-bms-ui-control bounded_number requires a number`);
    }
    if (control === 'optional_integer' && (field.kind !== 'integer' || !field.nullable)) {
        throw new Error(`${field.name}.x-bms-ui-control optional_integer requires a nullable integer`);
    }
    if (control === 'read_only' && field.fixedValue === undefined) {
        throw new Error(`${field.name}.x-bms-ui-control read_only requires a schema const`);
    }
}

function validateDeclaredPrecision(field: ParameterField): void {
    if (!field.precision) return;
    // A read-only schema const has no operator-entered numeric or text
    // representation to coerce. The const value itself is the exact authority.
    if (field.fixedValue !== undefined && field.uiControl === 'read_only') return;
    const expected = field.nullable
        ? 'union_exact'
        : field.kind === 'boolean'
            ? 'boolean'
            : field.kind === 'array'
                ? 'ordered_exact_items'
                : field.kind === 'string'
                    ? 'exact_utf8_or_enum'
                    : field.kind === 'number'
                        ? 'float64'
                        : 'integer';
    if (field.precision !== expected) {
        throw new Error(
            `${field.name}.x-bms-precision ${field.precision} conflicts with ${field.nullable ? 'nullable ' : ''}${field.kind}`,
        );
    }
}

function validateAnnotationSemantics(field: ParameterField, hasDeclaredDefault: boolean): void {
    const policy = field.defaultPolicy;
    if (policy?.kind === 'schema_default' && !hasDeclaredDefault) {
        throw new Error(`${field.name}.x-bms-default-policy schema_default requires a schema default`);
    }
    if (policy?.kind === 'required_explicit_or_authority_bound' && hasDeclaredDefault) {
        throw new Error(`${field.name}.x-bms-default-policy conflicts with its schema default`);
    }
    if (policy?.kind === 'contextual_defaults') {
        if (hasDeclaredDefault || field.fixedValue !== undefined || field.kind === 'array') {
            throw new Error(`${field.name}.x-bms-default-policy contextual defaults conflict with its schema shape`);
        }
        for (const entry of policy.entries) {
            const error = validateScalar(entry.value, field, `${field.name} contextual default ${entry.context}`);
            if (error) throw new Error(error);
        }
    }
    if (field.defaultValue !== undefined) {
        const error = validateParameterValues({ [field.name]: field.defaultValue }, [field]);
        if (error) throw new Error(`${field.name}.default is invalid: ${error}`);
    }
}

function parseParameterField(
    name: string,
    value: unknown,
    required: boolean,
    allowArray = true,
): ParameterField {
    if (!isObject(value)) throw new Error(`${name} is not a schema object`);
    let schema = value;
    let nullable = false;
    let outerTitle: unknown;
    let outerDescription: unknown;
    let outerDefault: unknown;
    let wrappedNullable = false;
    if (Array.isArray(schema.anyOf)) {
        rejectUnknownSchemaKeys(
            schema,
            new Set(['anyOf', 'title', 'description', 'default', ...PARAMETER_ANNOTATIONS]),
            name,
        );
        const nullBranches = schema.anyOf.filter((branch) => (
            isObject(branch) && branch.type === 'null' && Object.keys(branch).length === 1
        ));
        const valueBranches = schema.anyOf.filter((branch) => !nullBranches.includes(branch));
        if (nullBranches.length !== 1 || valueBranches.length !== 1 || !isObject(valueBranches[0])) {
            throw new Error(`${name} uses an unsupported union`);
        }
        nullable = true;
        wrappedNullable = true;
        outerTitle = schema.title;
        outerDescription = schema.description;
        outerDefault = schema.default;
        schema = valueBranches[0];
    }
    const inferredConstKind = typeof schema.const === 'boolean'
        ? 'boolean'
        : typeof schema.const === 'string'
            ? 'string'
            : typeof schema.const === 'number' && Number.isInteger(schema.const)
                ? 'integer'
                : typeof schema.const === 'number'
                    ? 'number'
                    : null;
    const inferredEnumKind = Array.isArray(schema.enum)
        && schema.enum.length > 0
        && schema.enum.every((item) => typeof item === 'string')
        ? 'string'
        : null;
    const kind = (schema.type ?? inferredConstKind ?? inferredEnumKind) as ParameterKind;
    if (!['boolean', 'string', 'number', 'integer', 'array'].includes(kind)) {
        throw new Error(`${name} uses unsupported type ${String(schema.type)}`);
    }
    if (kind === 'array' && !allowArray) throw new Error(`${name} uses unsupported nested arrays`);
    const common = ['type', 'title', 'description', 'default', 'const', ...PARAMETER_ANNOTATIONS];
    const allowed: Record<ParameterKind, ReadonlySet<string>> = {
        boolean: new Set(common),
        string: new Set([...common, 'enum', 'minLength', 'maxLength', 'pattern']),
        number: new Set([...common, 'minimum', 'maximum']),
        integer: new Set([...common, 'minimum', 'maximum']),
        array: new Set([...common, 'items', 'minItems', 'maxItems', 'uniqueItems']),
    };
    rejectUnknownSchemaKeys(schema, allowed[kind], name);
    const title = wrappedNullable && outerTitle !== undefined ? outerTitle : schema.title;
    const description = wrappedNullable && outerDescription !== undefined
        ? outerDescription
        : schema.description;
    const outerHasDefault = wrappedNullable && Object.prototype.hasOwnProperty.call(value, 'default');
    const innerHasDefault = Object.prototype.hasOwnProperty.call(schema, 'default');
    if (outerHasDefault && innerHasDefault && JSON.stringify(outerDefault) !== JSON.stringify(schema.default)) {
        throw new Error(`${name}.default diverges between nullable wrapper and value schema`);
    }
    const hasDeclaredDefault = outerHasDefault || innerHasDefault;
    const defaultValue = outerHasDefault ? outerDefault : schema.default;
    const annotations = parseParameterAnnotations(
        name,
        schema,
        wrappedNullable ? value : null,
    );
    const field: ParameterField = {
        name,
        label: typeof title === 'string' && title.trim() ? title.trim() : name,
        description: typeof description === 'string' && description.trim() ? description.trim() : undefined,
        kind,
        required,
        nullable,
        fixedValue: schema.const as ParameterValue | undefined,
        defaultValue: defaultValue as ParameterValue | undefined,
        ...annotations,
    };
    if (schema.const !== undefined) {
        if (inferredConstKind === null || (schema.type !== undefined && schema.type !== inferredConstKind)) {
            throw new Error(`${name}.const is incompatible with its primitive type`);
        }
        field.defaultValue = schema.const as ParameterValue;
    }
    if (kind === 'string') {
        if (schema.enum !== undefined) {
            if (!Array.isArray(schema.enum) || !schema.enum.length || schema.enum.some((item) => typeof item !== 'string')) {
                throw new Error(`${name} must use a non-empty string enum`);
            }
            field.enumValues = schema.enum as string[];
        }
        for (const key of ['minLength', 'maxLength'] as const) {
            const bound = schema[key];
            if (bound !== undefined && (!Number.isInteger(bound) || (bound as number) < 0)) {
                throw new Error(`${name}.${key} is invalid`);
            }
            if (bound !== undefined) field[key] = bound as number;
        }
        if (field.minLength !== undefined && field.maxLength !== undefined && field.minLength > field.maxLength) {
            throw new Error(`${name} has inverted string bounds`);
        }
        if (schema.pattern !== undefined) {
            if (typeof schema.pattern !== 'string') throw new Error(`${name}.pattern is invalid`);
            try { new RegExp(schema.pattern); } catch { throw new Error(`${name}.pattern is invalid`); }
            field.pattern = schema.pattern;
        }
    } else if (kind === 'number' || kind === 'integer') {
        const minimum = schema.minimum;
        const maximum = schema.maximum;
        const hasMinimum = typeof minimum === 'number' && Number.isFinite(minimum);
        const hasMaximum = typeof maximum === 'number' && Number.isFinite(maximum);
        if (
            (!hasMinimum && !hasMaximum)
            || (minimum !== undefined && !hasMinimum)
            || (maximum !== undefined && !hasMaximum)
            || (hasMinimum && hasMaximum && minimum > maximum)
        ) {
            throw new Error(`${name} must declare at least one valid finite numeric bound`);
        }
        if (hasMinimum) field.minimum = minimum;
        if (hasMaximum) field.maximum = maximum;
    } else if (kind === 'array') {
        if (schema.items === undefined) throw new Error(`${name} does not declare array items`);
        const item = parseParameterField(`${name}[]`, schema.items, true, false);
        if (item.nullable || item.kind === 'array') throw new Error(`${name} uses unsupported array items`);
        field.item = {
            kind: item.kind,
            description: item.description,
            nullable: false,
            fixedValue: item.fixedValue,
            defaultValue: item.defaultValue,
            enumValues: item.enumValues,
            minimum: item.minimum,
            maximum: item.maximum,
            minLength: item.minLength,
            maxLength: item.maxLength,
            pattern: item.pattern,
            uiControl: item.uiControl,
            units: item.units,
            precision: item.precision,
            applicability: item.applicability,
            defaultPolicy: item.defaultPolicy,
            incompatibilities: item.incompatibilities,
            nativeKey: item.nativeKey,
            persistedRepresentation: item.persistedRepresentation,
            reproducibilityEffect: item.reproducibilityEffect,
            scientificMeaning: item.scientificMeaning,
            supportedRuntimeRange: item.supportedRuntimeRange,
        };
        for (const key of ['minItems', 'maxItems'] as const) {
            const bound = schema[key];
            if (bound !== undefined && (!Number.isInteger(bound) || (bound as number) < 0)) {
                throw new Error(`${name}.${key} is invalid`);
            }
            if (bound !== undefined) field[key] = bound as number;
        }
        if (field.minItems !== undefined && field.maxItems !== undefined && field.minItems > field.maxItems) {
            throw new Error(`${name} has inverted array bounds`);
        }
        if (schema.uniqueItems !== undefined && typeof schema.uniqueItems !== 'boolean') {
            throw new Error(`${name}.uniqueItems is invalid`);
        }
        field.uniqueItems = schema.uniqueItems as boolean | undefined;
    }
    validateDeclaredUiControl(field);
    validateDeclaredPrecision(field);
    validateAnnotationSemantics(field, hasDeclaredDefault);
    return field;
}

function parseParameterSchema(value: unknown): ParameterSchemaResult {
    try {
        if (!isObject(value)) throw new Error('capability response has no parameter_schema body');
        rejectUnknownSchemaKeys(
            value,
            new Set([
                '$id', '$schema', 'title', 'description', 'type', 'additionalProperties',
                'properties', 'required', 'x-bms-source-pin', 'x-bms-unknown-fields',
            ]),
            'parameter schema',
        );
        if (value.type !== 'object' || value.additionalProperties !== false || !isObject(value.properties)) {
            throw new Error('parameter schema must be a closed object');
        }
        if (typeof value.$id !== 'string' || !value.$id || value.$id !== value.$id.trim()) {
            throw new Error('parameter schema has no canonical $id');
        }
        if (
            typeof value['x-bms-source-pin'] !== 'string'
            || !/^[0-9a-f]{40}$/.test(value['x-bms-source-pin'])
        ) {
            throw new Error('parameter schema has an invalid x-bms-source-pin');
        }
        if (value['x-bms-unknown-fields'] !== 'reject_before_preparation') {
            throw new Error('parameter schema has an unsupported x-bms-unknown-fields policy');
        }
        const required = value.required ?? [];
        if (!Array.isArray(required) || required.some((name) => typeof name !== 'string')) {
            throw new Error('parameter schema required list is invalid');
        }
        const requiredNames = new Set(required as string[]);
        const propertyNames = new Set(Object.keys(value.properties));
        const unknownRequired = [...requiredNames].filter((name) => !propertyNames.has(name));
        if (unknownRequired.length) throw new Error(`required fields are not declared: ${unknownRequired.join(', ')}`);
        return {
            fields: Object.entries(value.properties).map(([name, schema]) => (
                parseParameterField(name, schema, requiredNames.has(name))
            )),
            schemaId: value.$id,
            sourcePin: value['x-bms-source-pin'],
            unknownFieldsPolicy: value['x-bms-unknown-fields'],
            error: null,
        };
    } catch (error) {
        return {
            fields: null,
            schemaId: null,
            sourcePin: null,
            unknownFieldsPolicy: null,
            error: authorityErrorMessage(error),
        };
    }
}

function canonicalJsonString(value: string): string {
    return JSON.stringify(value).replace(/[\u0080-\uffff]/g, (character) => (
        `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`
    ));
}

function canonicalJsonForDigest(value: unknown, path = 'capability contract'): string {
    if (value === null) return 'null';
    if (typeof value === 'string') return canonicalJsonString(value);
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'number') {
        if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
            throw new Error(`${path} contains a non-canonical number`);
        }
        const rendered = Object.is(value, -0) ? '0' : String(value);
        if (/[eE]/.test(rendered)) throw new Error(`${path} contains an unsupported exponential number`);
        return rendered;
    }
    if (Array.isArray(value)) {
        return `[${value.map((entry, index) => canonicalJsonForDigest(entry, `${path}[${index}]`)).join(',')}]`;
    }
    if (isObject(value)) {
        const keys = Object.keys(value).sort();
        return `{${keys.map((key) => (
            `${canonicalJsonString(key)}:${canonicalJsonForDigest(value[key], `${path}.${key}`)}`
        )).join(',')}}`;
    }
    throw new Error(`${path} contains a non-JSON value`);
}

async function sha256Hex(value: string): Promise<string> {
    if (!globalThis.crypto?.subtle) throw new Error('browser SHA-256 verification is unavailable');
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function assertNonEmptyString(value: unknown, label: string): asserts value is string {
    if (typeof value !== 'string' || !value || value !== value.trim()) throw new Error(`${label} is invalid`);
}

function assertStringSet(value: unknown, label: string, requireNonEmpty = false): asserts value is string[] {
    if (
        !Array.isArray(value)
        || (requireNonEmpty && value.length === 0)
        || value.some((entry) => typeof entry !== 'string' || !entry || entry !== entry.trim())
        || new Set(value).size !== value.length
    ) {
        throw new Error(`${label} is invalid`);
    }
}

async function derivePinnedPlanParameterSchema(plan: DomainWorkflowPlanHead): Promise<ParameterSchemaResult> {
    try {
        const contract: unknown = plan.capability_contract;
        if (!isObject(contract)) throw new Error('pinned capability contract is not an object');
        const contractKeys = Object.keys(contract).sort();
        const expectedContractKeys = ['allowed_model_modes', 'capability', 'parameter_schema', 'schema'];
        if (
            contractKeys.length !== expectedContractKeys.length
            || contractKeys.some((key, index) => key !== expectedContractKeys[index])
            || contract.schema !== 'bms.workflow-plan-capability-contract.v1'
        ) {
            throw new Error('pinned capability contract shape is invalid');
        }
        if (!isObject(contract.capability)) throw new Error('pinned capability authority is unavailable');
        const capability = contract.capability;
        assertNonEmptyString(capability.capability_id, 'pinned capability ID');
        if (capability.capability_id !== plan.capability_id) {
            throw new Error('pinned capability ID does not match the Plan');
        }
        assertNonEmptyString(capability.capability_version, 'pinned capability version');
        assertNonEmptyString(capability.workflow_family, 'pinned workflow family');
        assertNonEmptyString(capability.workflow_adapter_id, 'pinned workflow adapter');
        assertNonEmptyString(capability.canonical_source_destination, 'pinned native launcher destination');
        if (
            !capability.canonical_source_destination.startsWith('/')
            || capability.canonical_source_destination.startsWith('//')
        ) {
            throw new Error('pinned native launcher destination is not a safe same-origin route');
        }
        assertNonEmptyString(capability.parameter_schema_id, 'pinned parameter schema ID');
        if (
            capability.workflow_family !== plan.workflow_family
            || capability.workflow_adapter_id !== plan.adapter_id
        ) {
            throw new Error('pinned capability family/adapter does not match the Plan');
        }
        if (
            capability.plannable !== true
            || capability.exposure_state !== 'accepted'
            || !['managed_materialization', 'typed_launcher_handoff'].includes(String(capability.launch_mode))
        ) {
            throw new Error('pinned capability is not an accepted Plan launch contract');
        }
        assertStringSet(capability.result_contracts, 'pinned result contracts', true);
        if (!isObject(contract.parameter_schema)) {
            throw new Error('pinned capability parameter schema is unavailable');
        }
        if (contract.parameter_schema.$id !== capability.parameter_schema_id) {
            throw new Error('pinned parameter schema ID does not match its capability authority');
        }
        if (!Array.isArray(contract.allowed_model_modes) || contract.allowed_model_modes.length === 0) {
            throw new Error('pinned model/mode contract is invalid');
        }
        const modelModes = contract.allowed_model_modes;
        const modeKeys = new Set<string>();
        for (const pair of modelModes) {
            if (!isObject(pair) || Object.keys(pair).sort().join(',') !== 'mode,model_id') {
                throw new Error('pinned model/mode contract shape is invalid');
            }
            assertNonEmptyString(pair.model_id, 'pinned model ID');
            assertNonEmptyString(pair.mode, 'pinned model mode');
            const key = `${pair.model_id}\u0000${pair.mode}`;
            if (modeKeys.has(key)) throw new Error('pinned model/mode contract has duplicates');
            modeKeys.add(key);
        }
        if (
            !Array.isArray(capability.allowed_model_modes)
            || canonicalJsonForDigest(capability.allowed_model_modes) !== canonicalJsonForDigest(modelModes)
        ) {
            throw new Error('pinned model/mode contract diverges from its capability authority');
        }
        if (!/^[0-9a-f]{64}$/.test(plan.capability_contract_sha256)) {
            throw new Error('pinned capability contract digest is invalid');
        }
        const actualDigest = await sha256Hex(canonicalJsonForDigest(contract));
        if (actualDigest !== plan.capability_contract_sha256) {
            throw new Error('pinned capability contract digest does not match its content');
        }
        return parseParameterSchema(contract.parameter_schema);
    } catch (error) {
        return {
            fields: null,
            schemaId: null,
            sourcePin: null,
            unknownFieldsPolicy: null,
            error: authorityErrorMessage(error),
        };
    }
}

export function parameterValuesFromDraft(draft: JsonObject, fields: ParameterField[]): ParameterValues {
    const existing = isObject(draft.parameters) ? draft.parameters : {};
    const values: ParameterValues = {};
    fields.forEach((field) => {
        if (existing[field.name] !== undefined) values[field.name] = existing[field.name] as ParameterValue;
        else if (field.defaultValue !== undefined) values[field.name] = field.defaultValue;
    });
    Object.entries(existing).forEach(([name, value]) => {
        if (!(name in values)) values[name] = value as ParameterValue;
    });
    fields.forEach((field) => {
        if (values[field.name] !== undefined || field.defaultPolicy?.kind !== 'contextual_defaults') return;
        const matching = field.defaultPolicy.entries.filter((entry) => (
            fields.some((candidate) => (
                candidate.name !== field.name
                && values[candidate.name] !== undefined
                && !Array.isArray(values[candidate.name])
                && String(values[candidate.name]) === entry.context
            ))
        ));
        if (matching.length === 1) values[field.name] = matching[0].value;
    });
    return values;
}

function parseApplicabilityValue(raw: string): ParameterValue {
    if (raw === 'null') return null;
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    if (/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(raw)) return Number(raw);
    return raw;
}

function fieldForApplicabilityKey(fields: ParameterField[], key: string): ParameterField | undefined {
    return fields.find((field) => (
        field.name === key || field.nativeKey?.split('+').includes(key)
    ));
}

export function parameterIsVisible(field: ParameterField, fields: ParameterField[], values: ParameterValues): boolean {
    const applicability = field.applicability;
    if (!applicability || applicability === 'always') return true;
    const clauses = applicability.split(' and ').map((clause) => {
        const match = /^([a-z][a-z0-9_]*)=([A-Za-z0-9_.+/-]+)$/.exec(clause);
        return match ? { key: match[1], expected: parseApplicabilityValue(match[2]) } : null;
    });
    if (clauses.some((clause) => clause === null)) return true;
    const controllers = clauses.map((clause) => fieldForApplicabilityKey(fields, clause?.key ?? ''));
    // Applicability may be authority-bound prose (for example, target receipt presence). Only
    // hide a control when every machine-readable controller is present in this pinned schema.
    if (controllers.some((controller) => controller === undefined)) return true;
    return clauses.every((clause, index) => values[controllers[index]?.name ?? ''] === clause?.expected);
}

function jsonReadbackValue(value: JsonValue | undefined): string {
    if (value === undefined) return 'Not supplied';
    if (typeof value === 'string') return value || 'Empty string';
    return JSON.stringify(value);
}

function SettingsReadback({
    requested,
    effective,
}: {
    requested: JsonObject;
    effective?: JsonObject;
}) {
    const keys = [...new Set([...Object.keys(requested), ...Object.keys(effective ?? {})])].sort();
    if (!keys.length) {
        return <p className="text-xs text-content-muted">No settings were persisted for this capability.</p>;
    }
    return (
        <div className="overflow-x-auto rounded-md border border-border-primary">
            <table className="w-full min-w-[32rem] text-left text-xs">
                <thead className="bg-surface-secondary text-content-muted">
                    <tr>
                        <th className="px-3 py-2 font-semibold">Setting</th>
                        <th className="px-3 py-2 font-semibold">Requested</th>
                        {effective && <th className="px-3 py-2 font-semibold">Effective</th>}
                    </tr>
                </thead>
                <tbody>
                    {keys.map((key) => (
                        <tr key={key} className="border-t border-border-primary align-top">
                            <th className="px-3 py-2 font-mono font-medium text-content-secondary">{key}</th>
                            <td className="max-w-md break-words px-3 py-2 font-mono text-content-primary">{jsonReadbackValue(requested[key])}</td>
                            {effective && (
                                <td className="max-w-md break-words px-3 py-2 font-mono text-content-primary">
                                    {jsonReadbackValue(effective[key])}
                                </td>
                            )}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function validateScalar(
    value: ParameterValue,
    field: NonNullable<ParameterField['item']> | ParameterField,
    label: string,
): string | null {
    if (value === null) return field.nullable ? null : `${label} may not be null`;
    if (field.fixedValue !== undefined && value !== field.fixedValue) return `${label} must retain its schema-fixed value`;
    if (field.kind === 'boolean') return typeof value === 'boolean' ? null : `${label} must be true or false`;
    if (field.kind === 'number' || field.kind === 'integer') {
        if (typeof value !== 'number' || !Number.isFinite(value)) return `${label} must be numeric`;
        if (field.kind === 'integer' && !Number.isInteger(value)) return `${label} must be an integer`;
        if (field.minimum !== undefined && value < field.minimum) return `${label} is below its minimum`;
        if (field.maximum !== undefined && value > field.maximum) return `${label} is above its maximum`;
        return null;
    }
    if (field.kind !== 'string' || typeof value !== 'string') return `${label} must be text`;
    if (field.enumValues && !field.enumValues.includes(value)) return `${label} is not an allowed option`;
    if (field.minLength !== undefined && value.length < field.minLength) return `${label} is too short`;
    if (field.maxLength !== undefined && value.length > field.maxLength) return `${label} is too long`;
    if (field.pattern && !new RegExp(field.pattern).test(value)) return `${label} does not match its required pattern`;
    return null;
}

function validateParameterValues(values: ParameterValues, fields: ParameterField[]): string | null {
    const known = new Set(fields.map((field) => field.name));
    const unknown = Object.keys(values).filter((name) => !known.has(name));
    if (unknown.length) return `Readback contains unsupported parameter fields: ${unknown.join(', ')}`;
    for (const field of fields) {
        const value = values[field.name];
        if (value === undefined) {
            if (field.required) return `${field.label} is required`;
            continue;
        }
        if (field.kind === 'array') {
            if (!Array.isArray(value) || !field.item) return `${field.label} must be an array`;
            if (field.minItems !== undefined && value.length < field.minItems) return `${field.label} has too few items`;
            if (field.maxItems !== undefined && value.length > field.maxItems) return `${field.label} has too many items`;
            if (field.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) {
                return `${field.label} requires unique items`;
            }
            for (const item of value) {
                const error = validateScalar(item, field.item, `${field.label} item`);
                if (error) return error;
            }
        } else {
            const error = validateScalar(value, field, field.label);
            if (error) return error;
        }
    }
    return null;
}

function validateTypedDraftParameters(draft: JsonObject | undefined, fields: ParameterField[]): string | null {
    if (!draft || !isObject(draft.parameters)) {
        return 'Workflow draft readback lacks a typed parameters object.';
    }
    return validateParameterValues(draft.parameters as unknown as ParameterValues, fields);
}

type ParameterMetadataField = ParameterField | NonNullable<ParameterField['item']>;

function parameterNumericStep(field: ParameterMetadataField): number | 'any' | undefined {
    if (field.kind === 'integer') return 1;
    if (field.kind === 'number') return 'any';
    return undefined;
}

function formatParameterValue(value: ParameterValue): string {
    return JSON.stringify(value) ?? 'null';
}

function ParameterMetadata({ field }: { field: ParameterMetadataField }) {
    const numericStep = parameterNumericStep(field);
    const boundedMetadata: string[] = [];
    if (field.minimum !== undefined || field.maximum !== undefined) {
        boundedMetadata.push(`Range ${field.minimum ?? 'unbounded'}–${field.maximum ?? 'unbounded'}`);
    }
    if (field.minLength !== undefined || field.maxLength !== undefined) {
        boundedMetadata.push(`Length ${field.minLength ?? 0}–${field.maxLength ?? 'unbounded'}`);
    }
    if (field.minItems !== undefined || field.maxItems !== undefined) {
        boundedMetadata.push(`Items ${field.minItems ?? 0}–${field.maxItems ?? 'unbounded'}`);
    }
    if (field.uniqueItems) boundedMetadata.push('Unique items required');
    return (
        <>
            <span className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-content-muted">
                <span>Type: <span className="font-mono">{field.kind}</span></span>
                {field.uiControl && <span>Control: <span className="font-mono">{field.uiControl}</span></span>}
                {field.units && <span>Units: <span className="font-mono">{field.units}</span></span>}
                {field.precision && <span>Precision: <span className="font-mono">{field.precision}</span></span>}
                {numericStep !== undefined && <span>Step: <span className="font-mono">{numericStep}</span></span>}
                {field.defaultValue !== undefined && (
                    <span>Default: <span className="font-mono">{formatParameterValue(field.defaultValue)}</span></span>
                )}
                {field.fixedValue !== undefined && (
                    <span>Fixed: <span className="font-mono">{formatParameterValue(field.fixedValue)}</span></span>
                )}
                {boundedMetadata.map((item) => <span key={item}>{item}</span>)}
            </span>
            {field.enumValues && (
                <span className="block text-[11px] text-content-muted">
                    Allowed values: <span className="font-mono">{field.enumValues.map((value) => JSON.stringify(value)).join(', ')}</span>
                </span>
            )}
            {field.pattern && (
                <span className="block text-[11px] text-content-muted">
                    Pattern: <span className="font-mono">{field.pattern}</span>
                </span>
            )}
            {field.defaultPolicy && (
                <span className="block text-[11px] text-content-muted">
                    Default policy: <span className="font-mono">{field.defaultPolicy.canonicalText}</span>
                </span>
            )}
            {field.nativeKey && (
                <span className="block text-[11px] text-content-muted">
                    Native key: <span className="font-mono">{field.nativeKey}</span>
                </span>
            )}
            {field.persistedRepresentation && (
                <span className="block text-[11px] text-content-muted">
                    Persisted representation: <span className="font-mono">{field.persistedRepresentation}</span>
                </span>
            )}
            {field.scientificMeaning && (
                <span className="block text-[11px] text-content-muted">
                    Scientific meaning: <span className="font-mono">{field.scientificMeaning}</span>
                </span>
            )}
            {field.supportedRuntimeRange && (
                <span className="block text-[11px] text-content-muted">
                    Supported runtime range: <span className="font-mono">{field.supportedRuntimeRange.canonicalText}</span>
                </span>
            )}
            {field.applicability && (
                <span className="block text-[11px] text-content-muted">
                    Applicability: <span className="font-mono">{field.applicability}</span>
                </span>
            )}
            {field.incompatibilities !== undefined && (
                <span className="block text-[11px] text-content-muted">
                    Incompatibilities: <span className="font-mono">
                        {field.incompatibilities.length ? field.incompatibilities.join('; ') : 'none declared'}
                    </span>
                </span>
            )}
            {field.reproducibilityEffect && (
                <span className="block text-[11px] text-content-muted">
                    Reproducibility: <span className="font-mono">{field.reproducibilityEffect}</span>
                </span>
            )}
        </>
    );
}

function initialArrayItemValue(item: NonNullable<ParameterField['item']>): ParameterValue {
    if (item.fixedValue !== undefined) return item.fixedValue;
    if (item.defaultValue !== undefined) return item.defaultValue;
    if (item.enumValues?.length) return item.enumValues[0];
    if (item.kind === 'boolean') return false;
    if (item.kind === 'number' || item.kind === 'integer') return item.minimum ?? 0;
    return '';
}

function ParameterControl({ field, value, onChange }: {
    field: ParameterField;
    value: ParameterValue | undefined;
    onChange: (value: ParameterValue | undefined) => void;
}) {
    const activateValue = () => onChange(field.defaultValue ?? (
        field.kind === 'array'
            ? []
            : field.kind === 'boolean'
                ? false
                : field.kind === 'number' || field.kind === 'integer'
                    ? field.minimum ?? 0
                    : ''
    ));
    const numericStep = parameterNumericStep(field);
    let control: ReactNode;
    if (field.fixedValue !== undefined || field.uiControl === 'read_only') {
        control = (
            <div className={`${INPUT_CLASS} cursor-not-allowed opacity-70`}>
                Fixed by schema: {String(field.fixedValue)}
            </div>
        );
    } else if (value === null) {
        control = <button type="button" className={BUTTON_CLASS} onClick={activateValue}>Set a concrete value</button>;
    } else if (
        field.uiControl === 'select'
        || field.uiControl === 'typed_source_selector'
        || (field.uiControl === 'typed_control' && field.enumValues)
        || (!field.uiControl && field.enumValues)
    ) {
        control = (
            <select className={INPUT_CLASS} value={typeof value === 'string' ? value : ''} onChange={(event) => onChange(event.target.value)}>
                <option value="" disabled>
                    {field.uiControl === 'typed_source_selector' ? 'Select a server-declared source' : 'Select an option'}
                </option>
                {field.enumValues?.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
        );
    } else if (
        field.uiControl === 'checkbox'
        || (field.uiControl === 'typed_control' && field.kind === 'boolean')
        || (!field.uiControl && field.kind === 'boolean')
    ) {
        control = (
            <span className={`${INPUT_CLASS} flex items-center gap-2`}>
                <input type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />
                <span>{value === true ? 'Enabled' : 'Disabled'}</span>
            </span>
        );
    } else if (
        field.uiControl === 'bounded_integer'
        || field.uiControl === 'bounded_number'
        || field.uiControl === 'optional_integer'
        || (field.uiControl === 'typed_control' && (field.kind === 'number' || field.kind === 'integer'))
        || (!field.uiControl && (field.kind === 'number' || field.kind === 'integer'))
    ) {
        control = (
            <input className={INPUT_CLASS} type="number" min={field.minimum} max={field.maximum}
                step={numericStep} value={typeof value === 'number' ? value : ''}
                onChange={(event) => {
                    if (!event.target.value) return onChange(undefined);
                    const parsed = Number(event.target.value);
                    if (Number.isFinite(parsed) && (field.kind !== 'integer' || Number.isInteger(parsed))) {
                        onChange(parsed);
                    }
                }} />
        );
    } else if (field.kind === 'array' && field.item) {
        const arrayItems = Array.isArray(value) ? value : [];
        control = (
            <div className="space-y-3">
                <div className="space-y-1 rounded-md border border-border-primary bg-surface-secondary p-2">
                    <span className="block text-xs font-medium text-content-primary">Array item contract</span>
                    {field.item.description && (
                        <span className="block text-xs text-content-muted">{field.item.description}</span>
                    )}
                    <ParameterMetadata field={field.item} />
                </div>
                {arrayItems.map((itemValue, index) => {
                    const itemField: ParameterField = {
                        name: `${field.name}[${index}]`,
                        label: `${field.label} item ${index + 1}`,
                        required: true,
                        ...field.item!,
                    };
                    const itemError = validateScalar(
                        itemValue,
                        field.item!,
                        `${field.label} item ${index + 1}`,
                    );
                    return (
                        <div key={index} className="space-y-2 rounded-md border border-border-primary p-2">
                            <ParameterControl
                                field={itemField}
                                value={itemValue}
                                onChange={(nextValue) => {
                                    if (nextValue === undefined) return;
                                    const nextItems = [...arrayItems];
                                    nextItems[index] = nextValue;
                                    onChange(nextItems);
                                }}
                            />
                            {itemError && <span className="block text-xs text-red-200">{itemError}</span>}
                            <button
                                type="button"
                                className="text-xs text-content-muted hover:text-content-primary disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={arrayItems.length <= (field.minItems ?? 0)}
                                onClick={() => onChange(arrayItems.filter((_, itemIndex) => itemIndex !== index))}
                            >
                                Remove item
                            </button>
                        </div>
                    );
                })}
                <button
                    type="button"
                    className={BUTTON_CLASS}
                    disabled={field.maxItems !== undefined && arrayItems.length >= field.maxItems}
                    onClick={() => onChange([...arrayItems, initialArrayItemValue(field.item!)])}
                >
                    Add item
                </button>
            </div>
        );
    } else {
        control = (
            <input className={INPUT_CLASS} type="text" minLength={field.minLength} maxLength={field.maxLength}
                pattern={field.pattern} value={typeof value === 'string' ? value : ''}
                onChange={(event) => onChange(event.target.value)} />
        );
    }
    return (
        <div className="block space-y-1 rounded-md border border-border-primary bg-surface p-3">
            <span className="flex items-center justify-between gap-3 text-sm font-medium text-content-primary">
                <span>{field.label}{field.required ? ' *' : ''}</span>
                <span className="flex gap-2">
                    {field.nullable && value !== null && (
                        <button type="button" className="text-xs text-content-muted hover:text-content-primary" onClick={() => onChange(null)}>Set null</button>
                    )}
                    {!field.required && value !== undefined && (
                        <button type="button" className="text-xs text-content-muted hover:text-content-primary" onClick={() => onChange(undefined)}>Unset</button>
                    )}
                </span>
            </span>
            {field.description && <span className="block text-xs text-content-muted">{field.description}</span>}
            {!field.required && value === undefined
                ? <button type="button" className={BUTTON_CLASS} onClick={activateValue}>Set value</button>
                : control}
            <ParameterMetadata field={field} />
        </div>
    );
}

function collectReceiptIds(value: unknown, found = new Set<string>()): Set<string> {
    if (Array.isArray(value)) {
        value.forEach((item) => collectReceiptIds(item, found));
        return found;
    }
    if (!value || typeof value !== 'object') return found;
    Object.entries(value).forEach(([key, child]) => {
        if (typeof child === 'string' && (key === 'receipt_id' || key.endsWith('_receipt_id'))) found.add(child);
        else collectReceiptIds(child, found);
    });
    return found;
}

function appendLaunchContext(path: string, launchContextId: string): string {
    const separator = path.includes('?') ? '&' : '?';
    return `${path}${separator}launch_context_id=${encodeURIComponent(launchContextId)}`;
}

export default function DomainWorkflowOperator({
    projectId,
    globalExperimentId,
    domainExperimentId,
    domainRevisionId,
    selectedStateRevisionId,
    currentStateRevisionId,
    projectReturnUri,
    contextHref,
    inputDatasetRevisionIds,
    initialRunGroupId,
}: DomainWorkflowOperatorProps) {
    const queryClient = useQueryClient();
    const scope = [projectId, globalExperimentId, domainExperimentId] as const;
    const routeParameters = new URLSearchParams(window.location.search);
    const routedCloneAction = routeParameters.get('run_group_action') === 'clone';
    const routedCloneRunId = routedCloneAction ? routeParameters.get('source_run_id')?.trim() ?? '' : '';
    const routedCloneAttemptId = routedCloneAction ? routeParameters.get('source_attempt_id')?.trim() ?? '' : '';
    const [selectedPlanId, setSelectedPlanId] = useState('');
    const [selectedPlanRevisionId, setSelectedPlanRevisionId] = useState('');
    const [planName, setPlanName] = useState('');
    const [capabilityId, setCapabilityId] = useState('');
    const [parameterValues, setParameterValues] = useState<ParameterValues>({});
    const [parameterSchema, setParameterSchema] = useState<ParameterSchemaResult>({
        fields: null,
        schemaId: null,
        sourcePin: null,
        unknownFieldsPolicy: null,
        error: 'Pinned Plan parameter authority is loading.',
    });
    const [draftError, setDraftError] = useState<string | null>(null);
    const [changeSummary, setChangeSummary] = useState('Publish operator-reviewed Workflow Plan');
    const [selectedPreparations, setSelectedPreparations] = useState<SelectedPreparation[]>([]);
    const [retryPreparationByRunId, setRetryPreparationByRunId] = useState<Record<string, string>>({});
    const [issuedLaunchContexts, setIssuedLaunchContexts] = useState<IssuedLaunchHandoff[]>([]);
    const [activeRunGroupId, setActiveRunGroupId] = useState(initialRunGroupId?.trim() ?? '');
    const [runGroupLookupId, setRunGroupLookupId] = useState(initialRunGroupId?.trim() ?? '');
    const [cancelReason, setCancelReason] = useState('Operator cancelled from the NGS/MolBio Domain workspace');
    const [cloneSourceRunId, setCloneSourceRunId] = useState(routedCloneRunId);
    const [cloneSourceAttemptId, setCloneSourceAttemptId] = useState(routedCloneAttemptId);
    const [clonePlanName, setClonePlanName] = useState('Cloned Workflow Plan intent');
    const [cloneChangeSummary, setCloneChangeSummary] = useState('Clone exact immutable run intent for revision');
    const [cloneReceipt, setCloneReceipt] = useState<RunCloneReceipt | null>(null);
    const [resultSurfaces, setResultSurfaces] = useState<Record<string, DomainResultSurface>>({});
    const selectionAuthorityKey = JSON.stringify([
        projectId,
        globalExperimentId,
        domainExperimentId,
        domainRevisionId,
        selectedStateRevisionId,
        currentStateRevisionId,
    ]);

    useEffect(() => {
        setSelectedPreparations([]);
        setRetryPreparationByRunId({});
        setIssuedLaunchContexts([]);
    }, [selectionAuthorityKey]);

    useEffect(() => {
        setRetryPreparationByRunId({});
        const initialGroup = initialRunGroupId?.trim() ?? '';
        setCloneSourceRunId(activeRunGroupId === initialGroup ? routedCloneRunId : '');
        setCloneSourceAttemptId(activeRunGroupId === initialGroup ? routedCloneAttemptId : '');
        setCloneReceipt(null);
    }, [activeRunGroupId, initialRunGroupId, routedCloneAttemptId, routedCloneRunId]);

    const removeSelectedPreparation = (preparationId: string) => {
        setSelectedPreparations((current) => current.filter(
            (selection) => selection.preparation.preparation_id !== preparationId,
        ));
        setRetryPreparationByRunId((current) => Object.fromEntries(
            Object.entries(current).filter(([, selectedId]) => selectedId !== preparationId),
        ));
    };

    useEffect(() => {
        const requested = initialRunGroupId?.trim() ?? '';
        setActiveRunGroupId(requested);
        setRunGroupLookupId(requested);
    }, [domainExperimentId, globalExperimentId, initialRunGroupId, projectId]);

    const bindingQuery = useQuery({
        queryKey: ['ngs-molbio-binding', ...scope],
        queryFn: ({ signal }) => getNgsMolBioBinding(...scope, signal),
        retry: false,
        refetchInterval: (query) => query.state.data?.provisioning_state === 'provisioning' ? 2000 : false,
    });
    const capabilitiesQuery = useQuery({
        queryKey: ['domain-capabilities', ...scope],
        queryFn: ({ signal }) => listDomainCapabilities(...scope, signal),
        retry: false,
    });

    const plansQuery = useQuery({
        queryKey: ['domain-workflow-plans', ...scope],
        queryFn: ({ signal }) => listDomainWorkflowPlans(...scope, signal),
        retry: false,
    });
    const planQuery = useQuery({
        queryKey: ['domain-workflow-plan', ...scope, selectedPlanId],
        queryFn: ({ signal }) => getDomainWorkflowPlan(...scope, selectedPlanId, signal),
        enabled: Boolean(selectedPlanId),
        retry: false,
    });
    const revisionsQuery = useQuery({
        queryKey: ['domain-workflow-plan-revisions', ...scope, selectedPlanId],
        queryFn: ({ signal }) => listDomainWorkflowPlanRevisions(...scope, selectedPlanId, signal),
        enabled: Boolean(selectedPlanId),
        retry: false,
    });
    const runGroupQuery = useQuery({
        queryKey: ['domain-run-group', ...scope, activeRunGroupId],
        queryFn: ({ signal }) => getDomainRunGroup(...scope, activeRunGroupId, signal),
        enabled: Boolean(activeRunGroupId),
        retry: false,
        refetchInterval: (query) => {
            const state = query.state.data?.state;
            return state && !TERMINAL_STATES.has(state) ? 5000 : false;
        },
    });
    const typedDraftError = useMemo(() => {
        if (!parameterSchema.fields) return parameterSchema.error;
        return validateTypedDraftParameters(planQuery.data?.draft ?? undefined, parameterSchema.fields);
    }, [parameterSchema, planQuery.data?.draft]);
    const visibleParameterFields = useMemo(() => (
        parameterSchema.fields?.filter((field) => (
            parameterIsVisible(field, parameterSchema.fields ?? [], parameterValues)
        )) ?? []
    ), [parameterSchema.fields, parameterValues]);
    const hiddenConditionalFieldCount = (parameterSchema.fields?.length ?? 0) - visibleParameterFields.length;

    useEffect(() => {
        let cancelled = false;
        if (!planQuery.data) {
            setParameterSchema({
                fields: null,
                schemaId: null,
                sourcePin: null,
                unknownFieldsPolicy: null,
                error: selectedPlanId
                    ? 'Pinned Plan parameter authority is loading.'
                    : 'Select a Workflow Plan to load its pinned parameter authority.',
            });
            return () => { cancelled = true; };
        }
        setParameterSchema({
            fields: null,
            schemaId: null,
            sourcePin: null,
            unknownFieldsPolicy: null,
            error: 'Pinned Plan capability contract verification is pending.',
        });
        void derivePinnedPlanParameterSchema(planQuery.data).then((result) => {
            if (!cancelled) setParameterSchema(result);
        });
        return () => { cancelled = true; };
    }, [planQuery.data, selectedPlanId]);

    useEffect(() => {
        const plans = plansQuery.data?.items ?? [];
        if (!plans.length) {
            setSelectedPlanId('');
            return;
        }
        if (!plans.some((plan) => plan.plan_id === selectedPlanId)) {
            const preferred = plans.find((plan) => Boolean(plan.current_revision_id) || (plan.draft_generation ?? 0) > 0) ?? plans[0];
            setSelectedPlanId(preferred.plan_id);
        }
    }, [plansQuery.data?.items, selectedPlanId]);

    useEffect(() => {
        const capabilities = capabilitiesQuery.data?.items ?? [];
        if (capabilityId && !capabilities.some((capability) => capability.capability_id === capabilityId)) {
            setCapabilityId('');
        }
    }, [capabilitiesQuery.data?.items, capabilityId]);

    useEffect(() => {
        const revisions = revisionsQuery.data?.items ?? [];
        if (!revisions.length) {
            setSelectedPlanRevisionId('');
            return;
        }
        const current = planQuery.data?.current_revision_id;
        if (!revisions.some((revision) => revision.revision_id === selectedPlanRevisionId)) {
            setSelectedPlanRevisionId(current && revisions.some((revision) => revision.revision_id === current)
                ? current
                : revisions[revisions.length - 1].revision_id);
        }
    }, [planQuery.data?.current_revision_id, revisionsQuery.data?.items, selectedPlanRevisionId]);

    useEffect(() => {
        if (planQuery.data && parameterSchema.fields) {
            setParameterValues(parameterValuesFromDraft(planQuery.data.draft ?? {}, parameterSchema.fields));
            setDraftError(null);
        } else {
            setParameterValues({});
        }
    }, [parameterSchema, planQuery.data?.draft, planQuery.data?.plan_id]);

    const binding = bindingQuery.data;
    const bindingReady = binding?.provisioning_state === 'ready'
        && (binding.command_state === 'applied' || binding.command_state === 'duplicate')
        && Boolean(binding.global_receipt_id)
        && Boolean(binding.acknowledgement_id)
        && binding.domain_revision_id === domainRevisionId;
    const inspectingCurrentState = Boolean(selectedStateRevisionId)
        && selectedStateRevisionId === currentStateRevisionId;
    const mutationBlocker = useMemo(() => {
        if (!domainRevisionId) return 'The exact global Domain revision is unavailable.';
        if (bindingQuery.isLoading) return 'Binding authority is loading.';
        if (bindingQuery.isError) return 'No readable global/local binding authority is available.';
        if (!bindingReady) return 'The exact current Domain revision does not have a ready acknowledged binding.';
        if (!selectedStateRevisionId) return 'Select an immutable local state revision.';
        if (!inspectingCurrentState) return 'Historical local state revisions are read-only. Select the current immutable state revision to mutate.';
        return null;
    }, [bindingQuery.isError, bindingQuery.isLoading, bindingReady, domainRevisionId, inspectingCurrentState, selectedStateRevisionId]);
    const canMutate = mutationBlocker === null;

    const invalidatePlanAuthority = async () => {
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['domain-workflow-plans', ...scope] }),
            queryClient.invalidateQueries({ queryKey: ['domain-workflow-plan', ...scope, selectedPlanId] }),
            queryClient.invalidateQueries({ queryKey: ['domain-workflow-plan-revisions', ...scope, selectedPlanId] }),
        ]);
    };

    const initializeBindingMutation = useMutation({
        mutationFn: async () => {
            if (!domainRevisionId) throw new Error('The exact Domain revision is unavailable.');
            const bindingStatus = await initializeNgsMolBioBinding(...scope, domainRevisionId);
            if (bindingStatus.provisioning_state !== 'ready') {
                throw new Error('The managed connector is still establishing binding authority. Refresh and retry after it is ready.');
            }
            const state = await initializeMolBioNgsDomainState(domainExperimentId, {
                global_domain_experiment_revision_id: domainRevisionId,
                idempotency_key: `project-manager-state-init:${domainExperimentId}:${domainRevisionId}`,
            });
            if (state.current_state_revision_id) return state;
            await saveMolBioNgsStateRevision(domainExperimentId, {
                global_domain_experiment_revision_id: domainRevisionId,
                expected_head_generation: state.head_generation,
                parent_revision_id: null,
                idempotency_key: `project-manager-state-revision:${domainExperimentId}:${domainRevisionId}`,
                payload: EMPTY_DOMAIN_STATE_PAYLOAD,
                members: [],
            });
            return fetchMolBioNgsDomainState(domainExperimentId);
        },
        onSuccess: async () => Promise.all([
            queryClient.invalidateQueries({ queryKey: ['ngs-molbio-binding', ...scope] }),
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-project-domain-experiments', projectId] }),
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-domain-state', domainExperimentId] }),
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revisions', domainExperimentId] }),
        ]),
    });
    const reverifyBindingMutation = useMutation({
        mutationFn: () => reverifyNgsMolBioBinding(...scope, domainRevisionId as string, binding?.binding_revision_id as string),
        onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['ngs-molbio-binding', ...scope] }),
    });
    const createPlanMutation = useMutation({
        mutationFn: () => createDomainWorkflowPlan(...scope, {
            name: planName.trim(),
            capability_id: capabilityId.trim(),
            expected_domain_revision_id: domainRevisionId as string,
        }),
        onSuccess: async (created) => {
            setPlanName('');
            setCapabilityId('');
            await queryClient.invalidateQueries({ queryKey: ['domain-workflow-plans', ...scope] });
            setSelectedPlanId(created.plan_id);
        },
    });
    const saveDraftMutation = useMutation({
        mutationFn: async () => {
            setDraftError(null);
            if (!parameterSchema.fields) throw new Error(parameterSchema.error);
            const parameterError = validateParameterValues(parameterValues, parameterSchema.fields);
            if (parameterError) throw new Error(parameterError);
            const draft = planQuery.data?.draft;
            if (
                !draft
                || typeof draft.schema !== 'string'
                || typeof draft.workflow_family !== 'string'
                || typeof draft.contract_version !== 'string'
                || typeof draft.adapter_id !== 'string'
                || !Array.isArray(draft.nodes)
                || !Array.isArray(draft.edges)
            ) {
                throw new Error('Workflow draft readback lacks the closed server-authored workflow envelope.');
            }
            const payload = {
                ...draft,
                parameters: parameterValues,
                scheduler: {
                    ...((isObject(draft.scheduler) ? draft.scheduler : {}) as JsonObject),
                    params: { ...parameterValues, workflow_adapter: draft.adapter_id },
                },
            } as JsonObject;
            return replaceDomainWorkflowPlanDraft(
                ...scope,
                selectedPlanId,
                planQuery.data?.draft_generation ?? 0,
                payload,
            );
        },
        onError: (error) => setDraftError(authorityErrorMessage(error)),
        onSuccess: async () => invalidatePlanAuthority(),
    });
    const publishRevisionMutation = useMutation({
        mutationFn: () => {
            setDraftError(null);
            if (typedDraftError) throw new Error(typedDraftError);
            return publishDomainWorkflowPlanRevision(...scope, selectedPlanId, {
                expected_head_generation: planQuery.data?.head_generation ?? 0,
                expected_draft_generation: planQuery.data?.draft_generation ?? 0,
                change_summary: changeSummary.trim(),
            });
        },
        onError: (error) => setDraftError(authorityErrorMessage(error)),
        onSuccess: async (revision) => {
            await invalidatePlanAuthority();
            setSelectedPlanRevisionId(revision.revision_id);
        },
    });
    const pinnedPlanCapability = parameterSchema.fields
        ? planQuery.data?.capability_contract.capability ?? null
        : null;
    const currentPlanLaunchMode = pinnedPlanCapability?.launch_mode;
    const launchModeResolved = isDomainCapabilityLaunchMode(currentPlanLaunchMode);
    const selectedPreparationBlocker = useMemo(
        () => preparationSelectionError(selectedPreparations, selectionAuthorityKey),
        [selectedPreparations, selectionAuthorityKey],
    );

    const prepareMutation = useMutation({
        mutationFn: async (): Promise<SelectedPreparation> => {
            if (!canMutate) throw new Error(mutationBlocker ?? 'Mutation authority is unavailable.');
            if (!isDomainCapabilityLaunchMode(currentPlanLaunchMode)) {
                throw new Error('The selected Plan pinned capability has no explicit supported launch mode. Preparation selection is blocked.');
            }
            const sourceDestination = pinnedPlanCapability?.canonical_source_destination;
            if (
                typeof sourceDestination !== 'string'
                || !sourceDestination.startsWith('/')
                || sourceDestination.startsWith('//')
            ) {
                throw new Error('The selected Plan has no safe pinned native launcher destination.');
            }
            const planId = selectedPlanId.trim();
            const planRevisionId = selectedPlanRevisionId.trim();
            if (!planId || !planRevisionId) throw new Error('Select an immutable Plan revision before preparing it.');
            if (planQuery.data?.plan_id !== planId) {
                throw new Error('The selected Plan authority is still loading or no longer matches the selector.');
            }
            if (!revisionsQuery.data?.items.some((revision) => revision.revision_id === planRevisionId)) {
                throw new Error('The selected immutable Plan revision is still loading or no longer belongs to this Plan.');
            }
            const created = await prepareDomainWorkflowPlanRevision(
                ...scope,
                planId,
                planRevisionId,
                inputDatasetRevisionIds,
            );
            if (created.workflow_revision_id !== planRevisionId) {
                throw new Error('Prepared authority does not match the selected immutable Plan revision.');
            }
            return {
                preparation: created,
                planId,
                planRevisionId,
                launchMode: currentPlanLaunchMode,
                sourceDestination,
                authorityKey: selectionAuthorityKey,
            };
        },
        onSuccess: (created) => {
            if (created.authorityKey !== selectionAuthorityKey) {
                setDraftError('Prepared authority returned after the selected Domain or state authority changed. Prepare it again in the current context.');
                return;
            }
            setSelectedPreparations((current) => [
                ...current.filter((selection) => (
                    selection.preparation.preparation_id !== created.preparation.preparation_id
                )),
                created,
            ]);
            setRetryPreparationByRunId({});
            setIssuedLaunchContexts([]);
        },
    });

    const buildPreparationLaunches = async (
        selections: SelectedPreparation[],
    ): Promise<{ launches: PreparationLaunchRequest[]; launchContexts: IssuedLaunchHandoff[] }> => {
        const resolved = await Promise.all(selections.map(async (selection) => {
            if (selection.launchMode === 'managed_materialization') {
                return {
                    launch: {
                        preparation_id: selection.preparation.preparation_id,
                        launch_context_id: null,
                    } satisfies PreparationLaunchRequest,
                    launchContext: null,
                    sourceDestination: selection.sourceDestination,
                };
            }
            if (selection.launchMode !== 'typed_launcher_handoff') {
                throw new Error(`Preparation ${selection.preparation.preparation_id} has an unsupported launch mode.`);
            }
            const launchContext = await issuePreparedLaunchContext(
                ...scope,
                selection.preparation.preparation_id,
                projectReturnUri,
            );
            if (
                launchContext.project_id !== projectId
                || launchContext.global_experiment_id !== globalExperimentId
                || launchContext.domain_experiment_id !== domainExperimentId
                || launchContext.workflow_id !== selection.planId
                || launchContext.workflow_revision_id !== selection.planRevisionId
                || launchContext.preparation_id !== selection.preparation.preparation_id
                || launchContext.normalized_request_sha256 !== selection.preparation.normalized_request_sha256
                || launchContext.validation_receipt_id !== selection.preparation.validation_receipt_id
            ) {
                throw new Error(`Issued launch context ${launchContext.launch_context_id} does not exactly bind its selected preparation authority.`);
            }
            return {
                launch: {
                    preparation_id: selection.preparation.preparation_id,
                    launch_context_id: launchContext.launch_context_id,
                } satisfies PreparationLaunchRequest,
                launchContext,
                sourceDestination: selection.sourceDestination,
            };
        }));
        return {
            launches: resolved.map((item) => item.launch),
            launchContexts: resolved.flatMap((item) => item.launchContext ? [{
                launchContext: item.launchContext,
                sourceDestination: item.sourceDestination,
            }] : []),
        };
    };

    const launchMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker ?? 'Mutation authority is unavailable.');
            const selectionError = preparationSelectionError(selectedPreparations, selectionAuthorityKey);
            if (selectionError) throw new Error(selectionError);
            const { launches, launchContexts } = await buildPreparationLaunches(selectedPreparations);
            const group = await launchDomainRunGroup(...scope, launches);
            return { group, launchContexts };
        },
        onSuccess: ({ group, launchContexts }) => {
            setIssuedLaunchContexts(launchContexts);
            setSelectedPreparations([]);
            setRetryPreparationByRunId({});
            queryClient.setQueryData(['domain-run-group', ...scope, group.run_group_id], group);
            setActiveRunGroupId(group.run_group_id);
            setRunGroupLookupId(group.run_group_id);
        },
    });
    const retryMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker ?? 'Mutation authority is unavailable.');
            const group = runGroupQuery.data;
            const mappingError = retryPreparationMappingError(
                group,
                selectedPreparations,
                retryPreparationByRunId,
                selectionAuthorityKey,
            );
            if (mappingError) throw new Error(mappingError);
            const selectionsById = new Map(selectedPreparations.map((selection) => [
                selection.preparation.preparation_id,
                selection,
            ]));
            const mappedSelections = eligibleFailedRuns(group).map((run) => {
                const selection = selectionsById.get(retryPreparationByRunId[run.run_id]);
                if (!selection) throw new Error(`Eligible failed run ${run.run_id} has no selected replacement preparation.`);
                return selection;
            });
            const { launches, launchContexts } = await buildPreparationLaunches(mappedSelections);
            const replacements = eligibleFailedRuns(group).map((run, index) => ({
                run_id: run.run_id,
                preparation_id: launches[index].preparation_id,
                launch_context_id: launches[index].launch_context_id,
            }));
            const updated = await retryDomainRunGroup(
                ...scope,
                group!.run_group_id,
                group!.generation,
                replacements,
            );
            return { group: updated, launchContexts };
        },
        onSuccess: ({ group, launchContexts }) => {
            setIssuedLaunchContexts(launchContexts);
            setSelectedPreparations([]);
            setRetryPreparationByRunId({});
            queryClient.setQueryData(['domain-run-group', ...scope, group.run_group_id], group);
        },
    });
    const resubmitMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker ?? 'Mutation authority is unavailable.');
            const group = runGroupQuery.data;
            if (!group || !TERMINAL_STATES.has(group.state)) {
                throw new Error('Load a terminal Run Group before resubmitting.');
            }
            const selectionError = preparationSelectionError(selectedPreparations, selectionAuthorityKey);
            if (selectionError) throw new Error(selectionError);
            const { launches, launchContexts } = await buildPreparationLaunches(selectedPreparations);
            const resubmitted = await resubmitDomainRunGroup(
                ...scope,
                group.run_group_id,
                group.generation,
                launches,
            );
            return { group: resubmitted, launchContexts };
        },
        onSuccess: ({ group, launchContexts }) => {
            setIssuedLaunchContexts(launchContexts);
            setSelectedPreparations([]);
            setRetryPreparationByRunId({});
            queryClient.setQueryData(['domain-run-group', ...scope, group.run_group_id], group);
            setActiveRunGroupId(group.run_group_id);
            setRunGroupLookupId(group.run_group_id);
        },
    });
    const cloneMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker ?? 'Mutation authority is unavailable.');
            const group = runGroupQuery.data;
            if (!group || !domainRevisionId) throw new Error('Load one exact Run Group and current Domain revision.');
            if (!cloneSourceRunId || !cloneSourceAttemptId) throw new Error('Select one exact source run and attempt.');
            if (!clonePlanName.trim() || !cloneChangeSummary.trim()) throw new Error('Plan name and change summary are required.');
            return cloneDomainRunIntent(...scope, group.run_group_id, {
                expected_run_group_generation: group.generation,
                source_run_id: cloneSourceRunId,
                source_attempt_id: cloneSourceAttemptId,
                new_workflow_name: clonePlanName.trim(),
                change_summary: cloneChangeSummary.trim(),
                expected_domain_revision_id: domainRevisionId,
            });
        },
        onSuccess: (receipt) => {
            setCloneReceipt(receipt);
            void queryClient.invalidateQueries({ queryKey: ['domain-workflow-plans', ...scope] });
            setSelectedPlanId(receipt.new_workflow_plan_id);
        },
    });
    const cancelMutation = useMutation({
        mutationFn: () => {
            const group = runGroupQuery.data as DomainRunGroup;
            return cancelDomainRunGroup(...scope, group.run_group_id, group.generation, cancelReason.trim());
        },
        onSuccess: (command) => {
            void queryClient.invalidateQueries({
                queryKey: ['domain-run-group', ...scope, command.run_group_id],
            });
        },
    });
    const reopenResultMutation = useMutation({
        mutationFn: (receiptId: string) => reopenDomainResult(...scope, receiptId),
        onSuccess: (surface) => setResultSurfaces((current) => ({ ...current, [surface.receipt_id]: surface })),
    });

    const selectedPlan = planQuery.data;
    const selectedRevision = revisionsQuery.data?.items.find((revision) => revision.revision_id === selectedPlanRevisionId) ?? null;
    const runGroup = runGroupQuery.data;
    const cloneSourceRun = runGroup?.runs.find((run) => run.run_id === cloneSourceRunId) ?? null;
    const cloneSourceAttempt = cloneSourceRun?.attempts.find((attempt) => attempt.attempt_id === cloneSourceAttemptId) ?? null;
    const cloneBlocker = !runGroup
        ? 'Load one exact Run Group.'
        : !domainRevisionId
            ? 'The exact current Domain revision is unavailable.'
            : !cloneSourceRun || !cloneSourceAttempt
                ? 'Select one exact source run and attempt.'
                : !clonePlanName.trim() || !cloneChangeSummary.trim()
                    ? 'Plan name and change summary are required.'
                    : null;
    const retryEligibleRuns = eligibleFailedRuns(runGroup);
    const retryBlocker = retryPreparationMappingError(
        runGroup,
        selectedPreparations,
        retryPreparationByRunId,
        selectionAuthorityKey,
    );
    const resubmitBlocker = !runGroup || !TERMINAL_STATES.has(runGroup.state)
        ? 'Load a terminal Run Group before resubmitting.'
        : selectedPreparationBlocker;
    const typedPreparationCount = selectedPreparations.filter(
        (selection) => selection.launchMode === 'typed_launcher_handoff',
    ).length;
    const managedPreparationCount = selectedPreparations.filter(
        (selection) => selection.launchMode === 'managed_materialization',
    ).length;
    const receiptIds = Array.from(new Set((runGroup?.runs ?? []).flatMap((run) => run.attempts.flatMap((attempt) =>
        Array.from(collectReceiptIds(attempt.terminal_receipt))))));
    const receiptLaunchContextIds = new Map<string, Set<string>>();
    (runGroup?.runs ?? []).forEach((run) => run.attempts.forEach((attempt) => {
        const launchContextId = attempt.launch_context?.launch_context_id;
        if (!launchContextId) return;
        collectReceiptIds(attempt.terminal_receipt).forEach((receiptId) => {
            const ids = receiptLaunchContextIds.get(receiptId) ?? new Set<string>();
            ids.add(launchContextId);
            receiptLaunchContextIds.set(receiptId, ids);
        });
    }));
    const activeError = capabilitiesQuery.error
        ?? plansQuery.error
        ?? planQuery.error
        ?? revisionsQuery.error
        ?? initializeBindingMutation.error
        ?? reverifyBindingMutation.error
        ?? createPlanMutation.error
        ?? saveDraftMutation.error
        ?? publishRevisionMutation.error
        ?? prepareMutation.error
        ?? launchMutation.error
        ?? retryMutation.error
        ?? resubmitMutation.error
        ?? cloneMutation.error
        ?? cancelMutation.error
        ?? reopenResultMutation.error;

    return (
        <div className="space-y-4">
            <Panel title="Binding health and mutation authority">
                <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
                    <KeyValue label="Binding health" value={binding?.provisioning_state ?? (bindingQuery.isLoading ? 'loading' : 'unavailable')} />
                    <KeyValue label="Command state" value={binding?.command_state} />
                    <KeyValue label="Binding revision" value={binding?.binding_revision_id} />
                    <KeyValue label="Domain revision" value={domainRevisionId} />
                    <KeyValue label="Selected state revision" value={selectedStateRevisionId} />
                    <KeyValue label="Mutation state" value={canMutate ? 'enabled' : 'disabled'} />
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                        type="button"
                        className={BUTTON_CLASS}
                        disabled={!domainRevisionId || initializeBindingMutation.isPending}
                        onClick={() => initializeBindingMutation.mutate()}
                    >Initialize binding</button>
                    <button
                        type="button"
                        className={BUTTON_CLASS}
                        disabled={!domainRevisionId || !binding?.binding_revision_id || reverifyBindingMutation.isPending}
                        onClick={() => reverifyBindingMutation.mutate()}
                    >Reverify exact revision</button>
                    <button type="button" className={BUTTON_CLASS} onClick={() => bindingQuery.refetch()}>Refresh authority</button>
                    <span className={`text-xs ${canMutate ? 'text-emerald-300' : 'text-amber-200'}`}>
                        {canMutate ? 'Exact acknowledged authority is current.' : mutationBlocker}
                    </span>
                </div>
                <ErrorBanner error={bindingQuery.error} />
                <ErrorBanner error={initializeBindingMutation.error} />
            </Panel>

            <ErrorBanner error={activeError ?? draftError} />

            <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="Workflow Plan authority">
                    <div className="space-y-3">
                        <label className="block text-xs text-content-secondary">Plan selector
                            <select className={`${INPUT_CLASS} mt-1`} value={selectedPlanId} onChange={(event) => {
                                setSelectedPlanId(event.target.value);
                            }}>
                                <option value="">Select a Workflow Plan</option>
                                {(plansQuery.data?.items ?? []).map((plan) => (
                                    <option key={plan.plan_id} value={plan.plan_id}>{plan.name} · {plan.capability_id}</option>
                                ))}
                            </select>
                        </label>
                        <div className="grid gap-2 sm:grid-cols-2">
                            <input className={INPUT_CLASS} value={planName} onChange={(event) => setPlanName(event.target.value)} placeholder="New Plan name" />
                            <select className={INPUT_CLASS} value={capabilityId} onChange={(event) => setCapabilityId(event.target.value)} disabled={capabilitiesQuery.isLoading}>
                                <option value="">Select a server-advertised capability</option>
                                {(capabilitiesQuery.data?.items ?? []).map((capability) => (
                                    <option key={capability.capability_id} value={capability.capability_id}>
                                        {capability.label} · {capability.launch_mode}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <button type="button" className={BUTTON_CLASS} disabled={!canMutate || !planName.trim() || !capabilityId || createPlanMutation.isPending} onClick={() => createPlanMutation.mutate()}>Create Plan under exact Domain</button>
                        {!capabilitiesQuery.isLoading && (capabilitiesQuery.data?.items ?? []).length === 0 && (
                            <p className="rounded-md border border-dashed border-border-primary p-3 text-xs text-content-muted">
                                The server advertises no accepted plannable capability for this exact Domain revision. Arbitrary capability IDs are not accepted.
                            </p>
                        )}
                        {selectedPlan && (
                            <dl className="grid gap-2 sm:grid-cols-2">
                                <KeyValue label="Plan ID" value={selectedPlan.plan_id} />
                                <KeyValue label="Capability" value={selectedPlan.capability_id} />
                                <KeyValue label="Pinned launch mode" value={pinnedPlanCapability?.launch_mode} />
                                <KeyValue label="Pinned native destination" value={pinnedPlanCapability?.canonical_source_destination} />
                                <KeyValue label="Pinned parameter schema" value={parameterSchema.schemaId} />
                                <KeyValue label="Pinned contract SHA-256" value={selectedPlan.capability_contract_sha256} />
                                <KeyValue label="Head generation" value={selectedPlan.head_generation} />
                                <KeyValue label="Current revision" value={selectedPlan.current_revision_id} />
                            </dl>
                        )}
                    </div>
                </Panel>

                <Panel title="Immutable Plan revision selector">
                    <div className="space-y-3">
                        <select className={INPUT_CLASS} value={selectedPlanRevisionId} onChange={(event) => {
                            setSelectedPlanRevisionId(event.target.value);
                        }} disabled={!selectedPlanId}>
                            <option value="">Select an immutable Plan revision</option>
                            {(revisionsQuery.data?.items ?? []).map((revision) => (
                                <option key={revision.revision_id} value={revision.revision_id}>Revision {revision.revision_number} · {revision.revision_id}</option>
                            ))}
                        </select>
                        {selectedRevision ? (
                            <>
                                <dl className="grid gap-2 sm:grid-cols-2">
                                    <KeyValue label="Revision ID" value={selectedRevision.revision_id} />
                                    <KeyValue label="Parent revision" value={selectedRevision.parent_revision_id} />
                                    <KeyValue label="Payload digest" value={selectedRevision.payload_sha256} />
                                    <KeyValue label="Dependency digest" value={selectedRevision.dependency_graph_sha256} />
                                </dl>
                                <details className="rounded-md border border-border-primary p-3">
                                    <summary className="cursor-pointer text-xs font-semibold text-content-secondary">Immutable requested settings readback</summary>
                                    <div className="mt-3">
                                        <SettingsReadback
                                            requested={isObject(selectedRevision.payload.parameters)
                                                ? selectedRevision.payload.parameters as JsonObject
                                                : {}}
                                        />
                                    </div>
                                </details>
                            </>
                        ) : <p className="text-xs text-content-muted">No immutable Plan revision is selected.</p>}
                    </div>
                </Panel>
            </div>

            {selectedPlan && (
                <Panel title="Plan draft and publication">
                    <p className="mb-2 text-xs text-content-muted">Draft edits are mutable. Preparation and launch use only a separately published immutable Plan revision.</p>
                    {parameterSchema.fields !== null && (
                        <dl className="mb-3 grid gap-2 sm:grid-cols-3">
                            <KeyValue label="Pinned parameter schema" value={parameterSchema.schemaId} />
                            <KeyValue label="Schema source pin" value={parameterSchema.sourcePin} />
                            <KeyValue label="Unknown fields" value={parameterSchema.unknownFieldsPolicy} />
                        </dl>
                    )}
                    {parameterSchema.fields === null ? (
                        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
                            Typed settings are unavailable: {parameterSchema.error}. Raw JSON submission is disabled.
                        </div>
                    ) : parameterSchema.fields.length === 0 ? (
                        <p className="rounded-md border border-border-primary p-3 text-xs text-content-muted">
                            This capability declares no configurable parameters.
                        </p>
                    ) : (
                        <div className="grid gap-3 md:grid-cols-2">
                            {visibleParameterFields.map((field) => (
                                <ParameterControl
                                    key={field.name}
                                    field={field}
                                    value={parameterValues[field.name]}
                                    onChange={(value) => setParameterValues((current) => {
                                        const next = { ...current };
                                        if (value === undefined) delete next[field.name];
                                        else next[field.name] = value;
                                        return next;
                                    })}
                                />
                            ))}
                        </div>
                    )}
                    {hiddenConditionalFieldCount > 0 && (
                        <p className="mt-3 rounded-md border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content-muted">
                            {hiddenConditionalFieldCount} conditionally inapplicable setting{hiddenConditionalFieldCount === 1 ? ' is' : 's are'} hidden. Its persisted value is retained so the closed server schema remains complete.
                        </p>
                    )}
                    {parameterSchema.fields && typedDraftError && (
                        <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
                            Publication is blocked until the persisted typed draft satisfies the server schema: {typedDraftError}
                        </div>
                    )}
                    <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_auto]">
                        <input className={INPUT_CLASS} value={changeSummary} onChange={(event) => setChangeSummary(event.target.value)} placeholder="Required publication change summary" />
                        <button type="button" className={BUTTON_CLASS} disabled={!canMutate || !parameterSchema.fields || saveDraftMutation.isPending} onClick={() => saveDraftMutation.mutate()}>Save typed settings</button>
                        <button type="button" className={BUTTON_CLASS} disabled={!canMutate || !parameterSchema.fields || Boolean(typedDraftError) || !changeSummary.trim() || saveDraftMutation.isPending || publishRevisionMutation.isPending} onClick={() => publishRevisionMutation.mutate()}>Publish immutable revision</button>
                    </div>
                </Panel>
            )}

            <Panel title="Prepare, inspect, select, then explicitly launch">
                <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                    <div className="text-xs text-content-secondary">
                        <p className="font-semibold">Exact immutable input Dataset revisions for the next preparation</p>
                        {inputDatasetRevisionIds.length ? (
                            <div className="mt-1 flex flex-wrap gap-2">
                                {inputDatasetRevisionIds.map((revisionId) => (
                                    <span key={revisionId} className="rounded-md border border-border-primary bg-surface px-2 py-1 font-mono text-[11px] text-content-primary">{revisionId}</span>
                                ))}
                            </div>
                        ) : (
                            <p className="mt-1 text-content-muted">No Dataset revision selected. Choose exact immutable revisions in the Datasets section if this workflow requires inputs.</p>
                        )}
                        <Link className={`${BUTTON_CLASS} mt-2 inline-block`} to={contextHref('/ngs', { section: 'datasets' })}>
                            Select immutable Dataset sources
                        </Link>
                        <p className="mt-2">
                            Current Plan mode: <span className="font-mono text-content-primary">{currentPlanLaunchMode ?? 'unavailable'}</span>.
                            Prepared selections retain their own pinned mode when you switch Plans. Preparing another immutable revision adds its validated preparation to the selection. Duplicate preparation IDs are rejected.
                        </p>
                    </div>
                    <button type="button" className={PRIMARY_BUTTON_CLASS} disabled={!canMutate || !selectedPlan || !selectedRevision || !launchModeResolved || prepareMutation.isPending} onClick={() => prepareMutation.mutate()}>1. Prepare and add selected revision</button>
                </div>
                {selectedPreparations.length > 0 && (
                    <div className="mt-4 space-y-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-content-secondary">
                            <span>
                                Selected group: <strong className="text-content-primary">{selectedPreparations.length}</strong> preparations · {typedPreparationCount} typed handoff · {managedPreparationCount} managed materialization.
                            </span>
                            <button type="button" className={BUTTON_CLASS} onClick={() => {
                                setSelectedPreparations([]);
                                setRetryPreparationByRunId({});
                            }}>Clear selection</button>
                        </div>
                        <p className="text-xs text-content-muted">
                            Mixed typed and managed groups are supported because every preparation retains its explicit pinned mode. Unknown modes, stale Domain/state authority, duplicate IDs, and incomplete retry mappings are rejected before launch-context or Run Group mutation.
                        </p>
                        {selectedPreparations.map((selection, index) => (
                            <div key={selection.preparation.preparation_id} className="rounded-md border border-border-primary bg-surface p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <dl className="grid flex-1 gap-2 md:grid-cols-4">
                                        <KeyValue label={`Preparation ${index + 1}`} value={selection.preparation.preparation_id} />
                                        <KeyValue label="Pinned launch mode" value={selection.launchMode} />
                                        <KeyValue label="Native destination" value={selection.sourceDestination} />
                                        <KeyValue label="Plan" value={selection.planId} />
                                        <KeyValue label="Plan revision" value={selection.planRevisionId} />
                                        <KeyValue label="Validation status" value={selection.preparation.status} />
                                        <KeyValue label="Validation receipt" value={selection.preparation.validation_receipt_id} />
                                        <KeyValue label="Request digest" value={selection.preparation.normalized_request_sha256} />
                                        <KeyValue label="Expected runs" value={selection.preparation.expected_cardinality} />
                                    </dl>
                                    <button type="button" className={BUTTON_CLASS} onClick={() => removeSelectedPreparation(selection.preparation.preparation_id)}>Remove</button>
                                </div>
                                <details className="mt-3 rounded-md border border-border-secondary p-3">
                                    <summary className="cursor-pointer text-xs font-semibold text-content-secondary">Requested/effective settings and validation receipt</summary>
                                    <div className="mt-3 space-y-3">
                                        <SettingsReadback
                                            requested={selection.preparation.requested_settings}
                                            effective={selection.preparation.effective_settings}
                                        />
                                        <div>
                                            <p className="mb-1 text-xs font-semibold text-content-secondary">Validation receipt payload</p>
                                            <pre className="max-h-64 overflow-auto rounded-md bg-surface-secondary p-3 text-[11px] text-content-primary">{JSON.stringify(selection.preparation.validation, null, 2)}</pre>
                                        </div>
                                    </div>
                                </details>
                            </div>
                        ))}
                        {selectedPreparationBlocker && (
                            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">Group launch blocked: {selectedPreparationBlocker}</p>
                        )}
                        <button type="button" className={PRIMARY_BUTTON_CLASS} disabled={!canMutate || Boolean(selectedPreparationBlocker) || launchMutation.isPending} onClick={() => launchMutation.mutate()}>2. Explicitly launch all {selectedPreparations.length} selected preparations</button>
                    </div>
                )}
                {issuedLaunchContexts.length > 0 && (
                    <div className="mt-3 space-y-3 rounded-md border border-blue-500/30 bg-blue-500/5 p-3 text-xs">
                        <p className="text-content-secondary">Fresh typed handoff contexts issued for the submitted preparation set:</p>
                        {issuedLaunchContexts.map(({ launchContext, sourceDestination }) => (
                            <div key={launchContext.launch_context_id} className="rounded border border-border-primary p-2">
                                <KeyValue label="Fresh launch context" value={launchContext.launch_context_id} />
                                <KeyValue label="Preparation" value={launchContext.preparation_id} />
                                <KeyValue label="Exact native destination" value={sourceDestination} />
                                <div className="mt-2 flex flex-wrap gap-2">
                                    <Link
                                        className={PRIMARY_BUTTON_CLASS}
                                        to={contextHref(appendLaunchContext(sourceDestination, launchContext.launch_context_id))}
                                    >Open exact typed native launcher</Link>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </Panel>

            <Panel title="Run Group, attempts, lineage, and results">
                <div className="flex flex-col gap-2 sm:flex-row">
                    <input className={INPUT_CLASS} value={runGroupLookupId} onChange={(event) => setRunGroupLookupId(event.target.value)} placeholder="Run Group ID" />
                    <button type="button" className={BUTTON_CLASS} disabled={!runGroupLookupId.trim()} onClick={() => setActiveRunGroupId(runGroupLookupId.trim())}>Load exact Run Group</button>
                    <button type="button" className={BUTTON_CLASS} disabled={!activeRunGroupId} onClick={() => runGroupQuery.refetch()}>Refresh status</button>
                    <Link className={BUTTON_CLASS} to={projectReturnUri}>Return to Project</Link>
                </div>
                <ErrorBanner error={runGroupQuery.error} />
                <div className="mt-4">
                    <ExperimentReferenceLinks
                        domainExperimentId={domainExperimentId}
                        stateRevisionId={selectedStateRevisionId || currentStateRevisionId}
                        title="Exact molecular references for this Domain Plans/Runs context"
                    />
                </div>
                {runGroup && (
                    <div className="mt-4 space-y-3">
                        <dl className="grid gap-2 md:grid-cols-4">
                            <KeyValue label="Run Group" value={runGroup.run_group_id} />
                            <KeyValue label="State" value={runGroup.state} />
                            <KeyValue label="Generation" value={runGroup.generation} />
                            <KeyValue label="Request digest" value={runGroup.request_sha256} />
                        </dl>
                        {runGroup.runs.map((run) => (
                            <div key={run.run_id} className="rounded-md border border-border-primary bg-surface p-3">
                                <dl className="grid gap-2 md:grid-cols-4">
                                    <KeyValue label="Workflow Run" value={run.run_id} />
                                    <KeyValue label="Preparation" value={run.preparation_id} />
                                    <KeyValue label="Run state" value={run.state} />
                                    <KeyValue label="Run generation" value={run.generation} />
                                </dl>
                                <div className="mt-3 space-y-2">
                                    {run.attempts.map((attempt) => (
                                        <div key={attempt.attempt_id} className="rounded border border-border-secondary p-2">
                                            <dl className="grid gap-2 md:grid-cols-5">
                                                <KeyValue label="Attempt" value={attempt.attempt_id} />
                                                <KeyValue label="Number" value={attempt.attempt_number} />
                                                <KeyValue label="State" value={attempt.state} />
                                                <KeyValue label="Canonical Job" value={attempt.canonical_job_id} />
                                                <KeyValue label="Launch context" value={attempt.launch_context?.launch_context_id} />
                                            </dl>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ))}
                        <div className="rounded-md border border-border-primary bg-surface p-3">
                            <h4 className="text-xs font-semibold uppercase tracking-wide text-content-muted">Clone exact run intent into a fresh Plan draft</h4>
                            <p className="mt-1 text-xs text-content-secondary">
                                This operation imports the exact immutable source Plan payload and pinned capability contract into a new generation-0 editable draft. It creates no preparation, launch context, dispatch, or Job.
                            </p>
                            <div className="mt-3 grid gap-3 md:grid-cols-2">
                                <label className="text-xs font-semibold text-content-secondary">Source Workflow Run
                                    <select
                                        className={`${INPUT_CLASS} mt-1`}
                                        value={cloneSourceRunId}
                                        onChange={(event) => {
                                            setCloneSourceRunId(event.target.value);
                                            setCloneSourceAttemptId('');
                                            setCloneReceipt(null);
                                        }}
                                    >
                                        <option value="">Select exact run</option>
                                        {runGroup.runs.filter((run) => run.attempts.length > 0).map((run) => (
                                            <option key={run.run_id} value={run.run_id}>{run.run_id} · {run.state}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="text-xs font-semibold text-content-secondary">Source Attempt
                                    <select
                                        className={`${INPUT_CLASS} mt-1`}
                                        value={cloneSourceAttemptId}
                                        onChange={(event) => { setCloneSourceAttemptId(event.target.value); setCloneReceipt(null); }}
                                        disabled={!cloneSourceRun}
                                    >
                                        <option value="">Select exact attempt</option>
                                        {(cloneSourceRun?.attempts ?? []).map((attempt) => (
                                            <option key={attempt.attempt_id} value={attempt.attempt_id}>#{attempt.attempt_number} · {attempt.attempt_id} · {attempt.state}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="text-xs font-semibold text-content-secondary">Fresh Plan name
                                    <input className={`${INPUT_CLASS} mt-1`} value={clonePlanName} onChange={(event) => setClonePlanName(event.target.value)} />
                                </label>
                                <label className="text-xs font-semibold text-content-secondary">Change summary
                                    <input className={`${INPUT_CLASS} mt-1`} value={cloneChangeSummary} onChange={(event) => setCloneChangeSummary(event.target.value)} />
                                </label>
                            </div>
                            {cloneBlocker && <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">Clone blocked: {cloneBlocker}</p>}
                            <button type="button" className={`${PRIMARY_BUTTON_CLASS} mt-3`} disabled={!canMutate || Boolean(cloneBlocker) || cloneMutation.isPending} onClick={() => cloneMutation.mutate()}>
                                {cloneMutation.isPending ? 'Cloning exact intent…' : 'Clone into fresh editable Plan draft'}
                            </button>
                            {cloneReceipt && (
                                <div className="mt-3 grid gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 md:grid-cols-3">
                                    <KeyValue label="Clone receipt" value={cloneReceipt.clone_receipt_id} />
                                    <KeyValue label="Fresh Plan" value={cloneReceipt.new_workflow_plan_id} />
                                    <KeyValue label="Fresh draft" value={cloneReceipt.new_draft_id} />
                                    <KeyValue label="Copied payload digest" value={cloneReceipt.copied_payload_sha256} />
                                    <KeyValue label="Lineage edge" value={cloneReceipt.lineage_edge_id} />
                                    <KeyValue label="Receipt digest" value={cloneReceipt.receipt_sha256} />
                                </div>
                            )}
                        </div>
                        {runGroup.state === 'failed' && (
                            <div className="rounded-md border border-border-primary bg-surface p-3">
                                <h4 className="text-xs font-semibold uppercase tracking-wide text-content-muted">Complete retry preparation mapping</h4>
                                <p className="mt-1 text-xs text-content-secondary">
                                    Map every eligible failed run to a selected immutable preparation. One preparation can supply fresh attempts for more than one failed run. Each typed attempt receives its own fresh launch context.
                                </p>
                                <div className="mt-3 space-y-2">
                                    {retryEligibleRuns.map((run) => {
                                        const mappedPreparationId = retryPreparationByRunId[run.run_id] ?? '';
                                        return (
                                            <label key={run.run_id} className="grid gap-1 text-xs text-content-secondary md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:items-center">
                                                <span className="font-mono text-content-primary">{run.run_id}</span>
                                                <select
                                                    className={INPUT_CLASS}
                                                    value={mappedPreparationId}
                                                    onChange={(event) => {
                                                        const nextPreparationId = event.target.value;
                                                        setRetryPreparationByRunId((current) => {
                                                            if (!nextPreparationId) {
                                                                const next = { ...current };
                                                                delete next[run.run_id];
                                                                return next;
                                                            }
                                                            return { ...current, [run.run_id]: nextPreparationId };
                                                        });
                                                    }}
                                                >
                                                    <option value="">Select replacement preparation</option>
                                                    {selectedPreparations.map((selection) => {
                                                        const preparationId = selection.preparation.preparation_id;
                                                        return (
                                                            <option key={preparationId} value={preparationId}>
                                                                {preparationId} · {selection.launchMode} · {selection.planRevisionId}
                                                            </option>
                                                        );
                                                    })}
                                                </select>
                                            </label>
                                        );
                                    })}
                                </div>
                                {retryBlocker && (
                                    <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">Retry blocked: {retryBlocker}</p>
                                )}
                            </div>
                        )}
                        {TERMINAL_STATES.has(runGroup.state) && resubmitBlocker && (
                            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">Resubmit blocked: {resubmitBlocker}</p>
                        )}
                        <div className="grid gap-2 lg:grid-cols-[auto_auto_1fr_auto]">
                            <button type="button" className={BUTTON_CLASS} disabled={!canMutate || Boolean(retryBlocker) || retryMutation.isPending} onClick={() => retryMutation.mutate()}>Retry all {retryEligibleRuns.length} eligible failed runs</button>
                            <button type="button" className={BUTTON_CLASS} disabled={!canMutate || Boolean(resubmitBlocker) || resubmitMutation.isPending} onClick={() => resubmitMutation.mutate()}>Resubmit all {selectedPreparations.length} selected preparations</button>
                            <input className={INPUT_CLASS} value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} aria-label="Cancellation reason" />
                            <button type="button" className={BUTTON_CLASS} disabled={!canMutate || cancelMutation.isPending || TERMINAL_STATES.has(runGroup.state) || !cancelReason.trim()} onClick={() => cancelMutation.mutate()}>Cancel Run Group</button>
                        </div>
                        <div className="rounded-md border border-border-primary p-3">
                            <h4 className="text-xs font-semibold uppercase tracking-wide text-content-muted">Terminal receipt result reopen</h4>
                            {receiptIds.length === 0 ? <p className="mt-2 text-xs text-content-muted">No result receipt IDs are present on terminal attempts yet.</p> : (
                                <div className="mt-2 space-y-2">
                                    {receiptIds.map((receiptId) => {
                                        const surface = resultSurfaces[receiptId];
                                        const launchContextIds = receiptLaunchContextIds.get(receiptId);
                                        const launchContextId = launchContextIds?.size === 1
                                            ? [...launchContextIds][0]
                                            : undefined;
                                        return (
                                            <div key={receiptId} className="flex flex-wrap items-center gap-2 text-xs">
                                                <span className="font-mono text-content-secondary">{receiptId}</span>
                                                <button type="button" className={BUTTON_CLASS} onClick={() => reopenResultMutation.mutate(receiptId)}>Resolve canonical result surface</button>
                                                {surface?.route && (
                                                    <Link
                                                        className={BUTTON_CLASS}
                                                        to={contextHref(internalRouteHref(surface.route), {
                                                            launch_context_id: launchContextId,
                                                            run_group_id: runGroup.run_group_id,
                                                        })}
                                                    >Reopen {surface.surface_kind} result</Link>
                                                )}
                                                {surface && !surface.route && <span className="text-amber-200">Surface unavailable ({surface.readiness})</span>}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </Panel>
        </div>
    );
}
