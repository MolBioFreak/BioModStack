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

export interface DomainWorkflowPlanHead {
    schema: 'bms.workflow-plan-head.v1';
    plan_id: string;
    name: string;
    capability_id: string;
    current_revision_id: string | null;
    head_generation: number;
    draft_id: string | null;
    draft_generation: number | null;
    domain_revision_id: string | null;
    capability_contract: DomainWorkflowCapabilityContract;
    capability_contract_sha256: string;
    workflow_family: string;
    adapter_id: string;
    lifecycle_state: string;
    draft?: JsonObject | null;
    created_at: string;
    updated_at: string;
}

export interface DomainWorkflowPlanRevision {
    schema: 'bms.workflow-plan-revision.v1';
    revision_id: string;
    plan_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    payload: JsonObject;
    payload_sha256: string;
    dependency_graph_sha256: string;
    change_summary?: string;
    created_at: string;
}

export interface DomainWorkflowPreparation {
    schema: 'bms.workflow-preparation.v1';
    preparation_id: string;
    workflow_revision_id: string;
    normalized_request: JsonObject;
    normalized_request_sha256: string;
    requested_settings: JsonObject;
    effective_settings: JsonObject;
    scheduler: JsonObject;
    validation_receipt_id: string;
    validation: JsonObject;
    status: string;
    expected_cardinality: number;
    created_at: string;
    prepared_at: string;
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

export interface RunControlCommandDocument {
    schema: 'bms.run-control-command.v1';
    command_id: string;
    command_type: 'cancel';
    workspace_id: string;
    run_group_id: string;
    expected_generation: number;
    status: string;
    attempt_count: number;
    created_at: string;
    updated_at: string;
    applied_at: string | null;
    acknowledgement?: JsonObject;
    conflict?: JsonObject;
}

export interface DomainResultSurface {
    schema: string;
    receipt_id: string;
    route: string | null;
    readiness: string;
    surface_kind: string;
    native_summary: JsonObject;
    available_actions: string[];
}

export type DomainCapabilityLaunchMode = 'managed_materialization' | 'typed_launcher_handoff';

export interface DomainCapabilityModelMode {
    model_id: string;
    mode: string;
}

export interface DomainWorkflowCapabilityContractCapability {
    [key: string]: JsonValue;
    capability_id: string;
    capability_version: string;
    label: string;
    scientific_role: string;
    launch_mode: DomainCapabilityLaunchMode;
    workflow_family: string;
    workflow_adapter_id: string;
    canonical_source_destination: string;
    parameter_schema_id: string;
    plannable: true;
    exposure_state: 'accepted';
    result_contracts: string[];
}

export interface DomainWorkflowCapabilityContract {
    schema: 'bms.workflow-plan-capability-contract.v1';
    capability: DomainWorkflowCapabilityContractCapability;
    parameter_schema: JsonObject;
    allowed_model_modes: DomainCapabilityModelMode[];
}

export interface DomainCapabilityDescriptor {
    capability_id: string;
    capability_version: string;
    label: string;
    scientific_role: string;
    launch_mode: DomainCapabilityLaunchMode;
    workflow_family: string | null;
    workflow_adapter_id: string | null;
    parameter_schema_id: string;
    parameter_schema: JsonObject;
    allowed_model_modes: DomainCapabilityModelMode[];
    result_contracts: string[];
    canonical_source_destination: string;
    accepted_source_roles: string[];
    capability_contract: DomainWorkflowCapabilityContract;
    capability_contract_sha256: string;
}

export interface DomainCapabilityList {
    schema: 'bms.ngs-molbio.domain-capability-list.v1';
    domain_id: string;
    domain_revision_id: string | null;
    experiment_mode: string | null;
    inventory_sha256: string;
    items: DomainCapabilityDescriptor[];
}

export interface DomainDatasetKindMemberContract {
    adapter_id: string;
    receipt_kind: string;
    allowed_roles: string[];
    compatibility_rule: string;
}

export interface DomainDatasetKindDescriptor {
    dataset_kind: string;
    label: string;
    minimum_members: number;
    maximum_members: number;
    allowed_members: DomainDatasetKindMemberContract[];
    compatibility_rules: string[];
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

export interface DomainDatasetMemberMetadata {
    display_label?: string | null;
    group_label?: string | null;
    condition_label?: string | null;
    tags: string[];
}

export interface DomainDatasetMember {
    schema?: string;
    receipt_id: string;
    adapter_id?: string;
    store_id?: string;
    entity_kind?: string;
    entity_id?: string;
    native_revision_or_generation?: string;
    native_content_sha256?: string;
    role: string;
    ordinal: number;
    media_type: string | null;
    metadata: DomainDatasetMemberMetadata;
    reopen_uri?: string;
    canonical_member_sha256?: string;
    size_bytes?: number | null;
}

export interface DomainDatasetRevisionSummary {
    revision_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    revision_sha256: string;
    created_at: string;
}

export interface DomainDatasetRevision extends DomainDatasetRevisionSummary {
    schema: 'bms.dataset-revision.v1';
    dataset_id: string;
    member_count: number;
    members?: DomainDatasetMember[];
    members_uri?: string;
    head_generation?: number;
    normalized_request_sha256?: string;
}

export interface DomainDatasetMemberDraft {
    receipt_id: string;
    role: string;
    media_type: string | null;
    metadata: DomainDatasetMemberMetadata;
}

export type HierarchyNodeType = 'project' | 'global_experiment' | 'domain_experiment' | 'virtual_folder';
export type MapNodeType = HierarchyNodeType | 'workflow' | 'run' | 'workflow_run' | 'result' | 'dataset' | 'external_entity_receipt' | 'research_record';
export type ResultSurfaceKind = 'protein_design' | 'molecular_dynamics' | 'conformational_mapping' | 'frustrampnn' | 'ngs' | 'molbio' | 'artifact' | 'unsupported';
export type ResultReadiness = 'running' | 'partial' | 'ready' | 'failed' | 'blocked' | 'unsupported';
export type ScientificAcceptanceState = 'passed' | 'failed' | 'review' | 'unavailable' | 'not_applicable';
export type ReconciliationState = 'current' | 'pending' | 'stale' | 'source_unavailable' | 'digest_mismatch';
export type ProteinExperimentMode = 'exploration' | 'design' | 'redesign' | 'prediction' | 'validation' | 'comparison' | 'simulation' | 'analysis';
export type LineageRole = 'references' | 'uses_input' | 'produced' | 'validated_by';
export type RecordKind = 'note' | 'observation' | 'decision' | 'conclusion';

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
    name: string;
    description: string;
    payload: JsonObject | null;
    active_experiment_count?: number;
    unresolved_failure_count?: number;
    created_at: string;
    updated_at: string;
}

export interface ProjectListPage {
    items: ProjectListItem[];
    next_cursor: string | null;
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

export interface ProjectHeadSummary {
    id: string;
    name: string;
    objective: string;
    lifecycle_state: string;
    head_generation: number;
    current_revision_id: string | null;
    updated_at: string;
}

export interface ProjectTreeNode {
    node_key: string;
    node_type: HierarchyNodeType;
    subject_id: string | null;
    parent_node_key: string | null;
    label: string;
    lifecycle_state: string | null;
    counts: Record<string, number>;
    has_children: boolean;
    allowed_actions: string[];
}

export interface Reconciliation {
    state: ReconciliationState;
    last_verified_at: string | null;
    reason: string | null;
}

export interface CanonicalIdentity {
    store_id?: string;
    entity_kind?: string;
    entity_id?: string | null;
    receipt_id?: string;
    content_digest?: string;
    contract_digest?: string;
    revision_id?: string | null;
    payload_sha256?: string | null;
    [key: string]: JsonValue | undefined;
}

export interface ProjectMapNode {
    node_key: string;
    node_type: MapNodeType;
    label: string;
    normalized_state: string;
    canonical_identity: CanonicalIdentity;
    counts: Record<string, number>;
    reconciliation: Reconciliation;
    allowed_actions: string[];
    parent_node_key?: string | null;
}

export interface ProjectMapEdge {
    source_node_key: string;
    target_node_key: string;
    lineage_mode: string;
    edge_key: string;
    accessible_label: string;
}

export interface ResultSurface {
    schema: 'bms.result-surface.v1';
    receipt_id: string;
    entity_kind: string;
    entity_id: string;
    contract_id: string;
    content_digest: string;
    surface_kind: ResultSurfaceKind;
    route: string | null;
    readiness: ResultReadiness;
    native_summary: JsonObject;
    scientific_acceptance: {
        state: ScientificAcceptanceState;
        reason: string | null;
    };
    provenance: JsonObject;
    available_actions: string[];
}

export interface ProjectSelection {
    node_key: string;
    node_type: MapNodeType;
    title: string;
    subtitle: string | null;
    canonical_identity: CanonicalIdentity;
    summary: JsonObject;
    relationship: JsonObject;
    scientific_context: JsonObject;
    reconciliation: Reconciliation;
    available_actions: string[];
    canonical_surface: ResultSurface | null;
}

export type RunProgressKind = 'fraction' | 'elapsed' | 'indeterminate';
export type RunConditionSeverity = 'none' | 'warning' | 'failure';

export interface ProjectRunAttempt {
    attempt_id: string;
    attempt_number: number;
    canonical_job_id: string;
    canonical_state: string;
    binding_receipt: JsonObject | null;
    runtime_identity: JsonObject | null;
    terminal_receipt: JsonObject | null;
}

export interface ProjectRun {
    run_id: string;
    workflow_id: string;
    canonical_job_id: string | null;
    workflow_type: string;
    target_label: string;
    canonical_state: string;
    normalized_state: string;
    stage: string | null;
    progress: { kind: RunProgressKind; value: number | null };
    started_at: string | null;
    elapsed_seconds: number;
    replica_index: number | null;
    batch_or_run_group_id: string | null;
    output_count: number;
    condition: {
        severity: RunConditionSeverity;
        code: string | null;
        message: string | null;
    };
    receipt_id: string | null;
    output_receipt_ids: string[];
    adapter_id: string | null;
    available_actions: string[];
    canonical_surface: ResultSurface | null;
    canonical_surfaces: ResultSurface[];
    attempts: ProjectRunAttempt[];
}

export interface ProjectActivity {
    id: string;
    resource_id: string;
    event_type: string;
    generation: number | null;
    payload: JsonObject;
    created_at: string;
}

export interface BoundedPage<T> {
    items: T[];
    next_cursor: string | null;
}

export interface ProjectManagerReadModel {
    schema: 'bms.project-manager.read-model.v1';
    subject_id: string;
    subject_generation: number;
    assembled_at: string;
    source_receipt_ids: string[];
    source_digest_set_sha256: string;
    adapter_versions: Array<{ adapter_id: string; version: string | number }>;
    reconciliation: Reconciliation;
    counts: Record<string, number>;
    status_summary: Record<string, JsonValue>;
    recent_activity: ProjectActivity[];
    result_previews: ResultSurface[];
    pagination: {
        map_next_cursor: string | null;
        run_next_cursor: string | null;
        result_next_cursor: string | null;
        lineage_next_cursor: string | null;
        note_next_cursor: string | null;
        decision_next_cursor: string | null;
        dataset_next_cursor: string | null;
        activity_next_cursor: string | null;
        map: BoundedPage<JsonObject> & { repeated_context_node_keys: string[] };
        runs: BoundedPage<ProjectRun>;
        results: BoundedPage<JsonObject>;
        lineage: BoundedPage<JsonObject>;
        notes: BoundedPage<JsonObject>;
        decisions: BoundedPage<JsonObject>;
        datasets: BoundedPage<JsonObject>;
        activity: BoundedPage<ProjectActivity>;
    };
    project: ProjectHeadSummary;
    tree: { nodes: ProjectTreeNode[] };
    map: {
        focus_node_key: string;
        nodes: ProjectMapNode[];
        edges: ProjectMapEdge[];
        truncated: boolean;
        next_cursor: string | null;
    };
    selection: ProjectSelection;
    runs: { items: ProjectRun[]; next_cursor: string | null };
    warnings: string[];
    allowed_actions: string[];
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
    issued_at: string;
    expires_at: string;
}

export interface CreateLaunchContextRequest {
    workflow_id?: string | null;
    workflow_revision_id?: string | null;
    return_uri: string;
}

export interface ProjectSummaryOptions {
    focusId?: string;
    selectedNodeKey?: string;
    mapCursor?: string;
    runCursor?: string;
    resultCursor?: string;
    lineageCursor?: string;
    noteCursor?: string;
    decisionCursor?: string;
    datasetCursor?: string;
    activityCursor?: string;
    mapLimit?: number;
    runLimit?: number;
    resultLimit?: number;
    lineageLimit?: number;
    noteLimit?: number;
    decisionLimit?: number;
    datasetLimit?: number;
    activityLimit?: number;
    signal?: AbortSignal;
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

export interface NgsMolBioProjectLink {
    schema: 'bms.ngs-molbio-project-link.v1';
    link_id: string;
    local_project_id: string;
    global_project_id: string;
    experiment_ids: string[];
    result_ids: string[];
    change_summary: string;
    created_at: string;
}

export interface NgsMolBioShareableResult {
    result_receipt_id: string;
    experiment_id: string;
    store_id: string;
    entity_kind: string;
    entity_id: string;
    generation_or_revision: string;
    content_digest: string;
    availability: string;
    created_at: string;
}

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

function parseCanonicalIdentity(value: unknown, label: string): CanonicalIdentity {
    const record = exactRecord(value, label, [], [
        'store_id', 'entity_kind', 'entity_id', 'receipt_id', 'content_digest', 'contract_digest',
        'revision_id', 'payload_sha256',
    ]);
    const parsed: CanonicalIdentity = {};
    if (record.store_id !== undefined) parsed.store_id = requireString(record.store_id, `${label}.store_id`);
    if (record.entity_kind !== undefined) parsed.entity_kind = requireString(record.entity_kind, `${label}.entity_kind`);
    if (record.entity_id !== undefined) parsed.entity_id = record.entity_id === null ? null : requireString(record.entity_id, `${label}.entity_id`);
    if (record.receipt_id !== undefined) parsed.receipt_id = requireString(record.receipt_id, `${label}.receipt_id`);
    if (record.content_digest !== undefined) parsed.content_digest = requireSha256(record.content_digest, `${label}.content_digest`);
    if (record.contract_digest !== undefined) parsed.contract_digest = requireSha256(record.contract_digest, `${label}.contract_digest`);
    if (record.revision_id !== undefined) parsed.revision_id = record.revision_id === null ? null : requireString(record.revision_id, `${label}.revision_id`);
    if (record.payload_sha256 !== undefined) parsed.payload_sha256 = record.payload_sha256 === null ? null : requireSha256(record.payload_sha256, `${label}.payload_sha256`);
    return parsed;
}

function requireStringArray(value: unknown, label: string): string[] {
    return requireArray(value, label, requireString);
}

function requireCounts(value: unknown, label: string): Record<string, number> {
    const record = exactRecord(value, label, Object.keys((value && typeof value === 'object' && !Array.isArray(value)) ? value : {}));
    const parsed: Record<string, number> = {};
    for (const [key, item] of Object.entries(record)) parsed[key] = requireInteger(item, `${label}.${key}`);
    return parsed;
}

function parseReconciliation(value: unknown, label: string): Reconciliation {
    const record = exactRecord(value, label, ['state', 'last_verified_at', 'reason']);
    return {
        state: requireLiteral(record.state, `${label}.state`, ['current', 'pending', 'stale', 'source_unavailable', 'digest_mismatch']),
        last_verified_at: requireNullableString(record.last_verified_at, `${label}.last_verified_at`),
        reason: requireNullableString(record.reason, `${label}.reason`),
    };
}

export function parseResultSurface(value: unknown, label = 'result surface'): ResultSurface {
    const record = exactRecord(value, label, [
        'schema', 'receipt_id', 'entity_kind', 'entity_id', 'contract_id', 'content_digest', 'surface_kind',
        'route', 'readiness', 'native_summary', 'scientific_acceptance', 'provenance', 'available_actions',
    ]);
    const acceptance = exactRecord(record.scientific_acceptance, `${label}.scientific_acceptance`, ['state', 'reason']);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.result-surface.v1']),
        receipt_id: requireString(record.receipt_id, `${label}.receipt_id`),
        entity_kind: requireString(record.entity_kind, `${label}.entity_kind`),
        entity_id: requireString(record.entity_id, `${label}.entity_id`),
        contract_id: requireString(record.contract_id, `${label}.contract_id`),
        content_digest: requireSha256(record.content_digest, `${label}.content_digest`),
        surface_kind: requireLiteral(record.surface_kind, `${label}.surface_kind`, ['protein_design', 'molecular_dynamics', 'conformational_mapping', 'frustrampnn', 'ngs', 'molbio', 'artifact', 'unsupported']),
        route: requireNullableString(record.route, `${label}.route`),
        readiness: requireLiteral(record.readiness, `${label}.readiness`, ['running', 'partial', 'ready', 'failed', 'blocked', 'unsupported']),
        native_summary: requireJsonObject(record.native_summary, `${label}.native_summary`),
        scientific_acceptance: {
            state: requireLiteral(acceptance.state, `${label}.scientific_acceptance.state`, ['passed', 'failed', 'review', 'unavailable', 'not_applicable']),
            reason: requireNullableString(acceptance.reason, `${label}.scientific_acceptance.reason`),
        },
        provenance: requireJsonObject(record.provenance, `${label}.provenance`),
        available_actions: requireArray(record.available_actions, `${label}.available_actions`, (item, itemLabel) => requireLiteral(item, itemLabel, ['open', 'download', 'compare', 'attach_evidence'])),
    };
}

function parseActivity(value: unknown, label: string): ProjectActivity {
    const record = exactRecord(value, label, ['id', 'resource_id', 'event_type', 'generation', 'payload', 'created_at']);
    return {
        id: requireString(record.id, `${label}.id`),
        resource_id: requireString(record.resource_id, `${label}.resource_id`),
        event_type: requireString(record.event_type, `${label}.event_type`),
        generation: record.generation === null ? null : requireInteger(record.generation, `${label}.generation`),
        payload: requireJsonObject(record.payload, `${label}.payload`),
        created_at: requireString(record.created_at, `${label}.created_at`),
    };
}

function parseRunAttempt(value: unknown, label: string): ProjectRunAttempt {
    const record = exactRecord(value, label, ['attempt_id', 'attempt_number', 'canonical_job_id', 'canonical_state', 'binding_receipt', 'runtime_identity', 'terminal_receipt']);
    return {
        attempt_id: requireString(record.attempt_id, `${label}.attempt_id`),
        attempt_number: requireInteger(record.attempt_number, `${label}.attempt_number`),
        canonical_job_id: requireString(record.canonical_job_id, `${label}.canonical_job_id`),
        canonical_state: requireString(record.canonical_state, `${label}.canonical_state`),
        binding_receipt: record.binding_receipt === null ? null : requireJsonObject(record.binding_receipt, `${label}.binding_receipt`),
        runtime_identity: record.runtime_identity === null ? null : requireJsonObject(record.runtime_identity, `${label}.runtime_identity`),
        terminal_receipt: record.terminal_receipt === null ? null : requireJsonObject(record.terminal_receipt, `${label}.terminal_receipt`),
    };
}

function parseProjectRun(value: unknown, label: string): ProjectRun {
    const record = exactRecord(value, label, [
        'run_id', 'workflow_id', 'canonical_job_id', 'workflow_type', 'target_label', 'canonical_state', 'normalized_state',
        'stage', 'progress', 'started_at', 'elapsed_seconds', 'replica_index', 'batch_or_run_group_id', 'output_count',
        'condition', 'receipt_id', 'output_receipt_ids', 'adapter_id', 'available_actions', 'canonical_surface', 'canonical_surfaces', 'attempts',
    ]);
    const progress = exactRecord(record.progress, `${label}.progress`, ['kind', 'value']);
    const condition = exactRecord(record.condition, `${label}.condition`, ['severity', 'code', 'message']);
    return {
        run_id: requireString(record.run_id, `${label}.run_id`),
        workflow_id: requireString(record.workflow_id, `${label}.workflow_id`),
        canonical_job_id: requireNullableString(record.canonical_job_id, `${label}.canonical_job_id`),
        workflow_type: requireString(record.workflow_type, `${label}.workflow_type`),
        target_label: requireString(record.target_label, `${label}.target_label`),
        canonical_state: requireString(record.canonical_state, `${label}.canonical_state`),
        normalized_state: requireString(record.normalized_state, `${label}.normalized_state`),
        stage: requireNullableString(record.stage, `${label}.stage`),
        progress: {
            kind: requireLiteral(progress.kind, `${label}.progress.kind`, ['fraction', 'elapsed', 'indeterminate']),
            value: progress.value === null ? null : requireNumber(progress.value, `${label}.progress.value`),
        },
        started_at: requireNullableString(record.started_at, `${label}.started_at`),
        elapsed_seconds: requireNumber(record.elapsed_seconds, `${label}.elapsed_seconds`),
        replica_index: record.replica_index === null ? null : requireInteger(record.replica_index, `${label}.replica_index`),
        batch_or_run_group_id: requireNullableString(record.batch_or_run_group_id, `${label}.batch_or_run_group_id`),
        output_count: requireInteger(record.output_count, `${label}.output_count`),
        condition: {
            severity: requireLiteral(condition.severity, `${label}.condition.severity`, ['none', 'warning', 'failure']),
            code: requireNullableString(condition.code, `${label}.condition.code`),
            message: requireNullableString(condition.message, `${label}.condition.message`),
        },
        receipt_id: requireNullableString(record.receipt_id, `${label}.receipt_id`),
        output_receipt_ids: requireStringArray(record.output_receipt_ids, `${label}.output_receipt_ids`),
        adapter_id: requireNullableString(record.adapter_id, `${label}.adapter_id`),
        available_actions: requireStringArray(record.available_actions, `${label}.available_actions`),
        canonical_surface: record.canonical_surface === null ? null : parseResultSurface(record.canonical_surface, `${label}.canonical_surface`),
        canonical_surfaces: requireArray(record.canonical_surfaces, `${label}.canonical_surfaces`, parseResultSurface),
        attempts: requireArray(record.attempts, `${label}.attempts`, parseRunAttempt),
    };
}

function parseTreeNode(value: unknown, label: string): ProjectTreeNode {
    const record = exactRecord(value, label, ['node_key', 'node_type', 'subject_id', 'parent_node_key', 'label', 'lifecycle_state', 'counts', 'has_children', 'allowed_actions']);
    return {
        node_key: requireString(record.node_key, `${label}.node_key`),
        node_type: requireLiteral(record.node_type, `${label}.node_type`, ['project', 'global_experiment', 'domain_experiment', 'virtual_folder']),
        subject_id: requireNullableString(record.subject_id, `${label}.subject_id`),
        parent_node_key: requireNullableString(record.parent_node_key, `${label}.parent_node_key`),
        label: requireString(record.label, `${label}.label`),
        lifecycle_state: requireNullableString(record.lifecycle_state, `${label}.lifecycle_state`),
        counts: requireCounts(record.counts, `${label}.counts`),
        has_children: requireBoolean(record.has_children, `${label}.has_children`),
        allowed_actions: requireStringArray(record.allowed_actions, `${label}.allowed_actions`),
    };
}

function parseMapNode(value: unknown, label: string): ProjectMapNode {
    const record = exactRecord(value, label, ['node_key', 'node_type', 'label', 'normalized_state', 'canonical_identity', 'counts', 'reconciliation', 'allowed_actions'], ['parent_node_key']);
    return {
        node_key: requireString(record.node_key, `${label}.node_key`),
        node_type: requireLiteral(record.node_type, `${label}.node_type`, ['project', 'global_experiment', 'domain_experiment', 'virtual_folder', 'workflow', 'run', 'workflow_run', 'result', 'dataset', 'external_entity_receipt', 'research_record']),
        label: requireString(record.label, `${label}.label`),
        normalized_state: requireString(record.normalized_state, `${label}.normalized_state`),
        canonical_identity: parseCanonicalIdentity(record.canonical_identity, `${label}.canonical_identity`),
        counts: requireCounts(record.counts, `${label}.counts`),
        reconciliation: parseReconciliation(record.reconciliation, `${label}.reconciliation`),
        allowed_actions: requireStringArray(record.allowed_actions, `${label}.allowed_actions`),
        ...(record.parent_node_key === undefined
            ? {}
            : { parent_node_key: requireNullableString(record.parent_node_key, `${label}.parent_node_key`) }),
    };
}

function parseMapEdge(value: unknown, label: string): ProjectMapEdge {
    const record = exactRecord(value, label, ['source_node_key', 'target_node_key', 'lineage_mode', 'edge_key', 'accessible_label']);
    return {
        source_node_key: requireString(record.source_node_key, `${label}.source_node_key`),
        target_node_key: requireString(record.target_node_key, `${label}.target_node_key`),
        lineage_mode: requireString(record.lineage_mode, `${label}.lineage_mode`),
        edge_key: requireString(record.edge_key, `${label}.edge_key`),
        accessible_label: requireString(record.accessible_label, `${label}.accessible_label`),
    };
}

function parseSelection(value: unknown, label: string): ProjectSelection {
    const record = exactRecord(value, label, ['node_key', 'node_type', 'title', 'subtitle', 'canonical_identity', 'summary', 'relationship', 'scientific_context', 'reconciliation', 'available_actions', 'canonical_surface']);
    return {
        node_key: requireString(record.node_key, `${label}.node_key`),
        node_type: requireLiteral(record.node_type, `${label}.node_type`, ['project', 'global_experiment', 'domain_experiment', 'virtual_folder', 'workflow', 'run', 'workflow_run', 'result', 'dataset', 'external_entity_receipt', 'research_record']),
        title: requireString(record.title, `${label}.title`),
        subtitle: requireNullableString(record.subtitle, `${label}.subtitle`),
        canonical_identity: parseCanonicalIdentity(record.canonical_identity, `${label}.canonical_identity`),
        summary: requireJsonObject(record.summary, `${label}.summary`),
        relationship: requireJsonObject(record.relationship, `${label}.relationship`),
        scientific_context: requireJsonObject(record.scientific_context, `${label}.scientific_context`),
        reconciliation: parseReconciliation(record.reconciliation, `${label}.reconciliation`),
        available_actions: requireStringArray(record.available_actions, `${label}.available_actions`),
        canonical_surface: record.canonical_surface === null ? null : parseResultSurface(record.canonical_surface, `${label}.canonical_surface`),
    };
}

function parseJsonPage(value: unknown, label: string, extra: readonly string[] = []): BoundedPage<JsonObject> & { repeated_context_node_keys?: string[] } {
    const record = exactRecord(value, label, ['items', 'next_cursor', ...extra]);
    const parsed: BoundedPage<JsonObject> & { repeated_context_node_keys?: string[] } = {
        items: requireArray(record.items, `${label}.items`, requireJsonObject),
        next_cursor: requireNullableString(record.next_cursor, `${label}.next_cursor`),
    };
    if (extra.includes('repeated_context_node_keys')) parsed.repeated_context_node_keys = requireStringArray(record.repeated_context_node_keys, `${label}.repeated_context_node_keys`);
    return parsed;
}

export function normalizeProjectManagerReadModel(value: unknown): ProjectManagerReadModel {
    const label = 'Project Manager read model';
    const record = exactRecord(value, label, [
        'schema', 'subject_id', 'subject_generation', 'assembled_at', 'source_receipt_ids', 'source_digest_set_sha256',
        'adapter_versions', 'reconciliation', 'counts', 'status_summary', 'recent_activity', 'result_previews', 'pagination',
        'project', 'tree', 'map', 'selection', 'runs', 'warnings', 'allowed_actions',
    ]);
    const project = exactRecord(record.project, `${label}.project`, ['id', 'name', 'objective', 'lifecycle_state', 'head_generation', 'current_revision_id', 'updated_at']);
    const tree = exactRecord(record.tree, `${label}.tree`, ['nodes']);
    const map = exactRecord(record.map, `${label}.map`, ['focus_node_key', 'nodes', 'edges', 'truncated', 'next_cursor']);
    const runs = exactRecord(record.runs, `${label}.runs`, ['items', 'next_cursor']);
    const pagination = exactRecord(record.pagination, `${label}.pagination`, [
        'map_next_cursor', 'run_next_cursor', 'result_next_cursor', 'lineage_next_cursor', 'note_next_cursor', 'decision_next_cursor', 'dataset_next_cursor', 'activity_next_cursor',
        'map', 'runs', 'results', 'lineage', 'notes', 'decisions', 'datasets', 'activity',
    ]);
    const paginationRuns = exactRecord(pagination.runs, `${label}.pagination.runs`, ['items', 'next_cursor']);
    const paginationActivity = exactRecord(pagination.activity, `${label}.pagination.activity`, ['items', 'next_cursor']);
    return {
        schema: requireLiteral(record.schema, `${label}.schema`, ['bms.project-manager.read-model.v1']),
        subject_id: requireString(record.subject_id, `${label}.subject_id`),
        subject_generation: requireInteger(record.subject_generation, `${label}.subject_generation`),
        assembled_at: requireString(record.assembled_at, `${label}.assembled_at`),
        source_receipt_ids: requireStringArray(record.source_receipt_ids, `${label}.source_receipt_ids`),
        source_digest_set_sha256: requireSha256(record.source_digest_set_sha256, `${label}.source_digest_set_sha256`),
        adapter_versions: requireArray(record.adapter_versions, `${label}.adapter_versions`, (item, itemLabel) => {
            const adapter = exactRecord(item, itemLabel, ['adapter_id', 'version']);
            const version = adapter.version;
            if (typeof version !== 'string' && typeof version !== 'number') throw new Error(`${itemLabel}.version must be a string or number.`);
            return { adapter_id: requireString(adapter.adapter_id, `${itemLabel}.adapter_id`), version };
        }),
        reconciliation: parseReconciliation(record.reconciliation, `${label}.reconciliation`),
        counts: requireCounts(record.counts, `${label}.counts`),
        status_summary: requireJsonObject(record.status_summary, `${label}.status_summary`),
        recent_activity: requireArray(record.recent_activity, `${label}.recent_activity`, parseActivity),
        result_previews: requireArray(record.result_previews, `${label}.result_previews`, parseResultSurface),
        pagination: {
            map_next_cursor: requireNullableString(pagination.map_next_cursor, `${label}.pagination.map_next_cursor`),
            run_next_cursor: requireNullableString(pagination.run_next_cursor, `${label}.pagination.run_next_cursor`),
            result_next_cursor: requireNullableString(pagination.result_next_cursor, `${label}.pagination.result_next_cursor`),
            lineage_next_cursor: requireNullableString(pagination.lineage_next_cursor, `${label}.pagination.lineage_next_cursor`),
            note_next_cursor: requireNullableString(pagination.note_next_cursor, `${label}.pagination.note_next_cursor`),
            decision_next_cursor: requireNullableString(pagination.decision_next_cursor, `${label}.pagination.decision_next_cursor`),
            dataset_next_cursor: requireNullableString(pagination.dataset_next_cursor, `${label}.pagination.dataset_next_cursor`),
            activity_next_cursor: requireNullableString(pagination.activity_next_cursor, `${label}.pagination.activity_next_cursor`),
            map: parseJsonPage(pagination.map, `${label}.pagination.map`, ['repeated_context_node_keys']) as BoundedPage<JsonObject> & { repeated_context_node_keys: string[] },
            runs: { items: requireArray(paginationRuns.items, `${label}.pagination.runs.items`, parseProjectRun), next_cursor: requireNullableString(paginationRuns.next_cursor, `${label}.pagination.runs.next_cursor`) },
            results: parseJsonPage(pagination.results, `${label}.pagination.results`),
            lineage: parseJsonPage(pagination.lineage, `${label}.pagination.lineage`),
            notes: parseJsonPage(pagination.notes, `${label}.pagination.notes`),
            decisions: parseJsonPage(pagination.decisions, `${label}.pagination.decisions`),
            datasets: parseJsonPage(pagination.datasets, `${label}.pagination.datasets`),
            activity: { items: requireArray(paginationActivity.items, `${label}.pagination.activity.items`, parseActivity), next_cursor: requireNullableString(paginationActivity.next_cursor, `${label}.pagination.activity.next_cursor`) },
        },
        project: {
            id: requireString(project.id, `${label}.project.id`),
            name: requireString(project.name, `${label}.project.name`),
            objective: requireString(project.objective, `${label}.project.objective`),
            lifecycle_state: requireString(project.lifecycle_state, `${label}.project.lifecycle_state`),
            head_generation: requireInteger(project.head_generation, `${label}.project.head_generation`),
            current_revision_id: requireNullableString(project.current_revision_id, `${label}.project.current_revision_id`),
            updated_at: requireString(project.updated_at, `${label}.project.updated_at`),
        },
        tree: { nodes: requireArray(tree.nodes, `${label}.tree.nodes`, parseTreeNode) },
        map: {
            focus_node_key: requireString(map.focus_node_key, `${label}.map.focus_node_key`),
            nodes: requireArray(map.nodes, `${label}.map.nodes`, parseMapNode),
            edges: requireArray(map.edges, `${label}.map.edges`, parseMapEdge),
            truncated: requireBoolean(map.truncated, `${label}.map.truncated`),
            next_cursor: requireNullableString(map.next_cursor, `${label}.map.next_cursor`),
        },
        selection: parseSelection(record.selection, `${label}.selection`),
        runs: { items: requireArray(runs.items, `${label}.runs.items`, parseProjectRun), next_cursor: requireNullableString(runs.next_cursor, `${label}.runs.next_cursor`) },
        warnings: requireStringArray(record.warnings, `${label}.warnings`),
        allowed_actions: requireStringArray(record.allowed_actions, `${label}.allowed_actions`),
    };
}

export function parseLaunchContext(value: unknown): LaunchContext {
    const label = 'launch context';
    const record = exactRecord(
        value,
        label,
        ['schema', 'launch_context_id', 'project_id', 'global_experiment_id', 'domain_experiment_id', 'workflow_id', 'workflow_revision_id', 'pinned_gpu', 'return_uri', 'source_receipt_id', 'state', 'issued_at', 'expires_at'],
        ['preparation_id', 'run_attempt_id', 'normalized_request_sha256', 'validation_receipt_id', 'validation_receipt_sha256', 'canonical_job_id', 'recovery_job_id', 'binding_receipt'],
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

export async function getProjectSummary(projectId: string, options: ProjectSummaryOptions = {}): Promise<ProjectManagerReadModel> {
    const response = await api.get<unknown>(`/api/projects/${segment(projectId)}/summary`, {
        params: {
            focus_id: options.focusId,
            selected_node_key: options.selectedNodeKey,
            map_cursor: options.mapCursor,
            run_cursor: options.runCursor,
            result_cursor: options.resultCursor,
            lineage_cursor: options.lineageCursor,
            note_cursor: options.noteCursor,
            decision_cursor: options.decisionCursor,
            dataset_cursor: options.datasetCursor,
            activity_cursor: options.activityCursor,
            map_limit: options.mapLimit,
            run_limit: options.runLimit,
            result_limit: options.resultLimit,
            lineage_limit: options.lineageLimit,
            note_limit: options.noteLimit,
            decision_limit: options.decisionLimit,
            dataset_limit: options.datasetLimit,
            activity_limit: options.activityLimit,
        },
        signal: options.signal,
    });
    return normalizeProjectManagerReadModel(response.data);
}

export async function listDomainAdapters(signal?: AbortSignal): Promise<DomainAdapterRegistry> {
    const response = await api.get<unknown>('/api/domain-adapters', { signal });
    return parseDomainAdapterRegistry(response.data);
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

export async function getResultSurface(projectId: string, receiptId: string, signal?: AbortSignal): Promise<ResultSurface> {
    const response = await api.get<unknown>(
        `/api/projects/${segment(projectId)}/receipts/${segment(receiptId)}/surface`,
        { signal },
    );
    return parseResultSurface(response.data);
}

export async function createLaunchContext(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    request: CreateLaunchContextRequest,
): Promise<LaunchContext> {
    const response = await api.post<unknown>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/launch-contexts`,
        request,
    );
    return parseLaunchContext(response.data);
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

export async function listNgsMolBioProjectLinks(projectId: string, signal?: AbortSignal): Promise<NgsMolBioProjectLink[]> {
    const response = await api.get<{ items: NgsMolBioProjectLink[] }>(
        `/api/projects/${segment(projectId)}/ngs-molbio-links`,
        { signal },
    );
    return response.data.items;
}

export async function listNgsMolBioShareableResults(projectId: string, signal?: AbortSignal): Promise<NgsMolBioShareableResult[]> {
    const response = await api.get<{ items: NgsMolBioShareableResult[] }>(
        `/api/projects/${segment(projectId)}/ngs-molbio-shareable-results`,
        { signal },
    );
    return response.data.items;
}

export async function linkNgsMolBioProject(
    globalProjectId: string,
    request: { local_project_id: string; experiment_ids: string[]; result_ids: string[]; change_summary: string },
): Promise<NgsMolBioProjectLink> {
    return (await api.post<NgsMolBioProjectLink>(
        `/api/projects/${segment(globalProjectId)}/ngs-molbio-links`,
        request,
    )).data;
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

export async function updateGlobalExperiment(projectId: string, experimentId: string, request: HierarchyPatch): Promise<HierarchyMutationResult> {
    return (await api.patch<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}`, request)).data;
}

export async function archiveGlobalExperiment(projectId: string, experimentId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/archive`, { expected_head_generation: expectedHeadGeneration })).data;
}

export async function restoreGlobalExperiment(projectId: string, experimentId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/restore`, { expected_head_generation: expectedHeadGeneration })).data;
}

export async function createDomainExperiment(projectId: string, experimentId: string, request: DomainExperimentCreateRequest): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains`, request)).data;
}

export async function updateDomainExperiment(projectId: string, experimentId: string, domainId: string, request: HierarchyPatch): Promise<HierarchyMutationResult> {
    return (await api.patch<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}`, request)).data;
}

