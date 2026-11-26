import { isAxiosError } from 'axios';
import { api } from './api';

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = Record<string, JsonValue>;

export interface NgsMolBioBindingStatus {
    schema: 'bms.ngs-molbio.binding-status.v1';
    project_id: string;
    project_revision_id: string;
    global_experiment_id: string;
    global_experiment_revision_id: string;
    domain_id: string;
    domain_revision_id: string;
    binding_revision_id: string;
    global_receipt_id: string | null;
    global_receipt_sha256: string | null;
    connector_command_id: string;
    command_state: string;
    acknowledgement_id: string | null;
    acknowledgement_sha256: string | null;
    local_state_id: string | null;
    provisioning_state: 'ready' | 'provisioning' | 'degraded';
    head_generation: number;
    created_at: string;
    updated_at: string;
}

export interface PreparedLaunchContext {
    schema: 'bms.launch-context.v2';
    launch_context_id: string;
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    workflow_id: string;
    workflow_revision_id: string;
    preparation_id: string;
    run_attempt_id: string | null;
    normalized_request_sha256: string;
    validation_receipt_id: string;
    validation_receipt_sha256: string;
    return_uri: string;
    source_receipt_id: string;
    state: string;
    canonical_job_id: string | null;
    binding_receipt: JsonObject | null;
    issued_at: string;
    expires_at: string;
}

export interface DomainRunAttempt {
    attempt_id: string;
    attempt_number: number;
    preparation_id: string;
    state: string;
    canonical_job_id: string | null;
    launch_context: PreparedLaunchContext | null;
    terminal_receipt: JsonObject | null;
}

export interface DomainWorkflowRun {
    run_id: string;
    preparation_id: string;
    state: string;
    generation: number;
    attempts: DomainRunAttempt[];
}

export interface DomainRunGroup {
    schema: 'bms.run-group.v1';
    run_group_id: string;
    request_sha256: string;
    state: string;
    generation: number;
    runs: DomainWorkflowRun[];
    cancellation_receipt?: JsonObject;
    created_at: string;
    updated_at: string;
}

export interface DomainDatasetHead {
    schema: 'bms.dataset-head.v1';
    project_id?: string;
    global_experiment_id?: string;
    domain_id?: string;
    dataset_id: string;
    name: string;
    dataset_kind: string;
    current_revision_id: string | null;
    head_generation: number;
    lifecycle_state: string;
    normalized_request_sha256?: string | null;
    created_at: string;
    updated_at?: string;
}

export interface DomainDatasetRevisionSummary {
    revision_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    revision_sha256: string;
    created_at: string;
}
export type LineageRole = 'references' | 'uses_input' | 'produced' | 'validated_by';
export type RecordKind = 'note' | 'observation' | 'decision' | 'conclusion';

export type ProjectStatus = 'draft' | 'active' | 'on_hold' | 'completed' | 'archived';

export interface ProjectSummaryCounts {
    in_silico: number;
    molecular_sequences: number;
    molecular_operations: number;
    ngs_ont_and_alignments: number;
}

export interface ProjectHubCapabilities {
    has_in_silico: boolean;
    has_molbio: boolean;
    has_ngs_ont: boolean;
    has_stats: boolean;
}

export interface ProjectHubDetachTarget {
    method: string;
    target_id: string;
    domain_experiment_id: string;
    expected_state_revision_id: string;
}

export interface ProjectHubItem {
    id: string;
    title: string;
    subtitle: string;
    kind: string;
    badges: string[];
    status: string;
    experiment_id: string | null;
    updated_at: string | null;
    open_href: string;
    detach: ProjectHubDetachTarget | null;
}

export interface ProjectHubExperimentSummary {
    id: string;
    name: string;
    status: string;
    item_count: number;
}

export interface ProjectHubLinkedLocalProject {
    id: string;
    name: string;
    description: string;
    lifecycle_state: string;
    updated_at: string | null;
    counts?: ProjectSummaryCounts;
}

export interface ProjectHubResponse {
    capabilities: ProjectHubCapabilities;
    project: {
        id: string;
        name: string;
        description: string;
        lifecycle_state: string;
        project_scope: 'global' | 'ngs_molbio_local';
        created_at: string | null;
        updated_at: string | null;
        experiments: ProjectHubExperimentSummary[];
        linked_local_projects: ProjectHubLinkedLocalProject[];
    };
    counts: {
        in_silico: number;
        molecular_sequences: number;
        molecular_operations: number;
        ngs_ont: number;
        alignments: number;
    };
    sections: {
        in_silico: ProjectHubItem[];
        molecular_sequences: ProjectHubItem[];
        molecular_operations: ProjectHubItem[];
        ngs_ont_and_alignments: ProjectHubItem[];
    };
}

export interface ProjectSequenceShelfItem {
    id: string;
    sequence_id?: string;
    revision_id?: string | null;
    name: string;
    description?: string | null;
    sequence_type?: string;
    length?: number;
    gc_content?: number | null;
    is_circular?: boolean;
    circular?: boolean;
    topology?: 'circular' | 'linear';
    feature_count?: number;
    tags?: string[];
    created_at?: string | null;
    updated_at?: string | null;
    domain_experiment_id?: string | null;
    state_revision_id?: string | null;
    reopen_href?: string | null;
    is_head_editable?: boolean;
}

export interface ProjectSequencesResponse {
    project_id: string;
    items: ProjectSequenceShelfItem[];
}

export interface ProjectListItem {
    id: string;
    kind: 'project';
    storage_kind: 'workspace';
    project_id: string;
    workspace_id: string;
    parent_id: string | null;
    current_revision_id: string | null;
    head_generation: number;
    lifecycle_state: string;
    status: string;
    project_scope?: 'global' | 'ngs_molbio_local' | null;
    name: string;
    description: string;
    payload: JsonObject | null;
    counts?: ProjectSummaryCounts;
    active_experiment_count?: number;
    unresolved_failure_count?: number;
    created_at: string;
    updated_at: string;
}

export interface ProjectListPage {
    items: ProjectListItem[];
    next_cursor: string | null;
    capabilities?: ProjectHubCapabilities;
}

export interface ProjectSearchOptions {
    query?: string;
    status?: string;
    archive?: 'active' | 'archived' | 'all';
    cursor?: string;
    limit?: number;
    projectScope?: 'global' | 'ngs_molbio_local' | 'all';
    signal?: AbortSignal;
}

export interface DomainAdapterDescriptor {
    adapter_id: string;
    adapter_version: string | number;
    domain_kind: string;
    entity_kind: string;
    display_name?: string;
}

