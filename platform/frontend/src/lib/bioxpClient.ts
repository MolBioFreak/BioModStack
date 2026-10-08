import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api.js';

export type AxisName = 'x' | 'y' | 'z' | 'g' | 'door';
export type ThermalBankName = 'nest' | 'lid' | 'pedestal';
export type ChillerBankName = 'rc' | 'oc';

type BioXpPayload = Record<string, UntypedApiValue>;

export interface BioXpStatus {
    status: string;
    transport: string;
    runtime_available?: boolean;
    hardware_connected: boolean;
    linkage_configured?: boolean;
    linkage_url?: string | null;
    recommended_url?: string | null;
    startup_error: string | null;
    status_error?: string | null;
    proxy_error?: {
        status_code?: number;
        detail?: unknown;
    } | null;
    board_status?: Record<string, UntypedApiValue> | null;
    deck_io_snapshot?: Record<string, number | null> | null;
}

export interface BioXpInterlinkSettings {
    robot_api_url: string;
    robot_ssh_host?: string;
    connection_mode?: string;
    display_name?: string;
}

export interface BioXpInterlinkActionRequest {
    operator_ack?: string;
    reason?: string;
    sudo_password?: string;
    watch_until_ready?: boolean;
    tail?: number;
}

export interface BioXpInterlinkState {
    component?: string;
    configured: boolean;
    active: boolean;
    connection_mode?: string;
    display_name?: string;
    robot_api_url?: string | null;
    robot_ssh_host?: string | null;
    recommended_url?: string | null;
    reachable?: boolean | null;
    hardware_connected?: boolean | null;
    maintenance_state?: Record<string, UntypedApiValue> | null;
    last_probe_at?: string | null;
    last_status?: Record<string, UntypedApiValue> | null;
    last_error?: Record<string, UntypedApiValue> | null;
    lifecycle_action?: string | null;
    control_mode?: string;
    runtime_note?: string;
    auto_connect_on_launch?: boolean;
}

export interface AxisStatus {
    axis: string;
    preset: Record<string, UntypedApiValue>;
    status: {
        position?: { position: number | null; ok: boolean };
        speed?: { speed: number | null; ok: boolean };
        max_current?: { value?: number | null; ok?: boolean };
        switches?: {
            left_state?: number | null;
            right_state?: number | null;
        };
    };
    switch_activity: {
        left_state?: number | null;
        right_state?: number | null;
        left_active?: boolean | null;
        right_active?: boolean | null;
    };
}

export interface AxisStatusBatchResponse {
    axes: AxisName[];
    rows: Partial<Record<AxisName, AxisStatus>>;
}

export interface MotionTruth {
    evidence_level?: string;
    controller_reported_position?: boolean;
    controller_reported_switches?: boolean;
    physical_motion_confirmed?: boolean;
    independent_evidence_required?: boolean;
    summary?: string;
    recommended_next_evidence?: string[];
}

export interface MotionPrepPolicy {
    axis?: AxisName | string;
    reuse_requested?: boolean;
    armed_and_live?: boolean;
    debug_flag_required?: boolean;
    debug_flag_enabled?: boolean;
    reuse_allowed?: boolean;
    reuse_used?: boolean;
    interlock_reused?: boolean;
    board_activation_skipped?: boolean;
    axis_prep_skipped?: boolean;
    note?: string;
}

export interface MotionArtifactBundle {
    schema_version?: string;
    bundle_kind?: string;
    bundle_id?: string;
    created_at?: string;
    command?: string;
    axis?: AxisName | string;
    dry_run?: boolean;
    root?: string;
    bundle_dir?: string;
    metadata_path?: string;
    request_path?: string;
    response_path?: string;
    operator_note?: string | null;
    snapshot_refs?: string[];
}

export interface AxisMotionResult {
    axis: AxisName | string;
    board_status?: Record<string, UntypedApiValue> | null;
    interlock?: Record<string, UntypedApiValue> | null;
    prep?: Record<string, UntypedApiValue> | null;
    prep_policy?: MotionPrepPolicy | null;
    motion_truth?: MotionTruth | null;
    artifact_bundle?: MotionArtifactBundle | null;
    motion_profile?: Record<string, UntypedApiValue> | null;
    position_before?: { position?: number | null; ok?: boolean } | null;
    position_after?: { position?: number | null; ok?: boolean } | null;
    position_delta?: number | null;
    target_position?: number | null;
    switch_activity_before?: Record<string, UntypedApiValue> | null;
    switch_activity_after?: Record<string, UntypedApiValue> | null;
    move?: Record<string, UntypedApiValue> | null;
    wait?: Record<string, UntypedApiValue> | null;
    home?: Record<string, UntypedApiValue> | null;
    dry_run?: boolean;
    skipped_hardware_io?: boolean;
    message?: string | null;
}

export interface MotionArtifactOptions {
    capture_bundle?: boolean;
    dry_run_bundle?: boolean;
    operator_note?: string;
    snapshot_refs?: string[];
}

