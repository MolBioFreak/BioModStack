import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './api.js';

export interface BioXpConnectionSnapshot {
    configured: boolean;
    active: boolean;
    generation: number;
    target_url: string | null;
    reachable: boolean | null;
    runtime_ready: boolean | null;
    hardware_ready: boolean | null;
    hardware_observed_at: string | null;
    hardware_fresh: boolean | null;
    hardware_stale: boolean;
    hardware_evidence_error: string | null;
    capabilities: string[];
    observed_at: string | null;
    freshness_budget_seconds: number;
    fresh: boolean | null;
    last_error: string | null;
    command_active: boolean;
    startup_lifecycle: BioXpStartupLifecycle | null;
    maintenance_state: BioXpMaintenanceState | null;
}

export interface BioXpMaintenanceState {
    motion_blocked?: boolean | null;
    recovery_required?: boolean | null;
    usb_owner?: string | null;
    blocked_by?: string | null;
    block_reason?: string | null;
    block_source?: string | null;
    recovery_hint?: string | null;
    blocked_at?: string | null;
    recovered_at?: string | null;
    last_recovery?: Record<string, unknown> | null;
}

export interface BioXpStartupStage {
    name?: string;
    state: string;
    prerequisite?: string | null;
    repeatable?: boolean;
    attempt_count?: number;
    started_at?: string | null;
    completed_at?: string | null;
    error?: string | null;
    evidence?: unknown;
}

export interface BioXpStartupLifecycle {
    state?: string;
    next_stage?: string | null;
    stages: Record<string, BioXpStartupStage>;
}

export interface BioXpStatusResponse {
    connection: BioXpConnectionSnapshot;
    available_commands: string[];
    available_controls: string[];
    unavailable_commands: Record<string, string>;
    emergency_stop: {
        delivery_available: boolean;
        physical_effect_verifiable: false;
    };
    startup_warnings: string[];
    connection_access?: {
        enabled: boolean;
        server_setting: string;
        hardware_effects_authorized: false;
    };
    mutation_access?: {
        enabled: boolean;
        server_setting: string;
        secret_required: false;
    };
    legacy_job_migration: {
        migrated: number;
        quarantined: number;
    };
}

export interface BioXpCameraStatus {
    schema_version: 'bioxp.camera_status.v1';
    state: 'live' | 'stale' | 'unavailable';
    available: boolean;
    frame_sequence: number | null;
    frame_captured_at: string | null;
    frame_age_seconds: number | null;
    freshness_budget_seconds: number;
    provider_generation: number;
    dropped_frames: number;
    content_sha256: string | null;
    detail: string | null;
    connection_generation: number;
}

export interface BioXpCameraImage {
    blob: Blob;
    etag: string;
    sha256: string;
    connectionGeneration: number;
}

export const BIOXP_CAMERA_ENDPOINTS = Object.freeze({
    status: '/api/bioxp/camera/status',
    latest: '/api/bioxp/camera/frame/latest',
    snapshot: '/api/bioxp/camera/snapshot',
});

export interface BioXpCommandRecord {
    command_id: string;
    command: string;
    idempotency_key: string;
    generation: number;
    status: 'queued' | 'acknowledged' | 'delivered_unacknowledged' | 'delivery_failed';
    started_at: string;
    finished_at: string;
    remote_acknowledged: boolean;
    physical_effect_verified: false;
    detail: string;
    handler_response: Record<string, unknown> | null;
}

export type BioXpCommandName =
    | 'activate_usb_for_service'
    | 'collect_hardware_snapshot'
    | 'initialize_oem_environment'
    | 'initialize_motors'
    | 'run_oem_motor_stage'
    | 'record_oem_motor_stage_observation'
    | 'collect_axis_diagnostics'
    | 'run_axis_diagnostic'
    | 'stop_axis_diagnostic'
    | 'recover_motion_non_homing'
    | 'start_job'
    | 'pause_job'
    | 'resume_job'
    | 'stop_job'
    | 'recover_runtime';

export type BioXpActiveCommandName =
    | 'collect_hardware_snapshot'
    | 'collect_axis_diagnostics'
    | 'run_axis_diagnostic'
    | 'stop_axis_diagnostic'
    | 'recover_motion_non_homing';