export interface DomainAdapterRegistry {
    schema: 'bms.global.adapter-registry.v1';
    adapters: DomainAdapterDescriptor[];
}

export interface AdapterEntityProjection {
    adapter_id: string;
    entity_kind: string;
    entity_id: string;
    label: string;
    canonical_state: string;
    attachable: boolean;
    reason: string | null;
    reopen_uri: string;
    metadata: JsonObject;
}

export interface AdapterSearchResult {
    schema: 'bms.global.adapter-search.v1';
    adapter_id: string;
    adapter_version: string | number;
    items: AdapterEntityProjection[];
    next_cursor: string | null;
}

export interface AttachExistingRequest {
    adapter_id: string;
    entity_id: string;
    operation: 'attach_reference' | 'bind_input' | 'link_output' | 'attach_evidence';
    role: LineageRole;
    note?: string | null;
    expected_head_generation: number;
}

export interface AttachmentReceipt {
    schema: 'bms.global.attachment-receipt.v1';
    attachment_receipt_id: string;
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    adapter_id: string;
    adapter_version: string | number;
    source_receipt_id: string;
    source_receipt: JsonObject;
    lineage_edge_id: string;
    operation: AttachExistingRequest['operation'];
    role: LineageRole;
    note: string | null;
    project_head_generation: number;
    normalized_request_sha256: string;
    attached_at: string;
}

export interface LaunchContext {
    schema: 'bms.launch-context.v1' | 'bms.launch-context.v2';
    launch_context_id: string;
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    workflow_id: string | null;
    workflow_revision_id: string | null;
    preparation_id?: string | null;
    run_attempt_id?: string | null;
    normalized_request_sha256?: string | null;
    validation_receipt_id?: string | null;
    validation_receipt_sha256?: string | null;
    pinned_gpu: number | null;
    return_uri: string;
    source_receipt_id: string;
    state: 'issued' | 'reserved' | 'claimed' | 'consumed';
    canonical_job_id?: string | null;
    recovery_job_id?: string | null;
    binding_receipt?: JsonObject | null;
    pinned_scheduler?: JsonObject | null;
    issued_at: string;
    expires_at: string;
}

export interface ProjectV1CreateRequest {
    schema: 'bms.project.v1';
    name: string;
    description?: string;
    research_objective?: string;
    owner?: string | null;
    contributors?: string[];
    tags?: string[];
    status?: 'draft' | 'active' | 'on_hold' | 'completed';
    start_date?: string | null;
    target_end_date?: string | null;
    created_by?: string | null;
    change_summary?: string;
    project_scope?: 'global' | 'ngs_molbio_local';
}

export interface ProjectV2CreateRequest {
    schema: 'bms.project.v2';
    project_scope: 'global' | 'ngs_molbio_local';
    name: string;
    description?: string;
    research_objective?: string;
    owner?: string | null;
    contributors?: string[];
    tags?: string[];
    status?: 'draft' | 'active' | 'on_hold' | 'completed' | 'archived';
    start_date?: string | null;
    target_end_date?: string | null;
    created_by?: string | null;
    change_summary?: string;
}

export type ProjectCreateRequest = ProjectV1CreateRequest | ProjectV2CreateRequest;

export interface GlobalExperimentV1CreateRequest {
    schema: 'bms.global-experiment.v1';
    name: string;
    objective?: string;
    scientific_question?: string;
    hypothesis?: string | null;
    description?: string;
    status?: 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked';
    priority?: 'low' | 'normal' | 'high' | 'critical';
    tags?: string[];
    success_criteria?: string[];
    change_summary?: string;
}

export interface GlobalExperimentV2CreateRequest {
    schema: 'bms.global-experiment.v2';
    name: string;
    objective?: string;
    scientific_question?: string;
    hypothesis?: string | null;
    description?: string;
    status?: 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked' | 'archived';
    priority?: 'low' | 'normal' | 'high' | 'critical';
    tags?: string[];
    shared_source_receipt_ids?: string[];
    shared_dataset_ids?: string[];
    comparison_plan?: string | null;
    success_criteria?: string[];
    review_summary?: string | null;
    conclusion?: string | null;
    change_summary?: string;
}

export type GlobalExperimentCreateRequest = GlobalExperimentV1CreateRequest | GlobalExperimentV2CreateRequest;

export interface DomainExperimentV2CreateRequest {
    schema: 'bms.domain-experiment.v2';
    domain_kind: 'protein_in_silico' | 'ngs_molbio';
    domain_contract_version: '2';
    name: string;
    objective: string;
    status: 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked' | 'archived';
    tags: string[];
    source_receipt_ids: string[];
    dataset_revision_ids: string[];
    change_summary: string;
    domain_payload: JsonObject;
}

export interface DomainExperimentV4CreateRequest {
    schema: 'bms.domain-experiment.v4';
    domain_kind: 'protein_in_silico' | 'ngs_molbio';
    domain_contract_version: '3';
    name: string;
    objective: string;
    status: 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked';
    tags: string[];
    source_receipt_ids: string[];
    dataset_revision_ids: string[];
    change_summary: string;
    domain_payload: JsonObject;
}

export type DomainExperimentCreateRequest = DomainExperimentV2CreateRequest | DomainExperimentV4CreateRequest;

export interface HierarchyMutationResult {
    id: string;
    kind: string;
    storage_kind: string;
    project_id: string;
    workspace_id: string;
    parent_id: string | null;
    current_revision_id: string | null;
    head_generation: number;
    lifecycle_state: string;
    status: string;
    name: string;
    description: string;
    payload: JsonObject | null;
    created_at: string;
    updated_at: string;
    experiment_id?: string;
    global_experiment_id?: string;
    domain_experiment_id?: string;
    domain_kind?: string;
}

export type HierarchyPatch = Record<string, JsonValue> & { expected_head_generation: number };

export interface ResearchRecordSubject {
    projectId: string;
    globalExperimentId?: string;
    domainExperimentId?: string;
}

export interface ResearchRecordRequest {
    record_kind: RecordKind;
    body: string;
    author?: string | null;
    source_receipt_ids?: string[];
    supersedes_record_id?: string | null;
}

export interface FrustraMpnnExperimentScopeItem {
    result_receipt_id: string;
    parent_job_id: string | null;
    invocation_id: string | null;
    candidate_id: string;
    operator_label: string;
    source_identity: {
        design_id: string | null;
        artifact_id: string;
        artifact_sha256: string;
        candidate_id: string;
    };
    state: 'completed' | 'failed' | 'missing' | 'skipped';
    diagnostic: string | null;
    statistics_analysis: {
        state: 'not_started' | 'queued' | 'running' | 'completed' | 'failed';
        diagnostic: string | null;
    };
    manifest_sha256: string | null;
    content_digest: string;
    reopen_uri: string | null;
}