export interface MotionPowerStatus {
    hardware_connected?: boolean;
    board_status?: Record<string, UntypedApiValue> | null;
    deck_io_snapshot?: Record<string, number | null> | null;
    rail_24v?: {
        raw?: number | null;
        no24v?: boolean | null;
        ack?: Record<string, UntypedApiValue> | null;
    } | null;
    motion_arm?: Record<string, UntypedApiValue> | null;
    latch_override?: Record<string, UntypedApiValue> | null;
}

export interface MotionInterlockOverrideState {
    enabled: boolean;
    override_latch_sensor?: boolean;
    override_rail_24v?: boolean;
    reason?: string;
    note?: string;
    seq?: number;
    updated_ms?: number;
}

export interface MotionInterlockOverrideResponse {
    state: MotionInterlockOverrideState;
    gate?: Record<string, UntypedApiValue> | null;
    motion_arm?: Record<string, UntypedApiValue> | null;
    warning?: string;
}

export interface MotionInterlockOverridePayload {
    enabled: boolean;
    override_latch?: boolean;
    override_24v?: boolean;
    override_latch_sensor?: boolean;
    override_rail_24v?: boolean;
    reason: string;
    operator_ack: 'INTERLOCK_OVERRIDE';
    operator?: string;
}

export type GantryAxisName = Extract<AxisName, 'x' | 'y' | 'z'>;

export interface MotionAxisCurrentPayload {
    axes?: GantryAxisName[];
    run_current?: number;
    standby_current?: number;
}

export interface MotionAxisCurrentResponse {
    ok: boolean;
    axes: Partial<Record<GantryAxisName, Record<string, UntypedApiValue>>>;
    current_param_bounds?: string;
    motion_commanded?: boolean;
}

export interface CameraSnapshotResponse {
    ok: boolean;
    device: string | null;
    path: string | null;
    size?: number;
    rc?: number;
    output?: string;
    error?: string | null;
    image_b64?: string | null;
    image_error?: string | null;
}

export interface CameraDevicesResponse {
    ok: boolean;
    rows: Array<Record<string, UntypedApiValue>>;
    preferred_device?: string | null;
}

export interface CameraControlRow {
    cid: number;
    type: number;
    name: string;
    minimum: number;
    maximum: number;
    step: number;
    default: number;
    flags: number;
    get?: {
        ok?: boolean;
        value?: number | null;
        device?: string;
        error?: string;
    };
}

export interface CameraControlsResponse {
    ok: boolean;
    device: string;
    rows: CameraControlRow[];
    error?: string | null;
}

export interface CameraControlWriteResponse {
    ok: boolean;
    cid: number;
    set_value: number;
    readback?: number | null;
    device: string;
    error?: string | null;
    stream_active?: boolean;
    stream_state?: Record<string, UntypedApiValue>;
}

export interface CameraStreamOptions {
    device: string;
    fps?: number;
    quality?: number;
    width?: number;
    height?: number;
    nonce?: number;
}

export interface RuntimeStatus {
    running: boolean;
    healthy?: boolean;
    stale_process?: boolean;
    host: string;
    port: number;
    runtime_url?: string | null;
    linkage_configured: boolean;
    linked_runtime_reachable: boolean;
    hardware_connected: boolean;
    admin_control_available: boolean;
    maintenance_mode?: string | null;
    recommended_url?: string | null;
    detail: string | null;
    proxy_error?: {
        status_code?: number;
        detail?: unknown;
    } | null;
    inferred_via_proxy?: boolean;
}

export interface BioXpCapabilities {
    linkage_url?: string | null;
    linkage_configured?: boolean;
    recommended_url?: string | null;
    robot_hardware_assumption?: string;
    truth_source?: string;
    bms_role?: string;
    robot_local_expected_routes: Record<string, boolean>;
    bms_proxy_routes: Record<string, boolean>;
    default_operator_routes?: Record<string, boolean>;
    commissioning_only_routes?: Record<string, boolean>;
    disabled_routes?: Record<string, boolean>;
    notes?: string[];
}

export interface BioXpOperationCapabilities {
    schema_version?: string;
    linkage_url?: string | null;
    linkage_configured?: boolean;
    robot_openapi_reachable?: boolean;
    openapi_error?: Record<string, UntypedApiValue> | null;
    operations?: Record<string, {
        available?: boolean;
        required_routes?: Record<string, boolean>;
        risk?: string;
        operator_ack_required?: boolean;
    }>;
    safety_boundary?: string;
}

export interface BioXpOperationReadiness {
    schema_version?: string;
    linkage_url?: string | null;
    runtime_reachable?: boolean;
    hardware_connected?: boolean;
    layers?: Record<string, UntypedApiValue>;
    notes?: string[];
}

