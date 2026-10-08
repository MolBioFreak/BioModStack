import { isAxiosError } from 'axios';
import { api } from './api';

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = Record<string, JsonValue>;

export type HierarchyNodeType = 'project' | 'global_experiment' | 'domain_experiment' | 'virtual_folder';
export type MapNodeType = HierarchyNodeType | 'workflow' | 'run' | 'workflow_run' | 'result' | 'dataset' | 'external_entity_receipt';
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
    adapter_id: string | null;
    available_actions: string[];
    canonical_surface: ResultSurface | null;
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
        activity_next_cursor: string | null;
        map: BoundedPage<JsonObject> & { repeated_context_node_keys: string[] };
        runs: BoundedPage<ProjectRun>;
        results: BoundedPage<JsonObject>;
        lineage: BoundedPage<JsonObject>;
        notes: BoundedPage<JsonObject>;
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
    role: LineageRole;
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
    role: LineageRole;
    project_head_generation: number;
    normalized_request_sha256: string;
    attached_at: string;
}

export interface ProjectSummaryOptions {
    focusId?: string;
    selectedNodeKey?: string;
    mapCursor?: string;
    runCursor?: string;
    resultCursor?: string;
    lineageCursor?: string;
    noteCursor?: string;
    activityCursor?: string;
    mapLimit?: number;
    runLimit?: number;
    resultLimit?: number;
    lineageLimit?: number;
    noteLimit?: number;
    activityLimit?: number;
    signal?: AbortSignal;
}

export interface ProjectCreateRequest {
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
}

export interface GlobalExperimentCreateRequest {
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

export interface DomainExperimentCreateRequest {
    schema: 'bms.domain-experiment.v1';
    domain_kind: 'protein_in_silico' | 'ngs_molbio';
    domain_contract_version?: string;
    name: string;
    objective?: string;
    status?: 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked';
    tags?: string[];
    change_summary?: string;
    domain_payload: JsonObject;
}

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

export function normalizeProjectManagerReadModel(readModel: ProjectManagerReadModel): ProjectManagerReadModel {
    return readModel;
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
        },
        signal: options.signal,
    });
    return response.data;
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}`, { signal })).data;
}

export async function listGlobalExperiments(projectId: string, signal?: AbortSignal): Promise<HierarchyMutationResult[]> {
    return (await api.get<HierarchyMutationResult[]>(`/api/projects/${segment(projectId)}/experiments`, { signal })).data;
}

export async function listDomainExperiments(projectId: string, experimentId: string, signal?: AbortSignal): Promise<HierarchyMutationResult[]> {
    return (await api.get<HierarchyMutationResult[]>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains`, { signal })).data;
}

export async function getGlobalExperiment(projectId: string, experimentId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}`, { signal })).data;
}

export async function getDomainExperiment(projectId: string, experimentId: string, domainId: string, signal?: AbortSignal): Promise<HierarchyMutationResult> {
    return (await api.get<HierarchyMutationResult>(`/api/projects/${segment(projectId)}/experiments/${segment(experimentId)}/domains/${segment(domainId)}`, { signal })).data;
}

export async function getProjectSummary(projectId: string, options: ProjectSummaryOptions = {}): Promise<ProjectManagerReadModel> {
    const response = await api.get<ProjectManagerReadModel>(`/api/projects/${segment(projectId)}/summary`, {
        params: {
            focus_id: options.focusId,
            selected_node_key: options.selectedNodeKey,
            map_cursor: options.mapCursor,
            run_cursor: options.runCursor,
            result_cursor: options.resultCursor,
            lineage_cursor: options.lineageCursor,
            note_cursor: options.noteCursor,
            activity_cursor: options.activityCursor,
            map_limit: options.mapLimit,
            run_limit: options.runLimit,
            result_limit: options.resultLimit,
            lineage_limit: options.lineageLimit,
            note_limit: options.noteLimit,
            activity_limit: options.activityLimit,
        },
        signal: options.signal,
    });
    return normalizeProjectManagerReadModel(response.data);
}

export async function listDomainAdapters(signal?: AbortSignal): Promise<DomainAdapterRegistry> {
    const response = await api.get<DomainAdapterRegistry>('/api/domain-adapters', { signal });
    return response.data;
}

export async function searchAdapterEntities(adapterId: string, query: string, limit = 25, signal?: AbortSignal): Promise<AdapterSearchResult> {
    const response = await api.get<AdapterSearchResult>(`/api/domain-adapters/${segment(adapterId)}/entities/search`, {
        params: { q: query, limit },
        signal,
    });
    return response.data;
}

export async function attachExistingEntity(
    projectId: string,
    globalExperimentId: string,
    domainExperimentId: string,
    request: AttachExistingRequest,
): Promise<AttachmentReceipt> {
    const response = await api.post<AttachmentReceipt>(
        `/api/projects/${segment(projectId)}/experiments/${segment(globalExperimentId)}/domains/${segment(domainExperimentId)}/attach`,
        request,
    );
    return response.data;
}

export async function getResultSurface(projectId: string, receiptId: string, signal?: AbortSignal): Promise<ResultSurface> {
    const response = await api.get<ResultSurface>(
        `/api/projects/${segment(projectId)}/receipts/${segment(receiptId)}/surface`,
        { signal },
    );
    return response.data;
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