export interface FrustraMpnnExperimentScope {
    schema: 'bms.project-frustrampnn-result-scope.v1';
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    global_experiment_revision_id: string;
    domain_revision_id: string;
    items: FrustraMpnnExperimentScopeItem[];
    count: number;
    bounded: true;
}

export interface ProteinProjectSetupCapability {
    capability_id: string;
    label: string;
    state: 'ready';
    adapter_id: string;
    native_owner_id: string;
    setup_destination: string;
    source_requirements: string[];
    follow_up_compatible_capability_ids: string[];
}

export interface ProteinProjectCapabilityInventory {
    schema: 'bms.protein-project-workflow-picker.v1';
    capabilities: ProteinProjectSetupCapability[];
}

export interface ProjectWorkflowSetupCreate {
    schema: 'bms.project-workflow-setup.create.v1';
    relationship_kind: 'primary' | 'follow_up';
    global_experiment_id: string | null;
    experiment?: { name: string; objective: string };
    domain_kind: 'protein_in_silico' | 'ngs_molbio';
    capability_id: string;
}

export interface ProjectWorkflowSetupNavigation {
    schema: 'bms.project-workflow-setup.create-response.v1';
    setup_context_id: string;
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    relationship_kind: 'primary' | 'follow_up';
    capability_id: string;
    state: 'open' | 'submitted' | 'deleted';
    validation_state: 'incomplete' | 'ready' | 'invalid';
    setup_destination: string;
    return_uri: string;
    generation: number;
}

export interface ProjectWorkflowSetup extends Omit<ProjectWorkflowSetupNavigation, 'schema'> {
    schema: 'bms.project-workflow-setup.detail.v1';
    project_label: string;
    experiment_label: string;
    workflow_label: string;
    draft: JsonObject;
    field_errors: Record<string, string>;
    diagnostics: JsonObject;
    preparation_id?: string;
    launch_context_id?: string;
}

const segment = (value: string) => encodeURIComponent(value);

type UnknownRecord = Record<string, unknown>;

function exactRecord(value: unknown, label: string, required: readonly string[], optional: readonly string[] = []): UnknownRecord {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`${label} must be an object.`);
    const record = value as UnknownRecord;
    const allowed = new Set([...required, ...optional]);
    for (const key of required) if (!(key in record)) throw new Error(`${label}.${key} is required.`);
    for (const key of Object.keys(record)) if (!allowed.has(key)) throw new Error(`${label}.${key} is not permitted.`);
    return record;
}

function requireString(value: unknown, label: string): string {
    if (typeof value !== 'string') throw new Error(`${label} must be a string.`);
    return value;
}

function requireNullableString(value: unknown, label: string): string | null {
    if (value === null) return null;
    return requireString(value, label);
}

function requireNumber(value: unknown, label: string): number {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be a finite number.`);
    return value;
}

function requireInteger(value: unknown, label: string): number {
    const number = requireNumber(value, label);
    if (!Number.isInteger(number)) throw new Error(`${label} must be an integer.`);
    return number;
}

function requireBoolean(value: unknown, label: string): boolean {
    if (typeof value !== 'boolean') throw new Error(`${label} must be a boolean.`);
    return value;
}

function requireLiteral<T extends string>(value: unknown, label: string, values: readonly T[]): T {
    if (typeof value !== 'string' || !values.includes(value as T)) throw new Error(`${label} has an unsupported value.`);
    return value as T;
}

function requireArray<T>(value: unknown, label: string, parse: (item: unknown, itemLabel: string) => T): T[] {
    if (!Array.isArray(value)) throw new Error(`${label} must be an array.`);
    return value.map((item, index) => parse(item, `${label}[${index}]`));
}

function requireJsonValue(value: unknown, label: string): JsonValue {
    if (value === null) return null;
    if (typeof value === 'string' || typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (Array.isArray(value)) return value.map((item, index) => requireJsonValue(item, `${label}[${index}]`));
    if (typeof value === 'object') {
        const parsed: Record<string, JsonValue> = {};
        for (const [key, item] of Object.entries(value as UnknownRecord)) parsed[key] = requireJsonValue(item, `${label}.${key}`);
        return parsed;
    }
    throw new Error(`${label} is not valid JSON.`);
}

function requireJsonObject(value: unknown, label: string): JsonObject {
    const parsed = requireJsonValue(value, label);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object.`);
    return parsed;
}

function requireSha256(value: unknown, label: string): string {
    const text = requireString(value, label);
    if (!/^[0-9a-f]{64}$/.test(text)) throw new Error(`${label} must be a lowercase SHA-256 digest.`);
    return text;
}

function requireStringArray(value: unknown, label: string): string[] {
    return requireArray(value, label, requireString);
}

export function parseLaunchContext(value: unknown): LaunchContext {
    const label = 'launch context';
    const record = exactRecord(
        value,
        label,
        ['schema', 'launch_context_id', 'project_id', 'global_experiment_id', 'domain_experiment_id', 'workflow_id', 'workflow_revision_id', 'pinned_gpu', 'return_uri', 'source_receipt_id', 'state', 'issued_at', 'expires_at'],
        ['preparation_id', 'run_attempt_id', 'normalized_request_sha256', 'validation_receipt_id', 'validation_receipt_sha256', 'canonical_job_id', 'recovery_job_id', 'binding_receipt', 'pinned_scheduler'],
    );
    const pinnedGpu = record.pinned_gpu === null ? null : requireInteger(record.pinned_gpu, `${label}.pinned_gpu`);
    if (pinnedGpu !== null && pinnedGpu < 0) throw new Error(`${label}.pinned_gpu must be non-negative.`);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.launch-context.v1', 'bms.launch-context.v2']),
        launch_context_id: requireString(record.launch_context_id, `${label}.launch_context_id`),
        project_id: requireString(record.project_id, `${label}.project_id`),
        global_experiment_id: requireString(record.global_experiment_id, `${label}.global_experiment_id`),
        domain_experiment_id: requireString(record.domain_experiment_id, `${label}.domain_experiment_id`),
        workflow_id: requireNullableString(record.workflow_id, `${label}.workflow_id`),
        workflow_revision_id: requireNullableString(record.workflow_revision_id, `${label}.workflow_revision_id`),
        preparation_id: record.preparation_id === undefined ? undefined : requireNullableString(record.preparation_id, `${label}.preparation_id`),
        run_attempt_id: record.run_attempt_id === undefined ? undefined : requireNullableString(record.run_attempt_id, `${label}.run_attempt_id`),
        normalized_request_sha256: record.normalized_request_sha256 === undefined ? undefined : requireNullableString(record.normalized_request_sha256, `${label}.normalized_request_sha256`),
        validation_receipt_id: record.validation_receipt_id === undefined ? undefined : requireNullableString(record.validation_receipt_id, `${label}.validation_receipt_id`),
        validation_receipt_sha256: record.validation_receipt_sha256 === undefined ? undefined : requireNullableString(record.validation_receipt_sha256, `${label}.validation_receipt_sha256`),
        pinned_gpu: pinnedGpu,
        return_uri: requireString(record.return_uri, `${label}.return_uri`),
        source_receipt_id: requireString(record.source_receipt_id, `${label}.source_receipt_id`),
        state: requireLiteral(record.state, `${label}.state`, ['issued', 'reserved', 'claimed', 'consumed']),
        canonical_job_id: record.canonical_job_id === undefined ? undefined : requireNullableString(record.canonical_job_id, `${label}.canonical_job_id`),
        recovery_job_id: record.recovery_job_id === undefined ? undefined : requireNullableString(record.recovery_job_id, `${label}.recovery_job_id`),
        binding_receipt: record.binding_receipt === undefined ? undefined : record.binding_receipt === null ? null : requireJsonObject(record.binding_receipt, `${label}.binding_receipt`),
        pinned_scheduler: record.pinned_scheduler === undefined ? undefined : record.pinned_scheduler === null ? null : requireJsonObject(record.pinned_scheduler, `${label}.pinned_scheduler`),
        issued_at: requireString(record.issued_at, `${label}.issued_at`),
        expires_at: requireString(record.expires_at, `${label}.expires_at`),
    };
}