export interface BioXpOperationReport {
    schema_version?: string;
    operation?: string;
    risk?: string;
    operator_ack?: boolean;
    operator?: string;
    physical_confirmation_required?: boolean;
    truth_level?: string;
    before?: Record<string, UntypedApiValue>;
    actions?: Array<Record<string, UntypedApiValue>>;
    after?: Record<string, UntypedApiValue>;
    notes?: string[];
}

export interface BioXpOperationPayload extends BioXpPayload {
    operator_ack?: boolean;
    operator?: string;
    operator_note?: string;
    axis?: AxisName;
    steps?: number;
    steps_abs?: number;
}

export type OemStatusPayload = Record<string, UntypedApiValue>;

export interface MotionReferenceStatus {
    ok?: boolean;
    axes?: string[];
    rows?: Record<string, Record<string, UntypedApiValue>>;
    state_path?: string;
    generated_at?: string;
    error?: string | null;
}

export interface MotionReferenceMarkPayload {
    axes?: AxisName[];
    axis?: AxisName;
    reason?: string;
    operator?: string;
    origin_position_steps?: number | null;
    metadata?: Record<string, UntypedApiValue>;
}

export interface LiquidLocation {
    location_id: string;
    well_id?: string | null;
    plate_name?: string | null;
    z_offset_steps?: number | null;
}

export interface LiquidCommandPayload {
    volume_ul?: number;
    cycles?: number;
    pressure_profile?: string;
    tip_action?: 'load' | 'eject' | string;
    source?: LiquidLocation;
    destination?: LiquidLocation;
    dest?: LiquidLocation;
    liquid_class?: string;
    tip_id?: string | null;
    air_gap_ul?: number | null;
    blow_out?: boolean;
    operator?: string;
    metadata?: Record<string, UntypedApiValue>;
}

export interface LiquidStatus {
    ok?: boolean;
    transport?: string;
    channel?: string;
    bitrate?: number;
    available?: boolean;
    initialized?: boolean;
    software_initialized?: boolean;
    tip_loaded?: boolean;
    software_tip_loaded?: boolean;
    pressure_profile?: string;
    last_command?: string | null;
    hardware_tip_status?: Record<string, UntypedApiValue> | null;
    hardware_pressure?: Record<string, UntypedApiValue> | null;
    hardware_truth_level?: string;
    driver_result?: Record<string, UntypedApiValue> | null;
    preflight?: Record<string, UntypedApiValue> | null;
    error?: string | null;
}

export type LiquidCommandResponse = LiquidStatus & Record<string, UntypedApiValue>;

export interface CameraStreamState {
    ok?: boolean;
    active?: boolean;
    busy?: boolean;
    device?: string | null;
    pid?: number | null;
    stream?: Record<string, UntypedApiValue> | null;
    reset_provenance?: Record<string, UntypedApiValue> | null;
    error?: string | null;
}

export type VisionCommandPayload = Record<string, UntypedApiValue>;
export type VisionCommandResponse = Record<string, UntypedApiValue>;

export type DaemonStatus = RuntimeStatus;

export interface ProtocolCompilePayload {
    source_type?: 'native' | 'oem_xml';
    document?: Record<string, UntypedApiValue>;
    xml_path?: string;
}

export interface ProtocolJobSummary {
    job_id: string;
    status: string;
    dry_run?: boolean;
    protocol_id?: string;
    source_type?: string;
    created_at?: string;
    updated_at?: string;
    pending_review?: Record<string, UntypedApiValue> | null;
}

export interface ProtocolJobBundle {
    schema_version: string;
    job_id: string;
    created_at?: string;
    updated_at?: string;
    status: string;
    protocol: {
        source_type: string;
        source_path?: string | null;
        coverage?: Record<string, UntypedApiValue>;
        experiment?: Record<string, UntypedApiValue>;
        inventory?: Record<string, UntypedApiValue>;
        document: Record<string, UntypedApiValue>;
    };
    execution: {
        dry_run: boolean;
        runtime_state: Record<string, UntypedApiValue>;
    };
    operator?: {
        manual_review_required?: boolean;
        pending_review?: Record<string, UntypedApiValue> | null;
        reviews?: Array<Record<string, UntypedApiValue>>;
    };
    artifacts?: Record<string, UntypedApiValue>;
}

const invalidateBioXp = (queryClient: ReturnType<typeof useQueryClient>) => {
    queryClient.invalidateQueries({ queryKey: ['bioxp'] });
};

const bioxpHardwareMutationKey = (...parts: string[]) => ['bioxp', 'hardware', ...parts] as const;

export const useBioXpInterlinkState = (probe = false, refetchIntervalMs: number | false = false) =>
    useQuery<BioXpInterlinkState, Error>({
        queryKey: ['bioxp', 'interlink', 'state', probe],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/interlink/state', {
                params: probe ? { probe: true } : undefined,
                timeout: probe ? 8000 : 5000,
            });
            return res.data;
        },
        refetchInterval: refetchIntervalMs,
        retry: false,
    });