export async function archiveDomainExperiment(projectId: string, experimentId: string, domainId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}/archive`, { expected_head_generation: expectedHeadGeneration })).data;
}

export async function restoreDomainExperiment(projectId: string, experimentId: string, domainId: string, expectedHeadGeneration: number): Promise<HierarchyMutationResult> {
    return (await api.post<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}/restore`, { expected_head_generation: expectedHeadGeneration })).data;
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

export async function initializeNgsMolBioBinding(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    expectedDomainRevisionId: string,
): Promise<NgsMolBioBindingStatus> {
    return (await api.post<NgsMolBioBindingStatus>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/initialize`,
        { expected_domain_revision_id: expectedDomainRevisionId },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function reverifyNgsMolBioBinding(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    expectedDomainRevisionId: string,
    expectedBindingRevisionId: string,
): Promise<NgsMolBioBindingStatus> {
    return (await api.post<NgsMolBioBindingStatus>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/binding/reverify`,
        {
            expected_domain_revision_id: expectedDomainRevisionId,
            expected_binding_revision_id: expectedBindingRevisionId,
        },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

function domainOperatorPath(projectId: string, globalExperimentId: string, domainExperimentId: string): string {
    return `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}`;
}

export async function listDomainCapabilities(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
): Promise<DomainCapabilityList> {
    return (await api.get<DomainCapabilityList>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/capabilities`,
        { signal },
    )).data;
}

export async function listDomainDatasetKinds(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; registry_schema: string; registry_sha256: string; items: DomainDatasetKindDescriptor[] }> {
    return (await api.get<{ schema: string; registry_schema: string; registry_sha256: string; items: DomainDatasetKindDescriptor[] }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/dataset-kinds`,
        { signal },
    )).data;
}

export async function listDomainDatasets(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; items: DomainDatasetHead[]; next_cursor: string | null; has_more: boolean }> {
    return (await api.get<{ schema: string; items: DomainDatasetHead[]; next_cursor: string | null; has_more: boolean }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets`,
        { params: { limit: 100 }, signal },
    )).data;
}

export async function createDomainDataset(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    request: { name: string; dataset_kind: string; change_summary: string },
): Promise<DomainDatasetHead> {
    return (await api.post<DomainDatasetHead>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets`,
        request,
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function getDomainDataset(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    signal?: AbortSignal,
): Promise<DomainDatasetHead> {
    return (await api.get<DomainDatasetHead>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}`,
        { signal },
    )).data;
}

export async function listDomainDatasetRevisions(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; items: DomainDatasetRevisionSummary[]; next_cursor: string | null; has_more: boolean }> {
    return (await api.get<{ schema: string; items: DomainDatasetRevisionSummary[]; next_cursor: string | null; has_more: boolean }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/revisions`,
        { params: { limit: 100 }, signal },
    )).data;
}

export async function getDomainDatasetRevision(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    revisionId: string,
    signal?: AbortSignal,
): Promise<DomainDatasetRevision> {
    return (await api.get<DomainDatasetRevision>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/revisions/${segment(revisionId)}`,
        { signal },
    )).data;
}

export async function listDomainDatasetRevisionMembers(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    revisionId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; items: DomainDatasetMember[]; next_cursor: string | null; has_more: boolean }> {
    return (await api.get<{ schema: string; items: DomainDatasetMember[]; next_cursor: string | null; has_more: boolean }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/revisions/${segment(revisionId)}/members`,
        { params: { limit: 100 }, signal },
    )).data;
}