function parseDomainAdapterRegistry(value: unknown): DomainAdapterRegistry {
    const label = 'domain adapter registry';
    const record = exactRecord(value, label, ['schema', 'adapters']);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.global.adapter-registry.v1']),
        adapters: requireArray(record.adapters, `${label}.adapters`, (item, itemLabel) => {
            const adapter = exactRecord(item, itemLabel, ['adapter_id', 'adapter_version', 'domain_kind', 'entity_kind'], ['display_name']);
            const version = adapter.adapter_version;
            if (typeof version !== 'string' && typeof version !== 'number') throw new Error(`${itemLabel}.adapter_version must be a string or number.`);
            return {
                adapter_id: requireString(adapter.adapter_id, `${itemLabel}.adapter_id`),
                adapter_version: version,
                domain_kind: requireString(adapter.domain_kind, `${itemLabel}.domain_kind`),
                entity_kind: requireString(adapter.entity_kind, `${itemLabel}.entity_kind`),
                display_name: adapter.display_name === undefined ? undefined : requireString(adapter.display_name, `${itemLabel}.display_name`),
            };
        }),
    };
}

function parseAdapterSearchResult(value: unknown): AdapterSearchResult {
    const label = 'adapter search result';
    const record = exactRecord(value, label, ['schema', 'adapter_id', 'adapter_version', 'items', 'next_cursor']);
    const version = record.adapter_version;
    if (typeof version !== 'string' && typeof version !== 'number') throw new Error(`${label}.adapter_version must be a string or number.`);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.global.adapter-search.v1']),
        adapter_id: requireString(record.adapter_id, `${label}.adapter_id`),
        adapter_version: version,
        items: requireArray(record.items, `${label}.items`, (item, itemLabel) => {
            const entity = exactRecord(item, itemLabel, ['adapter_id', 'entity_kind', 'entity_id', 'label', 'canonical_state', 'attachable', 'reason', 'reopen_uri', 'metadata']);
            return {
                adapter_id: requireString(entity.adapter_id, `${itemLabel}.adapter_id`),
                entity_kind: requireString(entity.entity_kind, `${itemLabel}.entity_kind`),
                entity_id: requireString(entity.entity_id, `${itemLabel}.entity_id`),
                label: requireString(entity.label, `${itemLabel}.label`),
                canonical_state: requireString(entity.canonical_state, `${itemLabel}.canonical_state`),
                attachable: requireBoolean(entity.attachable, `${itemLabel}.attachable`),
                reason: requireNullableString(entity.reason, `${itemLabel}.reason`),
                reopen_uri: requireString(entity.reopen_uri, `${itemLabel}.reopen_uri`),
                metadata: requireJsonObject(entity.metadata, `${itemLabel}.metadata`),
            };
        }),
        next_cursor: requireNullableString(record.next_cursor, `${label}.next_cursor`),
    };
}

function parseAttachmentReceipt(value: unknown): AttachmentReceipt {
    const label = 'attachment receipt';
    const record = exactRecord(value, label, [
        'schema', 'attachment_receipt_id', 'project_id', 'global_experiment_id', 'domain_experiment_id', 'adapter_id',
        'adapter_version', 'source_receipt_id', 'source_receipt', 'lineage_edge_id', 'operation', 'role', 'note',
        'project_head_generation', 'normalized_request_sha256', 'attached_at',
    ]);
    const version = record.adapter_version;
    if (typeof version !== 'string' && typeof version !== 'number') throw new Error(`${label}.adapter_version must be a string or number.`);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.global.attachment-receipt.v1']),
        attachment_receipt_id: requireString(record.attachment_receipt_id, `${label}.attachment_receipt_id`),
        project_id: requireString(record.project_id, `${label}.project_id`),
        global_experiment_id: requireString(record.global_experiment_id, `${label}.global_experiment_id`),
        domain_experiment_id: requireString(record.domain_experiment_id, `${label}.domain_experiment_id`),
        adapter_id: requireString(record.adapter_id, `${label}.adapter_id`),
        adapter_version: version,
        source_receipt_id: requireString(record.source_receipt_id, `${label}.source_receipt_id`),
        source_receipt: requireJsonObject(record.source_receipt, `${label}.source_receipt`),
        lineage_edge_id: requireString(record.lineage_edge_id, `${label}.lineage_edge_id`),
        operation: requireLiteral(record.operation, `${label}.operation`, ['attach_reference', 'bind_input', 'link_output', 'attach_evidence']),
        role: requireLiteral(record.role, `${label}.role`, ['references', 'uses_input', 'produced', 'validated_by']),
        note: requireNullableString(record.note, `${label}.note`),
        project_head_generation: requireInteger(record.project_head_generation, `${label}.project_head_generation`),
        normalized_request_sha256: requireString(record.normalized_request_sha256, `${label}.normalized_request_sha256`),
        attached_at: requireString(record.attached_at, `${label}.attached_at`),
    };
}