export type BioXpCommandPayload =
    | {
        command: 'recover_motion_non_homing';
        expected_generation: number;
        idempotency_key: string;
        operator_ack: 'RECOVER_MOTION';
        reason: string;
    }
    | ({
        command: Exclude<BioXpActiveCommandName, 'recover_motion_non_homing'>;
        expected_generation: number;
        idempotency_key: string;
    } & Record<string, unknown>);

export interface BioXpProfileView {
    configured: boolean;
    valid: boolean;
    display_name: string | null;
    target_url: string | null;
    detail?: string;
}

export interface BioXpProfileWrite {
    schema_version?: 1;
    display_name: string;
    api_url: string;
}

export type BioXpProtocolStep =
    | { action: 'initialize_motors' }
    | { action: 'start_job'; job_id: string }
    | { action: 'pause_job'; job_id: string }
    | { action: 'resume_job'; job_id: string }
    | { action: 'stop_job'; job_id: string }
    | { action: 'recover_runtime' };

export interface BioXpProtocol {
    schema_version?: 1;
    name: string;
    steps: BioXpProtocolStep[];
}

export interface BioXpCompiledProtocol {
    protocol: BioXpProtocol;
    compiled_hash: string;
    validation_status: 'validated_offline';
    robot_compatible: null;
    executable: false;
    required_capabilities: string[];
    blockers: string[];
}

export interface BioXpJob {
    job_id: string;
    idempotency_key: string;
    protocol: BioXpProtocol;
    compiled_hash: string;
    state: string;
    created_at: string;
    updated_at: string;
    detail: string | null;
    generation: number | null;
    remote_job_id: string | null;
}

export interface BioXpJobListResponse {
    jobs: BioXpJob[];
}

export interface BioXpProtocolSubmissionResponse {
    job: BioXpJob;
    delivery_attempted: false;
    robot_compatible: null;
}

export interface BioXpEmergencyResult {
    idempotency_key: string;
    generation: number;
    attempted_at: string;
    delivery_attempted: boolean;
    remote_acknowledged: boolean;
    physical_effect_verified: false;
    detail: string;
}

export interface BioXpOemFullLifecycleProvider {
    source_contract: boolean;
    implemented: boolean | string;
    live_bound: boolean;
    commissioned: boolean;
}

export interface BioXpOemFullLifecycleContract {
    schema_version: string;
    command: 'initialize_oem_movement_lifecycle';
    machine_serial: 206;
    registry_sha256: string;
    evidence_lock_sha256: string;
    evidence_lock_verified: boolean;
    source_registry_identity_verified: boolean;
    machine_configuration_verified: boolean;
    initialize_system_producers: ReadonlyArray<{
        producer: string;
        source_anchor: string;
        selected_by_this_route: boolean;
    }>;
    plan_available: boolean;
    plan_blockers: string[];
    live_creation_enabled: boolean;
    physical_commissioning_complete: boolean;
    providers: Record<string, BioXpOemFullLifecycleProvider>;
    safety_boundary: {
        caller_supplied_motion_parameters: false;
        dry_run_commands_hardware: false;
        queue_acceptance_is_execution: false;
        physical_effect_verified: false;
    };
}

export interface BioXpOemFullLifecycleStage {
    stage_id: string;
    status: string;
    source_anchor: string;
    would_command_hardware: boolean;
    would_command_physical_motion: boolean;
    movement_ledger_stage?: string;
    branch?: string;
    execution_semantics?: string;
    caller_result_used?: boolean;
}

export interface BioXpOemFullLifecycleRun {
    run_id: string;
    request: { mode: 'dry_run' };
    run_state: string;
    machine_serial: 206;
    registry_sha256: string;
    evidence_lock_sha256: string;
    evidence_lock_verified: true;
    source_registry_identity_verified: true;
    machine_configuration_verified: true;
    expected_next_stage: string | null;
    physical_motion_commanded: false;
    physical_effect_verified: false;
    stages: BioXpOemFullLifecycleStage[];
}