export async function reviseDomainDataset(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    expectedHeadGeneration: number,
    changeSummary: string,
    members: DomainDatasetMemberDraft[],
): Promise<DomainDatasetRevision> {
    return (await api.post<DomainDatasetRevision>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/revisions`,
        {
            expected_head_generation: expectedHeadGeneration,
            change_summary: changeSummary,
            members: members.map((member, ordinal) => ({ ...member, ordinal })),
        },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

async function setDomainDatasetLifecycle(
    operation: 'archive' | 'restore',
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    expectedHeadGeneration: number,
    changeSummary: string,
): Promise<DomainDatasetHead> {
    return (await api.post<DomainDatasetHead>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/datasets/${segment(datasetId)}/${operation}`,
        { expected_head_generation: expectedHeadGeneration, change_summary: changeSummary },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function archiveDomainDataset(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    expectedHeadGeneration: number,
    changeSummary: string,
): Promise<DomainDatasetHead> {
    return setDomainDatasetLifecycle('archive', projectId, globalExperimentId, domainExperimentId, datasetId, expectedHeadGeneration, changeSummary);
}

export async function restoreDomainDataset(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    datasetId: string,
    expectedHeadGeneration: number,
    changeSummary: string,
): Promise<DomainDatasetHead> {
    return setDomainDatasetLifecycle('restore', projectId, globalExperimentId, domainExperimentId, datasetId, expectedHeadGeneration, changeSummary);
}

export async function listDomainWorkflowPlans(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; items: DomainWorkflowPlanHead[]; next_cursor: string | null }> {
    return (await api.get<{ schema: string; items: DomainWorkflowPlanHead[]; next_cursor: string | null }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans`,
        { signal },
    )).data;
}

export async function createDomainWorkflowPlan(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    request: { name: string; capability_id: string; expected_domain_revision_id: string },
): Promise<DomainWorkflowPlanHead> {
    return (await api.post<DomainWorkflowPlanHead>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans`,
        request,
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function getDomainWorkflowPlan(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    planId: string,
    signal?: AbortSignal,
): Promise<DomainWorkflowPlanHead> {
    return (await api.get<DomainWorkflowPlanHead>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans/${segment(planId)}`,
        { signal },
    )).data;
}

export async function replaceDomainWorkflowPlanDraft(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    planId: string,
    expectedDraftGeneration: number,
    payload: JsonObject,
): Promise<{ schema: string; draft_id: string; plan_id: string; generation: number; payload: JsonObject; payload_sha256: string; updated_at: string }> {
    return (await api.put(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans/${segment(planId)}/draft`,
        { expected_draft_generation: expectedDraftGeneration, payload },
    )).data;
}

export async function publishDomainWorkflowPlanRevision(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    planId: string,
    request: { expected_head_generation: number; expected_draft_generation: number; change_summary: string },
): Promise<DomainWorkflowPlanRevision> {
    return (await api.post<DomainWorkflowPlanRevision>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans/${segment(planId)}/revisions`,
        request,
    )).data;
}

export async function listDomainWorkflowPlanRevisions(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    planId: string,
    signal?: AbortSignal,
): Promise<{ schema: string; items: DomainWorkflowPlanRevision[]; next_cursor: number | null }> {
    return (await api.get<{ schema: string; items: DomainWorkflowPlanRevision[]; next_cursor: number | null }>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans/${segment(planId)}/revisions`,
        { signal },
    )).data;
}

export async function prepareDomainWorkflowPlanRevision(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    planId: string,
    revisionId: string,
    inputDatasetRevisionIds: string[],
): Promise<DomainWorkflowPreparation> {
    return (await api.post<DomainWorkflowPreparation>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/plans/${segment(planId)}/revisions/${segment(revisionId)}/preparations`,
        { input_dataset_revision_ids: inputDatasetRevisionIds },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function issuePreparedLaunchContext(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    preparationId: string,
    returnUri: string,
): Promise<PreparedLaunchContext> {
    return (await api.post<PreparedLaunchContext>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/preparations/${segment(preparationId)}/launch-contexts`,
        { return_uri: returnUri },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
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

export async function getDomainRunGroup(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    runGroupId: string,
    signal?: AbortSignal,
): Promise<DomainRunGroup> {
    return (await api.get<DomainRunGroup>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/run-groups/${segment(runGroupId)}`,
        { signal },
    )).data;
}

export async function retryDomainRunGroup(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    runGroupId: string,
    expectedRunGroupGeneration: number,
    replacements: Array<{ run_id: string; preparation_id: string; launch_context_id: string | null }>,
): Promise<DomainRunGroup> {
    return (await api.post<DomainRunGroup>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/run-groups/${segment(runGroupId)}/retry`,
        { expected_run_group_generation: expectedRunGroupGeneration, replacements },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function resubmitDomainRunGroup(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    runGroupId: string,
    expectedRunGroupGeneration: number,
    preparationLaunches: PreparationLaunchRequest[],
): Promise<DomainRunGroup> {
    return (await api.post<DomainRunGroup>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/run-groups/${segment(runGroupId)}/resubmit`,
        {
            expected_run_group_generation: expectedRunGroupGeneration,
            preparation_launches: preparationLaunches,
        },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function cancelDomainRunGroup(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    runGroupId: string,
    expectedRunGroupGeneration: number,
    reason: string,
): Promise<RunControlCommandDocument> {
    return (await api.post<RunControlCommandDocument>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/run-groups/${segment(runGroupId)}/cancel`,
        { expected_run_group_generation: expectedRunGroupGeneration, reason },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
    )).data;
}

export async function reopenDomainResult(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    receiptId: string,
): Promise<DomainResultSurface> {
    return (await api.get<DomainResultSurface>(
        `${domainOperatorPath(projectId, globalExperimentId, domainExperimentId)}/results/${segment(receiptId)}/surface`,
    )).data;
}

export async function retryRunGroup(
    workspaceId: string,
    runGroupId: string,
    expectedGeneration: number,
    sourceDomainId: string,
    replacements: Array<{ run_id: string; preparation_id: string; launch_context_id: string | null }>,
): Promise<JsonObject> {
    const replacementPreparationIds = Object.fromEntries(
        replacements.map((item) => [item.run_id, item.preparation_id]),
    );
    const replacementLaunchContextIds = Object.fromEntries(
        replacements
            .filter((item) => item.launch_context_id !== null)
            .map((item) => [item.run_id, item.launch_context_id as string]),
    );
    return (await api.post<JsonObject>(
        `/api/experiment-workspaces/${segment(workspaceId)}/run-groups/${segment(runGroupId)}/retry`,
        {
            idempotency_key: crypto.randomUUID(),
            expected_generation: expectedGeneration,
            source_domain_id: sourceDomainId,
            replacement_preparation_ids: replacementPreparationIds,
            replacement_launch_context_ids: replacementLaunchContextIds,
        },
    )).data;
}

export async function resubmitRunGroup(
    workspaceId: string,
    runGroupId: string,
    expectedGeneration: number,
    sourceDomainId: string,
    preparationLaunches: PreparationLaunchRequest[],
): Promise<JsonObject> {
    return (await api.post<JsonObject>(
        `/api/experiment-workspaces/${segment(workspaceId)}/run-groups/${segment(runGroupId)}/resubmit`,
        {
            idempotency_key: crypto.randomUUID(),
            expected_generation: expectedGeneration,
            source_domain_id: sourceDomainId,
            preparation_ids: preparationLaunches.map((item) => item.preparation_id),
            launch_context_ids: Object.fromEntries(
                preparationLaunches
                    .filter((item) => item.launch_context_id !== null)
                    .map((item) => [item.preparation_id, item.launch_context_id as string]),
            ),
        },
    )).data;
}

export function isPermissionError(error: unknown): boolean {
    return isAxiosError(error) && (error.response?.status === 401 || error.response?.status === 403);
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