function parseFrustraMpnnExperimentScopeItem(value: unknown, label: string): FrustraMpnnExperimentScopeItem {
    const record = exactRecord(value, label, [
        'result_receipt_id', 'parent_job_id', 'invocation_id', 'candidate_id', 'operator_label',
        'source_identity', 'state', 'diagnostic', 'statistics_analysis',
        'manifest_sha256', 'content_digest', 'reopen_uri',
    ]);
    const sourceIdentity = exactRecord(record.source_identity, `${label}.source_identity`, [
        'design_id', 'artifact_id', 'artifact_sha256', 'candidate_id',
    ]);
    const statisticsAnalysis = exactRecord(record.statistics_analysis, `${label}.statistics_analysis`, ['state', 'diagnostic']);
    const state = requireLiteral(record.state, `${label}.state`, ['completed', 'failed', 'missing', 'skipped']);
    const parentJobId = requireNullableString(record.parent_job_id, `${label}.parent_job_id`);
    const invocationId = requireNullableString(record.invocation_id, `${label}.invocation_id`);
    const reopenUri = requireNullableString(record.reopen_uri, `${label}.reopen_uri`);
    const manifestSha256 = record.manifest_sha256 === null
        ? null
        : requireSha256(record.manifest_sha256, `${label}.manifest_sha256`);
    const hasExecution = parentJobId !== null || invocationId !== null || reopenUri !== null;
    if (hasExecution && (parentJobId === null || invocationId === null || reopenUri === null)) {
        throw new Error(`${label} execution identity and reopen URI must be supplied together.`);
    }
    if ((state === 'completed') !== (manifestSha256 !== null)) {
        throw new Error(`${label}.manifest_sha256 must exist only for a completed core result.`);
    }
    return {
        result_receipt_id: requireString(record.result_receipt_id, `${label}.result_receipt_id`),
        parent_job_id: parentJobId,
        invocation_id: invocationId,
        candidate_id: requireString(record.candidate_id, `${label}.candidate_id`),
        operator_label: requireString(record.operator_label, `${label}.operator_label`),
        source_identity: {
            design_id: requireNullableString(sourceIdentity.design_id, `${label}.source_identity.design_id`),
            artifact_id: requireString(sourceIdentity.artifact_id, `${label}.source_identity.artifact_id`),
            artifact_sha256: requireSha256(sourceIdentity.artifact_sha256, `${label}.source_identity.artifact_sha256`),
            candidate_id: requireString(sourceIdentity.candidate_id, `${label}.source_identity.candidate_id`),
        },
        state,
        diagnostic: requireNullableString(record.diagnostic, `${label}.diagnostic`),
        statistics_analysis: {
            state: requireLiteral(statisticsAnalysis.state, `${label}.statistics_analysis.state`, ['not_started', 'queued', 'running', 'completed', 'failed']),
            diagnostic: requireNullableString(statisticsAnalysis.diagnostic, `${label}.statistics_analysis.diagnostic`),
        },
        manifest_sha256: manifestSha256,
        content_digest: requireSha256(record.content_digest, `${label}.content_digest`),
        reopen_uri: reopenUri,
    };
}

export function parseFrustraMpnnExperimentScope(value: unknown): FrustraMpnnExperimentScope {
    const label = 'FrustraMPNN experiment scope';
    const record = exactRecord(value, label, [
        'schema', 'project_id', 'global_experiment_id', 'domain_experiment_id',
        'global_experiment_revision_id', 'domain_revision_id',
        'items', 'count', 'bounded',
    ]);
    const items = requireArray(record.items, `${label}.items`, parseFrustraMpnnExperimentScopeItem);
    const count = requireInteger(record.count, `${label}.count`);
    if (count !== items.length || count > 256) {
        throw new Error(`${label}.count must equal the bounded item cardinality.`);
    }
    if (requireBoolean(record.bounded, `${label}.bounded`) !== true) {
        throw new Error(`${label}.bounded must be true.`);
    }
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.project-frustrampnn-result-scope.v1']),
        project_id: requireString(record.project_id, `${label}.project_id`),
        global_experiment_id: requireString(record.global_experiment_id, `${label}.global_experiment_id`),
        domain_experiment_id: requireString(record.domain_experiment_id, `${label}.domain_experiment_id`),
        global_experiment_revision_id: requireString(record.global_experiment_revision_id, `${label}.global_experiment_revision_id`),
        domain_revision_id: requireString(record.domain_revision_id, `${label}.domain_revision_id`),
        items,
        count,
        bounded: true,
    };
}

export async function listProjects(signal?: AbortSignal): Promise<ProjectListPage> {
    const response = await api.get<ProjectListPage>('/api/projects', { params: { limit: 100 }, signal });
    return response.data;
}

export async function searchProjects(options: ProjectSearchOptions = {}): Promise<ProjectListPage> {
    const response = await api.get<ProjectListPage>('/api/projects/search', {
        params: {
            q: options.query?.trim() || undefined,
            status: options.status && options.status !== 'all' ? options.status : undefined,
            archive: options.archive ?? 'active',
            cursor: options.cursor,
            limit: options.limit ?? 50,
            project_scope: options.projectScope ?? 'all',
        },
        signal: options.signal,
    });
    return response.data;
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}`, { signal })).data;
}

export async function getProjectHub(
    projectId: string,
    options: { experimentId?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<ProjectHubResponse> {
    const response = await api.get<ProjectHubResponse>(`/api/projects/${segment(projectId)}/hub`, {
        params: {
            experiment_id: options.experimentId || undefined,
            limit: options.limit ?? 200,
        },
        signal: options.signal,
    });
    return response.data;
}

export async function getProjectSequences(
    projectId: string,
    options: { domainExperimentId?: string; stateRevisionId?: string; signal?: AbortSignal } = {},
): Promise<ProjectSequencesResponse> {
    const response = await api.get<ProjectSequencesResponse>(`/api/projects/${segment(projectId)}/sequences`, {
        params: {
            domain_experiment_id: options.domainExperimentId || undefined,
            state_revision_id: options.stateRevisionId || undefined,
        },
        signal: options.signal,
    });
    return response.data;
}

export async function listGlobalExperiments(projectId: string, signal?: AbortSignal): Promise<HierarchyMutationResult[]> {
    const response = await api.get<{ items: HierarchyMutationResult[] }>(
        `/api/projects/${segment(projectId)}/experiments`,
        { signal },
    );
    return response.data.items;
}

export async function listDomainExperiments(projectId: string, experimentId: string, signal?: AbortSignal): Promise<HierarchyMutationResult[]> {
    const response = await api.get<{ items: HierarchyMutationResult[] }>(
        `/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains`,
        { signal },
    );
    return response.data.items;
}

export async function getGlobalExperiment(projectId: string, experimentId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}`, { signal })).data;
}