export const useSaveBioXpInterlinkSettings = () => {
    const queryClient = useQueryClient();
    return useMutation<BioXpInterlinkState, Error, BioXpInterlinkSettings>({
        mutationFn: async (settings) => {
            const res = await api.put('/api/bioxp/interlink/settings', settings);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useForgetBioXpInterlinkSettings = () => {
    const queryClient = useQueryClient();
    return useMutation<BioXpInterlinkState, Error, void>({
        mutationFn: async () => {
            const res = await api.delete('/api/bioxp/interlink/settings');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpInterlinkConnect = () => {
    const queryClient = useQueryClient();
    return useMutation<BioXpInterlinkState, Error, BioXpInterlinkSettings | void>({
        mutationFn: async (settings) => {
            const res = await api.post('/api/bioxp/interlink/connect', settings ?? undefined);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpInterlinkDisconnect = () => {
    const queryClient = useQueryClient();
    return useMutation<BioXpInterlinkState, Error, void>({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/interlink/disconnect');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpInterlinkDiagnostics = () => {
    const queryClient = useQueryClient();
    return useMutation<BioXpInterlinkState, Error, { probe?: boolean }>({
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/interlink/diagnostics', null, { params: { probe: payload.probe ?? true } });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpRuntimeReset = () => {
    const queryClient = useQueryClient();
    return useMutation<Record<string, UntypedApiValue>, Error, BioXpInterlinkActionRequest>({
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/interlink/runtime-reset', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpRobotReboot = () => {
    const queryClient = useQueryClient();
    return useMutation<Record<string, UntypedApiValue>, Error, BioXpInterlinkActionRequest>({
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/interlink/robot-reboot', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useBioXpInterlinkLogs = () =>
    useMutation<Record<string, UntypedApiValue>, Error, BioXpInterlinkActionRequest | void>({
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/interlink/logs', payload);
            return res.data;
        },
    });

export const useRuntimeStatus = () =>
    useQuery<RuntimeStatus, Error>({
        queryKey: ['bioxp', 'runtime'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/runtime/status', { timeout: 8000 });
            return res.data;
        },
        // Robot-local status probes use the same serialized runtime path as live
        // controls. Do not auto-poll this from the cockpit; repeated background
        // GETs were enough to make arming/motor testing unstable.
        refetchInterval: false,
        retry: false,
    });

export const useDaemonStatus = useRuntimeStatus;

export const useBioXpStatus = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<BioXpStatus, Error>({
        queryKey: ['bioxp', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/status', { timeout: 8000 });
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useBioXpCapabilities = (enabled = true) =>
    useQuery<BioXpCapabilities, Error>({
        queryKey: ['bioxp', 'capabilities'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/capabilities', { timeout: 8000 });
            return res.data;
        },
        enabled,
        refetchInterval: false,
        retry: false,
    });

export const useBioXpOperationCapabilities = (enabled = true) =>
    useQuery<BioXpOperationCapabilities, Error>({
        queryKey: ['bioxp', 'operations', 'capabilities'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/operations/capabilities', { timeout: 8000 });
            return res.data;
        },
        enabled,
        refetchInterval: false,
        retry: false,
    });

export const useBioXpOperationReadiness = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<BioXpOperationReadiness, Error>({
        queryKey: ['bioxp', 'operations', 'readiness'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/operations/readiness', { timeout: 8000 });
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

const useBioXpOperation = (operationPath: string, mutationName: string) => {
    const queryClient = useQueryClient();
    return useMutation<BioXpOperationReport, Error, BioXpOperationPayload | void>({
        mutationKey: bioxpHardwareMutationKey('operations', mutationName),
        mutationFn: async (payload = {}) => {
            const res = await api.post(`/api/bioxp/operations/${operationPath}`, payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const usePrepareSafeOperation = () => useBioXpOperation('motion/prepare-safe', 'prepare-safe');
export const useHeadClearLockOperation = () => useBioXpOperation('head/clear-lock', 'head-clear-lock');
export const useHeadLiftIncrementOperation = () => useBioXpOperation('head/lift-increment', 'head-lift-increment');
export const useMicroMoveProofOperation = () => useBioXpOperation('motion/micro-move-proof', 'micro-move-proof');
export const useLatchLockOperation = () => useBioXpOperation('latch/lock', 'latch-lock');
export const useLatchUnlockOperation = () => useBioXpOperation('latch/unlock', 'latch-unlock');
export const useEmergencyStopOperation = () => useBioXpOperation('emergency-stop', 'emergency-stop');

export const useOemStartupLatest = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<OemStatusPayload, Error>({
        queryKey: ['bioxp', 'oem', 'startup', 'latest'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/oem/startup/status/latest');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useOemRuntimeStatus = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<OemStatusPayload, Error>({
        queryKey: ['bioxp', 'oem', 'runtime', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/oem/runtime/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useOemRuntimeState = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<OemStatusPayload, Error>({
        queryKey: ['bioxp', 'oem', 'runtime', 'state'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/oem/runtime/state');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useOemStartupRequest = () => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload | void>({
        mutationKey: bioxpHardwareMutationKey('oem', 'startup', 'request'),
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/oem/startup/request', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useOemStartupDoorEvent = () => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload>({
        mutationKey: bioxpHardwareMutationKey('oem', 'startup', 'door-event'),
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/oem/startup/door_event', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useOemRuntimeRecover = () => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload | void>({
        mutationKey: bioxpHardwareMutationKey('oem', 'runtime', 'recover'),
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/oem/runtime/recover', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useOemRuntimeEmergencyStop = () => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload | void>({
        mutationKey: bioxpHardwareMutationKey('oem', 'runtime', 'emergency-stop'),
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/oem/runtime/emergency_stop', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const usePrepareToRunJobReadiness = () => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload | void>({
        mutationKey: bioxpHardwareMutationKey('oem', 'runtime', 'readiness', 'prepare-to-run-job', 'dry-run'),
        mutationFn: async (payload = {}) => {
            const res = await api.post('/api/bioxp/oem/runtime/readiness/prepare-to-run-job/dry-run', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useOemRuntimeCommand = (commandName: 'initializeSystem' | 'PrepareToRunJob' | 'validateJob' | 'enqueue' | 'abortjob' | 'unlockProcess' | 'wakefrompause') => {
    const queryClient = useQueryClient();
    return useMutation<OemStatusPayload, Error, BioXpPayload | void>({
        mutationKey: bioxpHardwareMutationKey('oem', 'runtime', 'command', commandName),
        mutationFn: async (payload = {}) => {
            const res = await api.post(`/api/bioxp/oem/runtime/commands/${commandName}`, payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useMotionRangeStatus = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<OemStatusPayload, Error>({
        queryKey: ['bioxp', 'motion', 'range', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/range/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useMotionReferenceStatus = (enabled = true, axes: AxisName[] = ['x', 'y', 'z', 'g', 'door'], refetchIntervalMs: number | false = 5000) =>
    useQuery<MotionReferenceStatus, Error>({
        queryKey: ['bioxp', 'motion', 'reference', axes.join(',')],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/reference/status', { params: { axes: axes.join(',') } });
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useMarkMotionReferenced = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'reference', 'mark-referenced'),
        mutationFn: async (payload: MotionReferenceMarkPayload) => {
            const res = await api.post('/api/bioxp/motion/reference/mark_referenced', payload);
            return res.data as MotionReferenceStatus;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useMarkMotionDesynced = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'reference', 'mark-desynced'),
        mutationFn: async (payload: MotionReferenceMarkPayload) => {
            const res = await api.post('/api/bioxp/motion/reference/mark_desynced', payload);
            return res.data as MotionReferenceStatus;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useLiquidStatus = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<LiquidStatus, Error>({
        queryKey: ['bioxp', 'liquid', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/liquid/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

const useLiquidCommand = (command: 'init' | 'tip' | 'aspirate' | 'dispense' | 'mix') => {
    const queryClient = useQueryClient();
    return useMutation<LiquidCommandResponse, Error, LiquidCommandPayload | void>({
        mutationKey: bioxpHardwareMutationKey('liquid', command),
        mutationFn: async (payload = {}) => {
            const res = await api.post(`/api/bioxp/liquid/${command}`, payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useLiquidInit = () => useLiquidCommand('init');
export const useLiquidTip = () => useLiquidCommand('tip');
export const useLiquidAspirate = () => useLiquidCommand('aspirate');
export const useLiquidDispense = () => useLiquidCommand('dispense');
export const useLiquidMix = () => useLiquidCommand('mix');

export const useCameraStreamState = (enabled = true, refetchIntervalMs: number | false = 3000) =>
    useQuery<CameraStreamState, Error>({
        queryKey: ['bioxp', 'camera', 'stream-state'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/stream_state');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useVisionInspect = () => {
    const queryClient = useQueryClient();
    return useMutation<VisionCommandResponse, Error, VisionCommandPayload>({
        mutationKey: bioxpHardwareMutationKey('vision', 'inspect'),
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/vision/inspect', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useVisionBarcodeRead = () => {
    const queryClient = useQueryClient();
    return useMutation<VisionCommandResponse, Error, VisionCommandPayload>({
        mutationKey: bioxpHardwareMutationKey('vision', 'barcode-read'),
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/vision/barcode/read', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useReconnectRuntime = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('runtime', 'reconnect'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/reconnect');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useProtocolJobs = (enabled = true, refetchIntervalMs: number | false = 10000) =>
    useQuery<{ rows: ProtocolJobSummary[] }, Error>({
        queryKey: ['bioxp', 'protocol', 'jobs'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/protocol/jobs');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useProtocolJob = (jobId: string | null, enabled = true) =>
    useQuery<ProtocolJobBundle, Error>({
        queryKey: ['bioxp', 'protocol', 'job', jobId],
        queryFn: async () => {
            const res = await api.get(`/api/bioxp/protocol/jobs/${jobId}`);
            return res.data;
        },
        enabled: enabled && !!jobId,
        retry: false,
    });

export const useCompileProtocol = () =>
    useMutation<ProtocolJobBundle['protocol'], Error, ProtocolCompilePayload>({
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/protocol/compile', payload);
            return res.data;
        },
    });

export const useExecuteProtocol = () => {
    const queryClient = useQueryClient();
    return useMutation<ProtocolJobBundle, Error, ProtocolCompilePayload & { dry_run?: boolean }>({
        mutationKey: bioxpHardwareMutationKey('protocol', 'execute'),
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/protocol/execute', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useReviewProtocolJob = () => {
    const queryClient = useQueryClient();
    return useMutation<ProtocolJobBundle, Error, { job_id: string; reviewer?: string; note?: string | null }>({
        mutationKey: bioxpHardwareMutationKey('protocol', 'review'),
        mutationFn: async ({ job_id, reviewer = 'operator', note = null }) => {
            const res = await api.post(`/api/bioxp/protocol/jobs/${job_id}/review`, { reviewer, note });
            return res.data;
        },
        onSuccess: (_, variables) => {
            invalidateBioXp(queryClient);
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'protocol', 'job', variables.job_id] });
        },
    });
};

export const useAxisStatus = (axis: AxisName | null, enabled = true, refetchIntervalMs: number | false = 3000) =>
    useQuery<AxisStatus, Error>({
        queryKey: ['bioxp', 'axis', axis],
        queryFn: async () => {
            const res = await api.get(`/api/bioxp/motion/axis/${axis}/status`);
            return res.data;
        },
        enabled: !!axis && enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useAxisStatusBatch = (axes: AxisName[], enabled = true, refetchIntervalMs: number | false = 3000) =>
    useQuery<AxisStatusBatchResponse, Error>({
        queryKey: ['bioxp', 'axis-batch', axes.join(',')],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/axes/status', {
                params: { axes: axes.join(',') },
            });
            return res.data;
        },
        enabled: enabled && axes.length > 0,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const usePrepareInterlock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'interlock', 'prepare'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/interlock/prepare');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMotionInterlockOverrideStatus = (enabled = true, refetchIntervalMs: number | false = 8000) =>
    useQuery<MotionInterlockOverrideResponse, Error>({
        queryKey: ['bioxp', 'motion', 'interlock', 'override'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/interlock/override', { timeout: 8000 });
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useSetMotionInterlockOverride = () => {
    const queryClient = useQueryClient();
    return useMutation<MotionInterlockOverrideResponse, Error, MotionInterlockOverridePayload>({
        mutationKey: bioxpHardwareMutationKey('motion', 'interlock', 'override'),
        mutationFn: async (payload) => {
            const res = await api.post('/api/bioxp/motion/interlock/override', payload, { timeout: 12000 });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useMotionPowerStatus = (enabled = true, refetchIntervalMs: number | false = 8000) =>
    useQuery<MotionPowerStatus, Error>({
        queryKey: ['bioxp', 'motion', 'power', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/power/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useMotionPowerEnable = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'power', 'enable'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/power/enable');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMotionPowerDiag = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'power', 'diag'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/power/diag');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetMotionAxesCurrent = () => {
    const queryClient = useQueryClient();
    return useMutation<MotionAxisCurrentResponse, Error, MotionAxisCurrentPayload | undefined>({
        mutationKey: bioxpHardwareMutationKey('motion', 'axes', 'current'),
        mutationFn: async (payload = {}) => {
            const runCurrent = payload.run_current ?? 10;
            const res = await api.post('/api/bioxp/motion/axes/current', {
                axes: payload.axes ?? ['x', 'y', 'z'],
                run_current: runCurrent,
                standby_current: payload.standby_current ?? 10,
            });
            return res.data;
        },
        onSuccess: () => {
            invalidateBioXp(queryClient);
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'motion', 'power', 'status'] });
        }
    });
};

export const useMotionArmStrictStartup = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'arm', 'strict-startup'),
        mutationFn: async ({ run_homing = false }: { run_homing?: boolean } = {}) => {
            const res = await api.post('/api/bioxp/motion/arm/strict_startup', { run_homing });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export type OemMotionModePayload = {
    operator?: string;
    operator_ack?: string;
    run_homing?: boolean;
    dry_run?: boolean;
    capture_bundle?: boolean;
    source?: string;
    [key: string]: UntypedApiValue;
};

const OEM_MOTION_MODE_ROUTES: Record<'home_xy' | 'rehome' | 'initialize_motion', string> = {
    home_xy: '/api/bioxp/motion/oem/home_xy',
    rehome: '/api/bioxp/motion/oem/rehome',
    initialize_motion: '/api/bioxp/motion/oem/initialize_motion',
};

const useOemMotionMode = (mode: 'home_xy' | 'rehome' | 'initialize_motion', timeout_s?: number) => {
    const queryClient = useQueryClient();
    return useMutation<BioXpPayload, Error, OemMotionModePayload | undefined>({
        mutationKey: bioxpHardwareMutationKey('motion', 'oem', mode),
        mutationFn: async (payload = {}) => {
            const res = await api.post(OEM_MOTION_MODE_ROUTES[mode], {
                operator: 'bms-cockpit',
                source: `bms-oem-${mode}`,
                timeout_s,
                ...payload,
            });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient),
    });
};

export const useOemHomeXY = () => useOemMotionMode('home_xy', 120.0);
export const useOemRehome = () => useOemMotionMode('rehome', 180.0);
export const useOemInitializeMotion = () => useOemMotionMode('initialize_motion', 180.0);

export const useMotionHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'hard-reset'),
        mutationFn: async ({ rounds }: { rounds: number }) => {
            const res = await api.post('/api/bioxp/motion/hard_reset', { rounds });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useClearLock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'clear-lock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/clear_lock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMoveRelative = () => {
    const queryClient = useQueryClient();
    return useMutation<AxisMotionResult, Error, {
        axis: AxisName;
        steps: number;
        wait_timeout_s?: number;
        speed?: number;
        acc?: number;
        reuse_prepared?: boolean;
        capture_bundle?: boolean;
        dry_run_bundle?: boolean;
        operator_note?: string;
        snapshot_refs?: string[];
    }>({
        mutationKey: bioxpHardwareMutationKey('motion', 'relative'),
        mutationFn: async ({
            axis,
            steps,
            wait_timeout_s = 15.0,
            speed,
            acc,
            reuse_prepared = false,
            capture_bundle = false,
            dry_run_bundle = false,
            operator_note,
            snapshot_refs = [],
        }) => {
            const res = await api.post('/api/bioxp/motion/axis/relative', {
                axis,
                steps,
                wait_timeout_s,
                speed,
                acc,
                reuse_prepared,
                capture_bundle,
                dry_run_bundle,
                operator_note,
                snapshot_refs,
            });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useMoveAbsolute = () => {
    const queryClient = useQueryClient();
    return useMutation<AxisMotionResult, Error, { axis: AxisName; position_steps: number; speed?: number; acc?: number } & MotionArtifactOptions>({
        mutationKey: bioxpHardwareMutationKey('motion', 'absolute'),
        mutationFn: async ({ axis, position_steps, speed, acc, capture_bundle = false, dry_run_bundle = false, operator_note, snapshot_refs = [] }) => {
            const res = await api.post('/api/bioxp/motion/axis/absolute', {
                axis,
                position_steps,
                wait_timeout_s: 60.0,
                speed,
                acc,
                capture_bundle,
                dry_run_bundle,
                operator_note,
                snapshot_refs,
            });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

const invalidateAxisMotion = (queryClient: ReturnType<typeof useQueryClient>, axis: AxisName) => {
    queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', axis] });
    queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
    queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
};

export const useZeroAxis = () => {
    const queryClient = useQueryClient();
    return useMutation<AxisMotionResult, Error, { axis: AxisName; speed?: number } & MotionArtifactOptions>({
        mutationKey: bioxpHardwareMutationKey('motion', 'zero'),
        mutationFn: async ({ axis, speed, capture_bundle = false, dry_run_bundle = false, operator_note, snapshot_refs = [] }) => {
            const res = await api.post('/api/bioxp/motion/axis/zero', {
                axis,
                wait_timeout_s: 60.0,
                speed,
                capture_bundle,
                dry_run_bundle,
                operator_note,
                snapshot_refs,
            });
            return res.data;
        },
        onSuccess: (_, variables) => invalidateAxisMotion(queryClient, variables.axis)
    });
};

export const useHomeAxis = () => {
    const queryClient = useQueryClient();
    return useMutation<AxisMotionResult, Error, { axis: AxisName; speed?: number } & MotionArtifactOptions>({
        mutationKey: bioxpHardwareMutationKey('motion', 'switch-home'),
        mutationFn: async ({ axis, speed, capture_bundle = false, dry_run_bundle = false, operator_note, snapshot_refs = [] }) => {
            const res = await api.post('/api/bioxp/motion/axis/home', {
                axis,
                timeout_s: 90.0,
                speed,
                capture_bundle,
                dry_run_bundle,
                operator_note,
                snapshot_refs,
            });
            return res.data;
        },
        onSuccess: (_, variables) => invalidateAxisMotion(queryClient, variables.axis)
    });
};

export const useLatchStatus = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'latch'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/latch/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useLatchLock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('latch', 'lock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/latch/lock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLatchUnlock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('latch', 'unlock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/latch/unlock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedRgb = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'rgb'),
        mutationFn: async (payload: { r: number; g: number; b: number }) => {
            const res = await api.post('/api/bioxp/led/rgb', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedPct = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'pct'),
        mutationFn: async (pct: number) => {
            const res = await api.post('/api/bioxp/led/pct', { pct });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedOff = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'off'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/led/off');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalSnapshot = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'thermal', 'snapshot'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/thermal/snapshot');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useSetThermalTemp = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'set-temp'),
        mutationFn: async ({ bank, target_temp_c }: { bank: ThermalBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/thermal/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalFan = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'fan'),
        mutationFn: async ({ speed }: { speed: number }) => {
            const res = await api.post('/api/bioxp/thermal/fan', { speed });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalPwm = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'pwm'),
        mutationFn: async ({ bank, pwm }: { bank: ThermalBankName; pwm: number }) => {
            const res = await api.post('/api/bioxp/thermal/pwm', { bank, pwm });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalRates = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'rates'),
        mutationFn: async ({ bank, cool_rate_c_s, heat_rate_c_s }: { bank: ThermalBankName; cool_rate_c_s: number; heat_rate_c_s: number }) => {
            const res = await api.post('/api/bioxp/thermal/rates', { bank, cool_rate_c_s, heat_rate_c_s });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'baseline'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/baseline');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalFastProfile = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'fast-profile'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/fast_profile');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'hard-reset'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/hard_reset');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerSnapshot = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'chiller', 'snapshot'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/chiller/snapshot');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useSetChillerTemp = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'set-temp'),
        mutationFn: async ({ bank, target_temp_c }: { bank: ChillerBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/chiller/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerFan = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'fan'),
        mutationFn: async ({ bank, speed }: { bank: ChillerBankName; speed: number }) => {
            const res = await api.post('/api/bioxp/chiller/fan', { bank, speed });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerPwm = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'pwm'),
        mutationFn: async ({ bank, pwm }: { bank: ChillerBankName; pwm: number }) => {
            const res = await api.post('/api/bioxp/chiller/pwm', { bank, pwm });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerRates = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'rates'),
        mutationFn: async ({ bank, cool_rate_c_s, heat_rate_c_s }: { bank: ChillerBankName; cool_rate_c_s: number; heat_rate_c_s: number }) => {
            const res = await api.post('/api/bioxp/chiller/rates', { bank, cool_rate_c_s, heat_rate_c_s });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'baseline'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/chiller/baseline');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'hard-reset'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/chiller/hard_reset');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useCameraDevices = (enabled = true) =>
    useQuery<CameraDevicesResponse, Error>({
        queryKey: ['bioxp', 'camera', 'devices'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/devices');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 10000 : false,
        retry: false,
    });

export const useCameraControls = (device: string, enabled = true) =>
    useQuery<CameraControlsResponse, Error>({
        queryKey: ['bioxp', 'camera', 'controls', device],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/controls', { params: { device } });
            return res.data;
        },
        enabled: enabled && !!device,
        refetchInterval: enabled ? 10000 : false,
        retry: false,
    });

export const useSetCameraControl = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ device, cid, value }: { device: string; cid: number; value: number }) => {
            const res = await api.post('/api/bioxp/camera/control', { device, cid, value });
            return res.data as CameraControlWriteResponse;
        },
        onSuccess: (data, variables) => {
            queryClient.setQueryData<CameraControlsResponse | undefined>(
                ['bioxp', 'camera', 'controls', variables.device],
                (current) => {
                    if (!current?.rows?.length) {
                        return current;
                    }
                    return {
                        ...current,
                        rows: current.rows.map((row) =>
                            row.cid === variables.cid
                                ? {
                                    ...row,
                                    get: {
                                        ...(row.get ?? {}),
                                        ok: data.ok,
                                        value: typeof data.readback === 'number' ? data.readback : variables.value,
                                        device: variables.device,
                                    },
                                }
                                : row,
                        ),
                    };
                },
            );
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'camera', 'controls', variables.device] });
        },
    });
};

export const useCameraSnapshot = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/snapshot', { device });
            return res.data as CameraSnapshotResponse;
        }
    });

export const useCameraStreamHealth = () =>
    useMutation({
        mutationFn: async ({ device, seconds }: { device: string; seconds: number }) => {
            const res = await api.post('/api/bioxp/camera/stream_health', { device, seconds });
            return res.data;
        }
    });

export const useCameraAutoRecover = () =>
    useMutation({
        mutationFn: async ({ device, max_resets }: { device: string; max_resets: number }) => {
            const res = await api.post('/api/bioxp/camera/auto_recover', { device, max_resets });
            return res.data;
        }
    });

export const useCameraReset = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/reset', { device });
            return res.data;
        }
    });

export const useCameraStop = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/stop', { device });
            return res.data;
        }
    });

export const getCameraStreamUrl = ({
    device,
    fps = 8,
    quality = 7,
    width = 640,
    height = 480,
    nonce,
}: CameraStreamOptions) => {
    const params = new URLSearchParams({
        device,
        fps: String(fps),
        quality: String(quality),
        width: String(width),
        height: String(height),
    });
    if (nonce != null) {
        params.set('nonce', String(nonce));
    }
    return `/api/bioxp/camera/mjpeg?${params.toString()}`;
};