const statusKey = ['bioxp', 'status'] as const;
const profileKey = ['bioxp', 'profile'] as const;
const jobsKey = ['bioxp', 'jobs'] as const;
const fullLifecycleContractKey = ['bioxp', 'oem-full-lifecycle', 'contract'] as const;

function cameraImageFromResponse(response: {
    data: Blob;
    headers: Record<string, unknown>;
}): BioXpCameraImage {
    if (!(response.data instanceof Blob) || response.data.type !== 'image/jpeg' || response.data.size < 1) {
        throw new Error('BioXP camera proxy returned an invalid JPEG image');
    }
    const etag = String(response.headers.etag ?? '');
    const sha256 = String(response.headers['x-content-sha256'] ?? '');
    const generationText = String(response.headers['x-bioxp-connection-generation'] ?? '');
    const connectionGeneration = Number(generationText);
    if (!/^[0-9a-f]{64}$/.test(sha256)
        || (etag !== sha256 && etag !== `"${sha256}"`)
        || !Number.isSafeInteger(connectionGeneration)
        || connectionGeneration < 1) {
        throw new Error('BioXP camera proxy returned invalid image provenance');
    }
    return { blob: response.data, etag, sha256, connectionGeneration };
}
export function bioXpErrorText(error: unknown): string {
    if (error && typeof error === 'object') {
        const response = 'response' in error
            ? (error as { response?: { data?: { detail?: unknown } } }).response
            : undefined;
        const detail = response?.data?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        if (Array.isArray(detail)) {
            const normalized = detail
                .map((entry) => {
                    if (!entry || typeof entry !== 'object') return String(entry);
                    const item = entry as { loc?: unknown; msg?: unknown };
                    const location = Array.isArray(item.loc) ? item.loc.map(String).join('.') : '';
                    const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(entry);
                    return location ? `${location}: ${message}` : message;
                })
                .filter(Boolean)
                .join('; ');
            if (normalized) return normalized;
        }
        if ('message' in error && typeof error.message === 'string') return error.message;
    }
    return String(error ?? 'Unknown error');
}

export const useBioXpStatus = (enabled = true) => useQuery({
    queryKey: statusKey,
    queryFn: async () => (await api.get<BioXpStatusResponse>('/api/bioxp/status')).data,
    enabled,
    refetchInterval: enabled ? 5_000 : false,
    retry: false,
});

export const useBioXpCameraStatus = (
    connectionGeneration: number | null,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'camera', 'status', connectionGeneration],
    queryFn: async () => {
        if (connectionGeneration === null) throw new Error('An active BioXP connection generation is required');
        return (await api.get<BioXpCameraStatus>(BIOXP_CAMERA_ENDPOINTS.status, {
            params: { expected_generation: connectionGeneration },
        })).data;
    },
    enabled: enabled && connectionGeneration !== null,
    refetchInterval: enabled ? 1_000 : false,
    refetchIntervalInBackground: false,
    retry: false,
});

export async function fetchBioXpCameraFrame(connectionGeneration: number): Promise<BioXpCameraImage> {
    const response = await api.get<Blob>(BIOXP_CAMERA_ENDPOINTS.latest, {
        params: { expected_generation: connectionGeneration },
        responseType: 'blob',
    });
    return cameraImageFromResponse(response);
}

export async function captureBioXpCameraSnapshot(connectionGeneration: number): Promise<BioXpCameraImage> {
    const response = await api.post<Blob>(
        BIOXP_CAMERA_ENDPOINTS.snapshot,
        { expected_generation: connectionGeneration },
        { responseType: 'blob' },
    );
    return cameraImageFromResponse(response);
}

export const useBioXpOemFullLifecycleContract = (enabled = true) => useQuery({
    queryKey: fullLifecycleContractKey,
    queryFn: async () => (
        await api.get<BioXpOemFullLifecycleContract>('/api/bioxp/oem-full-lifecycle/contract')
    ).data,
    enabled,
    refetchInterval: enabled ? 10_000 : false,
    retry: false,
});

export const useBioXpOemFullLifecycleRun = (runId: string | null) => useQuery({
    queryKey: ['bioxp', 'oem-full-lifecycle', 'run', runId],
    queryFn: async () => (
        await api.get<BioXpOemFullLifecycleRun>(`/api/bioxp/oem-full-lifecycle/runs/${encodeURIComponent(runId ?? '')}/ledger`)
    ).data,
    enabled: Boolean(runId),
    retry: false,
});