export async function getDomainExperiment(projectId: string, experimentId: string, domainId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}`, { signal })).data;
}

export async function fetchDomainFrustraMpnnResults(
    projectId: string,
    experimentId: string,
    domainId: string,
    globalExperimentRevisionId: string,
    domainRevisionId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnExperimentScope> {
    const response = await api.get<unknown>(
        `/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}/frustrampnn-results`,
        {
            params: {
                global_experiment_revision_id: globalExperimentRevisionId,
                domain_revision_id: domainRevisionId,
            },
            signal,
        },
    );
    const parsed = parseFrustraMpnnExperimentScope(response.data);
    if (
        parsed.project_id !== projectId
        || parsed.global_experiment_id !== experimentId
        || parsed.domain_experiment_id !== domainId
        || parsed.global_experiment_revision_id !== globalExperimentRevisionId
        || parsed.domain_revision_id !== domainRevisionId
    ) {
        throw new Error('FrustraMPNN experiment scope does not match the requested Project hierarchy.');
    }
    return parsed;
}

export async function listDomainAdapters(signal?: AbortSignal): Promise<DomainAdapterRegistry> {
    const response = await api.get<unknown>('/api/domain-adapters', { signal });
    return parseDomainAdapterRegistry(response.data);
}

function parseProteinProjectCapability(value: unknown, label: string): ProteinProjectSetupCapability {
    const record = exactRecord(value, label, [
        'capability_id', 'label', 'state', 'adapter_id', 'native_owner_id', 'setup_destination',
        'source_requirements', 'follow_up_compatible_capability_ids',
    ]);
    const destination = requireString(record.setup_destination, `${label}.setup_destination`);
    const nativeOwnerId = requireString(record.native_owner_id, `${label}.native_owner_id`);
    if (!destination.startsWith('/') || destination.startsWith('//')) {
        throw new Error(`${label} is ready without a safe native setup destination and adapter.`);
    }
    const destinationUrl = new URL(destination, 'https://project-setup.invalid');
    const alignmentModels = ['family_msa_protein', 'family_msa_nucleic'];
    const alignmentOwner = alignmentModels.find((model) => nativeOwnerId === `${model}:family_msa`);
    const alignmentDestination = alignmentOwner !== undefined
        && destination === `/alignments?model=${alignmentOwner}&mode=family_msa`;
    const submitDestination = destinationUrl.pathname === '/submit' && (
        (destinationUrl.searchParams.getAll('template').length === 1
            && destinationUrl.searchParams.get('template') === nativeOwnerId)
        || (destinationUrl.searchParams.size === 2
            && destinationUrl.searchParams.getAll('model').length === 1
            && destinationUrl.searchParams.getAll('mode').length === 1
            && `${destinationUrl.searchParams.get('model')}:${destinationUrl.searchParams.get('mode')}` === nativeOwnerId)
    );
    if (destinationUrl.hash || (!alignmentDestination && !submitDestination)) {
        throw new Error(`${label} native owner does not match its setup destination.`);
    }
    return {
        capability_id: requireString(record.capability_id, `${label}.capability_id`),
        label: requireString(record.label, `${label}.label`),
        state: requireLiteral(record.state, `${label}.state`, ['ready'] as const),
        adapter_id: requireString(record.adapter_id, `${label}.adapter_id`),
        native_owner_id: nativeOwnerId,
        setup_destination: destination,
        source_requirements: requireStringArray(record.source_requirements, `${label}.source_requirements`),
        follow_up_compatible_capability_ids: requireStringArray(record.follow_up_compatible_capability_ids, `${label}.follow_up_compatible_capability_ids`),
    };
}

export function parseProteinProjectCapabilityInventory(value: unknown): ProteinProjectCapabilityInventory {
    const record = exactRecord(value, 'protein capability inventory', ['schema', 'capabilities']);
    if (record.schema !== 'bms.protein-project-workflow-picker.v1') throw new Error('Protein capability inventory schema is unsupported.');
    return {
        schema: record.schema,
        capabilities: requireArray(record.capabilities, 'protein capability inventory.capabilities', parseProteinProjectCapability),
    };
}

function parseSetupNavigation(value: unknown, detailed: boolean): ProjectWorkflowSetupNavigation | ProjectWorkflowSetup {
    const details = ['project_label', 'experiment_label', 'workflow_label', 'draft', 'field_errors', 'diagnostics'];
    const record = exactRecord(value, 'project workflow setup', [
        'schema', 'setup_context_id', 'project_id', 'global_experiment_id', 'domain_experiment_id', 'relationship_kind',
        'capability_id', 'state', 'validation_state', 'setup_destination', 'return_uri', 'generation',
        ...(detailed ? details : []),
    ], detailed ? ['preparation_id', 'launch_context_id'] : []);
    const expectedSchema = detailed ? 'bms.project-workflow-setup.detail.v1' : 'bms.project-workflow-setup.create-response.v1';
    if (record.schema !== expectedSchema) throw new Error('Project workflow setup schema is unsupported.');
    const destination = requireString(record.setup_destination, 'project workflow setup.setup_destination');
    const returnUri = requireString(record.return_uri, 'project workflow setup.return_uri');
    if (!destination.startsWith('/') || destination.startsWith('//')) throw new Error('Project workflow setup destination must be a safe local route.');
    if (!returnUri.startsWith('/') || returnUri.startsWith('//')) throw new Error('Project workflow setup return_uri must be a safe local route.');
    const base: ProjectWorkflowSetupNavigation = {
        schema: 'bms.project-workflow-setup.create-response.v1',
        setup_context_id: requireString(record.setup_context_id, 'project workflow setup.setup_context_id'),
        project_id: requireString(record.project_id, 'project workflow setup.project_id'),
        global_experiment_id: requireString(record.global_experiment_id, 'project workflow setup.global_experiment_id'),
        domain_experiment_id: requireString(record.domain_experiment_id, 'project workflow setup.domain_experiment_id'),
        relationship_kind: requireLiteral(record.relationship_kind, 'project workflow setup.relationship_kind', ['primary', 'follow_up'] as const),
        capability_id: requireString(record.capability_id, 'project workflow setup.capability_id'),
        state: requireLiteral(record.state, 'project workflow setup.state', ['open', 'submitted', 'deleted'] as const),
        validation_state: requireLiteral(record.validation_state, 'project workflow setup.validation_state', ['incomplete', 'ready', 'invalid'] as const),
        setup_destination: destination,
        return_uri: returnUri,
        generation: requireInteger(record.generation, 'project workflow setup.generation'),
    };
    if (!detailed) return base;
    const rawErrors = exactRecord(record.field_errors, 'project workflow setup.field_errors', [], Object.keys(record.field_errors as UnknownRecord));
    const fieldErrors: Record<string, string> = {};
    for (const [key, item] of Object.entries(rawErrors)) fieldErrors[key] = requireString(item, `project workflow setup.field_errors.${key}`);
    return {
        ...base,
        schema: 'bms.project-workflow-setup.detail.v1',
        project_label: requireString(record.project_label, 'project workflow setup.project_label'),
        experiment_label: requireString(record.experiment_label, 'project workflow setup.experiment_label'),
        workflow_label: requireString(record.workflow_label, 'project workflow setup.workflow_label'),
        draft: requireJsonObject(record.draft, 'project workflow setup.draft'),
        field_errors: fieldErrors,
        diagnostics: requireJsonObject(record.diagnostics, 'project workflow setup.diagnostics'),
        ...(record.preparation_id === undefined ? {} : { preparation_id: requireString(record.preparation_id, 'project workflow setup.preparation_id') }),
        ...(record.launch_context_id === undefined ? {} : { launch_context_id: requireString(record.launch_context_id, 'project workflow setup.launch_context_id') }),
    };
}

export function parseProjectWorkflowSetup(value: unknown): ProjectWorkflowSetup {
    return parseSetupNavigation(value, true) as ProjectWorkflowSetup;
}

export function parseProjectWorkflowSetupNavigation(value: unknown): ProjectWorkflowSetupNavigation {
    return parseSetupNavigation(value, false) as ProjectWorkflowSetupNavigation;
}

export async function listProteinProjectCapabilities(signal?: AbortSignal): Promise<ProteinProjectCapabilityInventory> {
    const response = await api.get<unknown>('/api/protein-project-capabilities', { signal });
    return parseProteinProjectCapabilityInventory(response.data);
}

export interface FamilyAlignmentSetupCapability {
    capability_id: string;
    label: string;
    domain_kind: 'protein_in_silico' | 'ngs_molbio';
}

export function parseFamilyAlignmentCapabilities(value: unknown): FamilyAlignmentSetupCapability[] {
    const record = exactRecord(value, 'family alignment inventory', ['schema', 'capabilities']);
    if (record.schema !== 'bms.family-msa-project-workflow-picker.v1') throw new Error('Family alignment inventory schema is unsupported.');
    return requireArray(record.capabilities, 'family alignment inventory.capabilities', (value, label) => {
        const row = exactRecord(value, label, ['capability_id', 'label', 'domain_kind', 'project_setup_state', 'project_setup_adapter_id', 'project_native_owner_id', 'safe_setup_destination', 'launch_mode', 'product_taxonomy', 'scientific_capabilities', 'result_adapter_ids']);
        const id = requireLiteral(row.capability_id, `${label}.capability_id`, ['family_msa_protein', 'family_msa_nucleic'] as const);
        const domain = requireLiteral(row.domain_kind, `${label}.domain_kind`, ['protein_in_silico', 'ngs_molbio'] as const);
        if (domain !== (id === 'family_msa_protein' ? 'protein_in_silico' : 'ngs_molbio') || row.project_setup_state !== 'ready' || row.project_native_owner_id !== `${id}:family_msa` || row.safe_setup_destination !== `/alignments?model=${id}&mode=family_msa` || row.launch_mode !== 'typed_launcher_handoff') throw new Error(`${label} has an invalid alignment setup contract.`);
        requireString(row.project_setup_adapter_id, `${label}.project_setup_adapter_id`);
        const taxonomy = exactRecord(row.product_taxonomy, `${label}.product_taxonomy`, ['family']);
        if (taxonomy.family !== 'family_alignment') throw new Error(`${label} has an invalid taxonomy.`);
        return { capability_id: id, label: requireString(row.label, `${label}.label`), domain_kind: domain };
    });
}

export async function listFamilyAlignmentCapabilities(signal?: AbortSignal): Promise<FamilyAlignmentSetupCapability[]> {
    const response = await api.get<unknown>('/api/family-msa-project-capabilities', { signal });
    return parseFamilyAlignmentCapabilities(response.data);
}

export async function createProjectWorkflowSetup(projectId: string, request: ProjectWorkflowSetupCreate): Promise<ProjectWorkflowSetupNavigation> {
    const response = await api.post<unknown>(`/api/projects/${segment(projectId)}/workflow-setups`, request, { headers: { 'Idempotency-Key': crypto.randomUUID() } });
    return parseProjectWorkflowSetupNavigation(response.data);
}

export async function getProjectWorkflowSetup(projectId: string, setupContextId: string, signal?: AbortSignal): Promise<ProjectWorkflowSetup> {
    const response = await api.get<unknown>(`/api/projects/${segment(projectId)}/workflow-setups/${segment(setupContextId)}`, { signal });
    return parseProjectWorkflowSetup(response.data);
}

export async function saveProjectWorkflowSetupDraft(projectId: string, setupContextId: string, request: { expected_generation: number; draft: JsonObject }): Promise<ProjectWorkflowSetup> {
    const response = await api.put<unknown>(`/api/projects/${segment(projectId)}/workflow-setups/${segment(setupContextId)}/draft`, request, { headers: { 'Idempotency-Key': crypto.randomUUID() } });
    return parseProjectWorkflowSetup(response.data);
}

export async function prepareProjectWorkflowSetup(projectId: string, setupContextId: string, expectedGeneration: number): Promise<ProjectWorkflowSetup> {
    const response = await api.post<unknown>(`/api/projects/${segment(projectId)}/workflow-setups/${segment(setupContextId)}/prepare-launch`, { expected_generation: expectedGeneration }, { headers: { 'Idempotency-Key': crypto.randomUUID() } });
    return parseProjectWorkflowSetup(response.data);
}

export async function deleteProjectWorkflowSetup(projectId: string, setupContextId: string): Promise<void> {
    await api.delete(`/api/projects/${segment(projectId)}/workflow-setups/${segment(setupContextId)}`);
}

export async function searchAdapterEntities(adapterId: string, query: string, limit = 25, signal?: AbortSignal): Promise<AdapterSearchResult> {
    const response = await api.get<unknown>(`/api/domain-adapters/${segment(adapterId)}/entities/search`, {
        params: { q: query, limit },
        signal,
    });
    return parseAdapterSearchResult(response.data);
}

export async function attachExistingEntity(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    request: AttachExistingRequest,
): Promise<AttachmentReceipt> {
    const response = await api.post<unknown>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/attach`,
        request,
    );
    return parseAttachmentReceipt(response.data);
}

export interface ProjectResultContext {
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    global_experiment_revision_id: string;
    domain_revision_id: string;
    return_uri: string;
}

/** Same hierarchy and safe-return contract for both Project result entrypoints. */
// Navigation only: this conveys no launch context, receipt or scientific authority.
export function safeProjectNavigationReturn(raw: string | null): string | null {
    if (!raw || !raw.startsWith('/projects/') || /[\\\r\n]/.test(raw)) return null;
    try {
        const uri = new URL(raw, 'https://bms.local');
        if (uri.origin !== 'https://bms.local' || !/^\/projects\/[^/\\\s]+$/.test(decodeURIComponent(uri.pathname))) return null;
        return `${uri.pathname}${uri.search}${uri.hash}`;
    } catch { return null; }
}

export function verifiedProjectReturnUri(context: Pick<LaunchContext, 'project_id' | 'global_experiment_id' | 'domain_experiment_id' | 'return_uri'>): string | null {
    const raw = context.return_uri;
    if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return null;
    try {
        const uri = new URL(raw, 'https://bms.local');
        if (uri.origin !== 'https://bms.local' || uri.hash) return null;
        const path = decodeURIComponent(uri.pathname);
        const query = uri.searchParams;
        if (Array.from(query.keys()).length !== new Set(query.keys()).size) return null;
        if (path === `/projects/${context.project_id}`) {
            if (Array.from(query.keys()).some(key => !['focus', 'selected', 'view', 'candidate_source', 'candidate_search', 'candidate_family', 'state_revision_id', 'map_cursor'].includes(key))
                || query.get('focus') !== context.global_experiment_id || !query.get('selected')) return null;
        } else if (path === `/projects/${context.project_id}/experiments/${context.global_experiment_id}/domains/${context.domain_experiment_id}`) {
            if (Array.from(query.keys()).some(key => !['workspace', 'section'].includes(key))
                || query.get('workspace') !== 'protein'
                || !['overview', 'targets', 'datasets', 'plans', 'runs', 'results', 'comparisons', 'evidence', 'history', 'technical'].includes(query.get('section') ?? '')) return null;
        } else return null;
        return `${uri.pathname}${uri.search}`;
    } catch { return null; }
}