export const useBioXpProfile = (enabled = true) => useQuery({
    queryKey: profileKey,
    queryFn: async () => (await api.get<BioXpProfileView>('/api/bioxp/profile')).data,
    enabled,
    retry: false,
});

export const useBioXpJobs = (enabled = true) => useQuery({
    queryKey: jobsKey,
    queryFn: async () => {
        const response = await api.get<BioXpJobListResponse>('/api/bioxp/jobs');
        return response.data.jobs;
    },
    enabled,
    refetchInterval: enabled ? 10_000 : false,
});

const useRefreshMutation = <TVariables, TData>(
    mutationFn: (variables: TVariables) => Promise<TData>,
) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn,
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: statusKey }),
                queryClient.invalidateQueries({ queryKey: profileKey }),
                queryClient.invalidateQueries({ queryKey: jobsKey }),
                queryClient.invalidateQueries({ queryKey: fullLifecycleContractKey }),
            ]);
        },
    });
};

export const useSaveBioXpProfile = () => useRefreshMutation(
    async (profile: BioXpProfileWrite) => (
        await api.put<BioXpProfileView>('/api/bioxp/profile', profile)
    ).data,
);

export const useForgetBioXpProfile = () => useRefreshMutation(
    async () => (await api.delete<{ forgotten: boolean }>('/api/bioxp/profile')).data,
);

export const useConnectBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/connect')).data,
);

export const useDisconnectBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/disconnect')).data,
);

export const useProbeBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/probe')).data,
);

export const useCompileBioXpProtocol = () => useMutation({
    mutationFn: async (protocol: BioXpProtocol) => (
        await api.post<BioXpCompiledProtocol>('/api/bioxp/protocols/compile', protocol)
    ).data,
});

export const useSubmitBioXpProtocol = () => useRefreshMutation(
    async ({ protocol, idempotencyKey }: {
        protocol: BioXpProtocol;
        idempotencyKey: string;
    }) => (
        await api.post<BioXpProtocolSubmissionResponse>('/api/bioxp/protocols/submit', {
            protocol,
            idempotency_key: idempotencyKey,
        })
    ).data,
);

export const useBioXpCommand = () => useRefreshMutation(
    async (payload: BioXpCommandPayload) => (
        await api.post<BioXpCommandRecord>('/api/bioxp/commands', payload)
    ).data,
);

export const usePlanBioXpOemFullLifecycle = () => useRefreshMutation(
    async ({ generation, machineSerial, registrySha256, evidenceLockSha256 }: {
        generation: number;
        machineSerial: 206;
        registrySha256: string;
        evidenceLockSha256: string;
    }) => (
        await api.post<BioXpOemFullLifecycleRun>('/api/bioxp/oem-full-lifecycle/runs', {
            expected_generation: generation,
            expected_machine_serial: machineSerial,
            expected_registry_sha256: registrySha256,
            expected_evidence_lock_sha256: evidenceLockSha256,
            idempotency_key: crypto.randomUUID(),
        })
    ).data,
);

export const useCancelBioXpOemFullLifecycle = () => useRefreshMutation(
    async ({ runId, generation, machineSerial, registrySha256, evidenceLockSha256 }: {
        runId: string;
        generation: number;
        machineSerial: 206;
        registrySha256: string;
        evidenceLockSha256: string;
    }) => (
        await api.post<BioXpOemFullLifecycleRun>(
            `/api/bioxp/oem-full-lifecycle/runs/${encodeURIComponent(runId)}/cancel`,
            {
                expected_generation: generation,
                expected_machine_serial: machineSerial,
                expected_registry_sha256: registrySha256,
                expected_evidence_lock_sha256: evidenceLockSha256,
            },
        )
    ).data,
);

export const useBioXpEmergencyStop = () => useRefreshMutation(
    async ({ generation }: { generation: number }) => (
        await api.post<BioXpEmergencyResult>('/api/bioxp/emergency-stop', {
            expected_generation: generation,
            idempotency_key: crypto.randomUUID(),
        })
    ).data,
);