export async function getProjectResultContext(projectId: string, globalExperimentId: string, domainExperimentId: string, returnUri: string): Promise<ProjectResultContext> {
    const [project, global, domain] = await Promise.all([
        getProject(projectId), getGlobalExperiment(projectId, globalExperimentId), getDomainExperiment(projectId, globalExperimentId, domainExperimentId),
    ]);
    if (project.id !== projectId || global.id !== globalExperimentId || global.parent_id !== projectId
        || domain.id !== domainExperimentId || domain.parent_id !== globalExperimentId
        || !global.current_revision_id || !domain.current_revision_id) throw new Error('Exact Project result hierarchy is unavailable.');
    const context = {
        project_id: projectId, global_experiment_id: globalExperimentId, domain_experiment_id: domainExperimentId,
        global_experiment_revision_id: global.current_revision_id, domain_revision_id: domain.current_revision_id, return_uri: returnUri,
    };
    if (!verifiedProjectReturnUri(context)) throw new Error('The Project result return destination is invalid.');
    return context;
}

export async function getLaunchContext(launchContextId: string, signal?: AbortSignal): Promise<LaunchContext> {
    const response = await api.get<unknown>(
        `/api/launch-contexts/${segment(launchContextId)}`,
        { signal },
    );
    return parseLaunchContext(response.data);
}

export async function createProject(request: ProjectCreateRequest): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>('/api/projects', request)).data;
}

export async function updateProject(projectId: string, request: HierarchyPatch): Promise<HierarchyMutationResult> {
    return (await api.patch<HierarchyMutationResult>(`/api/projects/${segment(projectId)}`, request)).data;
}

export async function archiveProject(projectId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/archive`, { expected_head_generation: expectedHeadGeneration })).data;
}

export async function restoreProject(projectId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/restore`, { expected_head_generation: expectedHeadGeneration })).data;
}

export async function createGlobalExperiment(projectId: string, request: GlobalExperimentCreateRequest): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments`, request)).data;
}

export async function createDomainExperiment(projectId: string, experimentId: string, request: DomainExperimentCreateRequest): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains`, request)).data;
}

export async function createResearchRecord(subject: ResearchRecordSubject, request: ResearchRecordRequest): Promise<JsonObject> {
    let path = `/api/projects/${segment(subject.projectId)}`;
    if (subject.globalExperimentId) path += `/experiments/${segment(subject.globalExperimentId)}`;
    if (subject.domainExperimentId) path += `/domains/${segment(subject.domainExperimentId)}`;
    return (await api.post<JsonObject>(`${path}/records`, request)).data;
}

export async function getNgsMolBioBinding(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
): Promise<NgsMolBioBindingStatus> {
    return (await api.get<NgsMolBioBindingStatus>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/binding`,
        { signal },
    )).data;
}

function domainOperatorPath(projectId: string, globalExperimentId: string, domainExperimentId: string): string {
    return `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}`;
}

export async function listDomainDatasets(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
    cursor?: string | null,
): Promise<{ schema: string; items: DomainDatasetHead[]; next_cursor: string | null; has_more: boolean }> {
    return (await api.get<{ schema: string; items: DomainDatasetHead[]; next_cursor: string | null; has_more: boolean }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets`,
        { params: { limit: 100, cursor: cursor ?? undefined }, signal },
    )).data;
}

export async function listDomainDatasetRevisions(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    signal?: AbortSignal,
    cursor?: string | null,
): Promise<{ schema: string; items: DomainDatasetRevisionSummary[]; next_cursor: string | null; has_more: boolean }> {
    return (await api.get<{ schema: string; items: DomainDatasetRevisionSummary[]; next_cursor: string | null; has_more: boolean }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/revisions`,
        { params: { limit: 100, cursor: cursor ?? undefined }, signal },
    )).data;
}

export interface PreparationLaunchRequest {
    preparation_id: string;
    launch_context_id: string | null;
}

export async function launchDomainRunGroup(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    preparationLaunches: PreparationLaunchRequest[],
): Promise<DomainRunGroup> {
    return (await api.post<DomainRunGroup>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/run-groups`,
        { preparation_launches: preparationLaunches },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export function projectManagerErrorMessage(error: unknown): string {
    if (isAxiosError(error)) {
        const detail = error.response?.data?.detail;
        if (typeof detail === 'string') return detail;
        if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
        if (error.response?.status) return `Project Manager request failed (${error.response.status})`;
    }
    return error instanceof Error ? error.message : 'Project Manager request failed';
}
