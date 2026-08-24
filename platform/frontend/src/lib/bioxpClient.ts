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
    automatic_snapshot_refresh: Record<string, unknown> | null;
    capabilities: string[];
    observed_at: string | null;
    freshness_budget_seconds: number | null;
    fresh: boolean | null;
    last_error: string | null;
    startup_lifecycle: BioXpStartupLifecycle | null;
    maintenance_state: BioXpMaintenanceState | null;
    ownership: BioXpOwnership | null;
}

export interface BioXpOwnership {
    transport?: string | null;
    usb?: string | null;
    router?: string | null;
    camera?: string | null;
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

export interface BioXpCameraStream {
    schema_version: 'bioxp.camera_stream.v1';
    state: 'off' | 'starting' | 'live' | 'error';
    active: boolean;
    stream_id: string | null;
    camera_ownership_epoch: number;
    fps: number | null;
    quality: number | null;
    width: number | null;
    height: number | null;
    frames_emitted: number;
    dropped_frames: number;
    latest_frame_at: string | null;
    last_error: string | null;
    idempotent: boolean | null;
    connection_generation: number;
}

export const BIOXP_CAMERA_ENDPOINTS = Object.freeze({
    status: '/api/bioxp/camera/status',
    latest: '/api/bioxp/camera/frame/latest',
    snapshot: '/api/bioxp/camera/snapshot',
    streamStart: '/api/bioxp/camera/stream/start',
    streamState: '/api/bioxp/camera/stream/state',
    mjpeg: '/api/bioxp/camera/mjpeg',
    streamStop: '/api/bioxp/camera/stream/stop',
});


export type BioXpOperatorActionKind = 'primitive' | 'meta';
export type BioXpOperatorSafetyClass = 'read_only' | 'service' | 'motion' | 'stop' | 'emergency';

export interface BioXpOperatorDependency {
    key: string;
    label: string;
    met: boolean;
    reason: string | null;
}

export interface BioXpOperatorDashboardAxis {
    axis: string;
    reference: string;
    position_steps: number | null;
    speed_steps_s: number | null;
    run_current: number | null;
    standby_current: number | null;
    left_switch_state: number | null;
    right_switch_state: number | null;
    left_switch_raw_active: boolean | null;
    right_switch_raw_active: boolean | null;
    left_switch_active: boolean | null;
    right_switch_active: boolean | null;
    left_switch_disabled: boolean | null;
    right_switch_disabled: boolean | null;
    coordinate_contract: string | null;
    min_steps: number | null;
    max_steps: number | null;
    motor_temperature_c: number | null;
    motor_temperature_available: boolean;
    telemetry_authority?: string | null;
    physical_position_verified?: false | null;
}

export interface BioXpOperatorDashboardXAxis {
    status: BioXpOperatorDashboardAxis | null;
    provider: {
        authority?: string | null;
        state?: string;
        reference_state?: string;
        source_min_steps?: number | null;
        source_max_steps?: number | null;
        effective_absolute_min_steps?: number | null;
        relative_limit_margin_steps?: number | null;
        current_generation?: number | null;
        current_board_lifecycle_generation?: number | null;
        board_generation_fresh?: boolean | null;
        lifecycle?: {
            state?: string;
            reference_state?: string;
            generation?: number | null;
            board_lifecycle_generation?: number | null;
            awaiting_observation_receipt_id?: string | null;
            last_failure?: unknown;
            latest_receipt?: Record<string, unknown> | null;
        };
        live_status?: {
            position_steps?: number | null;
            speed_steps_s?: number | null;
            max_speed?: number | null;
            max_acceleration?: number | null;
            max_current?: number | null;
            stall_guard?: number | null;
            left_switch_state?: number | null;
            right_switch_state?: number | null;
            left_switch_disabled?: boolean | null;
            right_switch_disabled?: boolean | null;
            profile_verified?: boolean;
            switch_mask_verified?: boolean | null;
            switch_mask_tuple?: Record<string, number | null>;
            switch_mask_policy?: 'observed_only_oem_source_omits_x_writes';
        };
        profile?: { verified?: boolean };
        switch_masks?: {
            expected?: Record<string, number>;
            verified?: boolean | null;
            observed?: Record<string, number | null> | null;
            policy?: 'observed_only_oem_source_omits_x_writes';
        };
        bound: boolean;
        physical_position_verified: false;
    };
    snapshot_freshness: Record<string, unknown>;
    last_failure: unknown;
    latest_receipt: Record<string, unknown> | null;
    authority: string;
    physical_position_verified: false;
}

export interface BioXpOperatorSuccessiveMoveQueueAxis {
    active_command_id: string | null;
    depth: number;
    head_action_id: string | null;
    state: 'running' | 'queued' | 'idle';
}

export interface BioXpOperatorDashboard {
    schema_version: 'bioxp.operator_dashboard.v1';
    ownership_generation: number;
    connection: { live: boolean; ownership: Record<string, unknown> };
    motion: { enabled: boolean; reason: string | null };
    operation: { state: string | null; reason: string | null };
    enclosure: { door_closed: boolean | null; latch_closed: boolean | null };
    axes: BioXpOperatorDashboardAxis[];
    x_axis: BioXpOperatorDashboardXAxis;
    z_axis: {
        status: BioXpOperatorDashboardAxis | null;
        provider: {
            bound?: boolean;
            board?: 4;
            motor?: 1;
            state?: string;
            expected_startup_stage?: string | null;
            startup_terminal_state?: string | null;
            switch_mask_policy?: 'observed_only_oem_source_omits_z_writes' | null;
            switch_mask_tuple?: Record<'12' | '13', number | null> | null;
            terminal_state?: {
                switch_mask_policy?: 'observed_only_oem_source_omits_z_writes' | null;
                switch_mask_tuple?: Record<'12' | '13', number | null> | null;
                position_steps?: number | null;
                speed_steps_s?: number | null;
            } | null;
            reference_state?: string;
            awaiting_observation_receipt_id?: string | null;
            last_failure?: unknown;
        };
        snapshot_freshness: Record<string, unknown>;
        last_failure: unknown;
        authority: string;
    };
    temperatures: Array<{ sensor: string; label: string; unit: '°C'; temperature_c: number | null; available: boolean }>;
    pipettes: BioXpPipettes;
    snapshot: { snapshot_id: string | null; freshness: { state?: string; age_s?: number | null; fresh_for_s?: number | null }; collection_triggered: false };
    successive_move_queue: Record<string, BioXpOperatorSuccessiveMoveQueueAxis>;
}

export type BioXpOperatorReceiptV2Status =
    | 'queued'
    | 'dispatched'
    | 'issued_pending'
    | 'interrupting'
    | 'completed'
    | 'failed'
    | 'cleared'
    | 'interrupted'
    | 'ambiguous'
    | 'rejected';

export interface BioXpOperatorReceiptV2 {
    schema_version: 'bioxp.operator_action_receipt.v2';
    command_id: string;
    action_id: string;
    status: BioXpOperatorReceiptV2Status;
    terminal: boolean;
    sequence: number;
    method_id: string | null;
    ownership_generation: number;
    expected_board_epoch_by_board: Record<string, number>;
    state_version: number;
    status_path: string;
    accepted_at: number;
    queued_at: number;
    dispatched_at: number | null;
    finished_at: number | null;
    terminal_receipt_id: string | null;
    completion_class: string | null;
    physical_effect_verified: boolean;
    error: { code: string; message: string; retryable: boolean } | null;
}

export type BioXpReceiptScalarV2 = number | string | boolean | null;

export interface BioXpOperatorReceiptDetailV2 extends BioXpOperatorReceiptV2 {
    canonical_inputs: Record<string, unknown>;
    requested_values: Record<string, BioXpReceiptScalarV2>;
    effective_values: Record<string, BioXpReceiptScalarV2>;
    observed_values: Record<string, BioXpReceiptScalarV2>;
    raw_return_layers: Record<string, unknown>;
    controller_evidence: Record<string, unknown>;
    transport_artifacts: Array<{ sha256: string; path: string; bytes: number }>;
    child_receipts: BioXpOperatorReceiptV2[];
    transitions: Array<{
        transition_id: string;
        from_status: BioXpOperatorReceiptV2Status | null;
        to_status: BioXpOperatorReceiptV2Status;
        at: number;
        reason: string | null;
    }>;
}

export interface BioXpYAxisV2 {
    axis: 'y';
    board_id: 4;
    motor_id: 0;
    ownership_generation: number;
    prior_board_epoch: number | null;
    active_board_epoch: number | null;
    prepared_board_epoch: number | null;
    lifecycle_state: 'unbound' | 'unprepared' | 'prepared_unreferenced' | 'referenced_ready' | 'generation_stale' | 'reconciliation_required' | 'faulted';
    reference_state: 'unreferenced' | 'referenced' | 'generation_stale' | 'reconciliation_required';
    position_steps: number | null;
    position_reply_valid: boolean;
    position_status_code: number | null;
    speed_steps_s: number | null;
    speed_reply_valid: boolean;
    speed_status_code: number | null;
    left_switch_raw: number | null;
    left_switch_reply_valid: boolean;
    left_switch_status_code: number | null;
    home_effective: boolean | null;
    profile_fingerprint: string | null;
    profile_readback_valid: boolean;
    profile_mismatches: string[];
    active_command: BioXpOperatorReceiptV2 | null;
    interrupt_epoch: number;
    latest_compact_receipt: BioXpOperatorReceiptV2 | null;
    last_discrepancy_steps: number | null;
    state_version: number;
    updated_at: number;
    physical_position_verified: boolean;
}

export interface BioXpBoard4AuthorityV2 {
    state: 'unknown' | 'inactive' | 'transitioning' | 'active' | 'faulted';
    prior_board_epoch: number | null;
    active_board_epoch: number | null;
    transition_phase: string;
    transition_evidence: Record<string, unknown>;
    member_motors: Record<string, number>;
    state_version: number;
    updated_at: number;
}

export interface BioXpOperatorQueueItemV2 {
    command_id: string;
    sequence: number;
    status: Extract<BioXpOperatorReceiptV2Status, 'queued' | 'dispatched' | 'issued_pending' | 'interrupting'>;
    method_id: string | null;
    resource_keys: string[];
    accepted_at: number;
}

export interface BioXpOperatorCommandQueueV2 {
    schema_version: 'bioxp.oem_command_queue.v1';
    generated_at: number;
    items: BioXpOperatorQueueItemV2[];
}

export interface BioXpOperatorDashboardV2 {
    schema_version: 'bioxp.operator_dashboard.v2';
    generated_at: number;
    ownership_generation: number;
    board4: BioXpBoard4AuthorityV2;
    y_axis: BioXpYAxisV2;
    active_commands: BioXpOperatorReceiptV2[];
    command_queue: BioXpOperatorCommandQueueV2;
    latest_receipts: BioXpOperatorReceiptV2[];
}

export interface BioXpOperatorControlCatalogV2 {
    schema_version: 'bioxp.operator_control_catalog.v2';
    dashboard: BioXpOperatorDashboardV2;
    actions: Array<{
        action_id: string;
        request_schema_version: 'bioxp.operator_action_request.v2' | 'bioxp.operator_interrupt_request.v1';
        response_schema_version: 'bioxp.operator_action_receipt.v2' | 'bioxp.operator_interrupt_receipt.v1';
        interrupt: boolean;
        enabled: boolean;
        disabled_reason: string | null;
    }>;
}

export type BioXpOperatorDashboardWire = BioXpOperatorDashboard | BioXpOperatorDashboardV2;
export type BioXpOperatorControlCatalogWire = BioXpOperatorControlCatalog | BioXpOperatorControlCatalogV2;

export type BioXpOperatorMethodV1Status =
    | 'queued'
    | 'active'
    | 'pause_requested'
    | 'paused'
    | 'cancel_requested'
    | 'stopping'
    | 'aborting'
    | 'completed'
    | 'completed_partial'
    | 'failed'
    | 'cleared'
    | 'interrupted'
    | 'ambiguous';

export interface BioXpOperatorMethodV1 {
    schema_version: 'bioxp.operator_method.v1';
    method_id: string;
    action_id: 'oem.xy.move_absolute' | 'oem.xy.home';
    status: BioXpOperatorMethodV1Status;
    state_version: number;
    child_receipts: BioXpOperatorReceiptV2[];
    accepted_at: number;
    finished_at: number | null;
}

interface BioXpOperatorMethodV1Envelope {
    expected_connection_generation: number;
    schema_version: 'bioxp.operator_method_request.v1';
    idempotency_key: string;
    expected_ownership_generation: number;
    expected_board_epoch_by_board: Record<string, number>;
}

export type BioXpOperatorMethodV1Request =
    | (BioXpOperatorMethodV1Envelope & { method_action_id: 'oem.xy.move_absolute'; inputs: { x_steps: number; y_steps: number } })
    | (BioXpOperatorMethodV1Envelope & { method_action_id: 'oem.xy.home'; inputs: Record<string, never> });

export function assertBioXpOperatorMethodV1Request(request: BioXpOperatorMethodV1Request): void {
    assertCanonicalBoardEpochMap(request.expected_board_epoch_by_board);
    if (request.method_action_id === 'oem.xy.move_absolute'
        && (!Number.isSafeInteger(request.inputs.x_steps)
            || !Number.isSafeInteger(request.inputs.y_steps)
            || request.inputs.x_steps < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.x_steps > BIOXP_Y_ABSOLUTE_MAX_STEPS
            || request.inputs.y_steps < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.y_steps > BIOXP_Y_ABSOLUTE_MAX_STEPS)) {
        throw new Error('XY method positions must fit signed int32');
    }
}
export interface BioXpPipetteHardwareEvidence {
    ok: boolean;
    hardware_truth_level?: 'hardware_query' | 'unparsed_hardware_reply' | 'no_readback' | null;
    reply_received?: boolean | null;
    semantic_ok?: boolean | null;
    tip_loaded?: boolean | null;
    pressure?: number | null;
    error?: string | null;
    [key: string]: unknown;
}

export interface BioXpPipetteReceipt {
    schema: 'bioxp.pipette.receipt.v1';
    receipt_id: string;
    created_at: string;
    operation: string;
    truth: {
        delivery_verified: boolean;
        controller_acknowledged: boolean;
        completion_verified: boolean;
        hardware_precondition_verified: boolean;
        hardware_postcondition_verified: boolean;
        physical_effect_verified: false;
        physical_effect_claim_suppressed: true;
    };
}

export interface BioXpPipetteChannel {
    channel: 0 | 1 | 2 | 3;
    pipette_id: 0 | 1 | 2 | 3;
    available: boolean;
    initialized: boolean;
    software_initialized: boolean;
    tip_loaded: boolean;
    software_tip_loaded: boolean;
    hardware_truth_level: string;
    hardware_tip_status: BioXpPipetteHardwareEvidence | null;
    hardware_pressure: BioXpPipetteHardwareEvidence | null;
    oem_diagnosis: string | null;
    oem_error_queue: number[];
    liquid_level_ul: number;
    front_air_level_ul: number;
    rear_air_level_ul: number;
    last_command: string | null;
}

export interface BioXpPipettes {
    ok: boolean;
    transport: 'novo_usb_can';
    channels: BioXpPipetteChannel[];
    channel_count: 4;
    live_query_performed: false;
    liquid_mutation_enabled: boolean;
    allow_to_stop: boolean;
    last_error: { channel: 0 | 1 | 2 | 3; error_code: number; source: 'ClassPipetteCollection.handlePipetteMessage' } | null;
    last_group_transaction: Record<string, unknown> | null;
    latest_receipt?: BioXpPipetteReceipt | null;
    application?: BioXpPipetteApplicationStatus | null;
    physical_effect_verified: false;
}

export interface BioXpPipetteReadbackRequest {
    include_data?: boolean;
}

export interface BioXpPipetteReadbackChannel {
    channel: 0 | 1 | 2 | 3;
    semantic_ok: boolean;
    firmware: Record<string, unknown>;
    status: Record<string, unknown>;
    tip: Record<string, unknown>;
    pressure: Record<string, unknown> | null;
    data: Record<string, unknown> | null;
}

export interface BioXpPipetteReadback {
    ok: boolean;
    semantic_ok: boolean;
    available: boolean;
    channel_count: 4;
    channels_constructed_unconditionally: [0, 1, 2, 3];
    channels: [BioXpPipetteReadbackChannel, BioXpPipetteReadbackChannel, BioXpPipetteReadbackChannel, BioXpPipetteReadbackChannel];
    include_data: boolean;
    live_query_performed: true;
    truth_source: 'live_hardware_queries';
    delivery_verified: false;
    controller_acknowledged: false;
    completion_verified: false;
    hardware_postcondition_verified: false;
    physical_effect_verified: false;
    oem_source_anchor: 'ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData';
    receipt_id: string;
    receipt_truth: BioXpPipetteReceipt['truth'];
}

export type BioXpPipetteApplicationOperation = 'load_tip' | 'move_to_waste' | 'detect_fluid' | 'plunger_up' | 'plunger_down';
export type BioXpPipetteApplicationDependencyName = 'deck' | 'gantry' | 'z' | 'pressure' | 'pipette' | 'machine_state';

export interface BioXpPipetteApplicationDependency {
    bound: boolean;
    authority: string | null;
    generation: number;
    state: Record<string, unknown>;
    blockers: string[];
}

export interface BioXpPipetteApplicationStatus {
    ok: boolean;
    mode: 'plan_only';
    execution_admitted: false;
    physical_effect_verified: false;
    operations: BioXpPipetteApplicationOperation[];
    dependencies: Record<BioXpPipetteApplicationDependencyName, BioXpPipetteApplicationDependency>;
    required_dependencies: BioXpPipetteApplicationDependencyName[];
    missing_dependencies: BioXpPipetteApplicationDependencyName[];
    dependency_blockers: string[];
    dependencies_satisfied: boolean;
    blocker: 'physical_pipette_execution_not_authorized';
}

export type BioXpPipetteApplicationPlanRequest =
    | {
        operation: 'load_tip';
        tip_tray: string;
        tip_well: string;
        tip_type: number;
        tip_location: 0 | 1 | 2 | 3;
        home_z_after?: boolean;
    }
    | { operation: 'move_to_waste' }
    | { operation: 'detect_fluid'; fluid_class: 'TC' | 'MS' | 'OC' | 'RC' | 'STRIP' }
    | { operation: 'plunger_up' }
    | { operation: 'plunger_down' };

export interface BioXpPipetteApplicationPlan {
    ok: boolean;
    operation: BioXpPipetteApplicationOperation;
    mode: 'plan_only';
    execution_admitted: false;
    motion_commanded: false;
    liquid_mutation_commanded: false;
    controller_acknowledged: false;
    completion_verified: false;
    physical_effect_verified: false;
    state_reconciled: false;
    requested_inputs: Record<string, unknown>;
    effective_inputs: null;
    steps: Array<Record<string, unknown> & { owner: BioXpPipetteApplicationDependencyName }>;
    dependencies: Partial<Record<BioXpPipetteApplicationDependencyName, BioXpPipetteApplicationDependency>>;
    required_dependencies: BioXpPipetteApplicationDependencyName[];
    missing_dependencies: BioXpPipetteApplicationDependencyName[];
    dependency_blockers: string[];
    dependencies_satisfied: boolean;
    required_completion_evidence: string[];
    constants: Record<string, unknown>;
    oem_source_anchor: string;
    blocker: 'physical_pipette_execution_not_authorized' | 'application_dependencies_unbound';
    receipt_id: string;
    receipt_truth: BioXpPipetteReceipt['truth'];
}

export interface BioXpOperatorAdmission {
    action_id: string;
    ownership_generation: number;
    enabled: boolean;
    disabled_reason: string | null;
    dependencies: BioXpOperatorDependency[];
}

export interface BioXpOperatorInputSpec {
    name: string;
    wire_name: string | null;
    label: string;
    value_type: 'string' | 'integer' | 'number' | 'boolean' | 'enum' | 'json';
    location: 'path' | 'query' | 'body';
    required: boolean;
    description: string;
    unit: string | null;
    enum_values: string[];
    minimum: number | null;
    maximum: number | null;
    exclusive_minimum: number | null;
    exclusive_maximum: number | null;
    default: unknown;
}

export interface BioXpOperatorActionSpec {
    action_id: string;
    label: string;
    subsystem: string;
    category: string;
    kind: BioXpOperatorActionKind;
    safety_class: BioXpOperatorSafetyClass;
    description: string;
    source_anchor: string | null;
    informational_method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    informational_path: string;
    provider_available: boolean;
    provider_unavailable_reason: string | null;
    available: boolean;
    unavailable_reason: string | null;
    enabled: boolean;
    disabled_reason: string | null;
    dependencies: BioXpOperatorDependency[];
    requires_confirmation: boolean;
    timeout_seconds: number;
    required_provider_capability: string | null;
    inputs: BioXpOperatorInputSpec[];
    stages: string[];
    aggregate_abort?: true;
    physical_scope?: 'aggregate_oem_all_present_boards';
    x_only?: false;
}

export interface BioXpOperatorControlCatalog {
    schema_name: 'bioxp.operator_control_catalog';
    schema_version: 'bioxp.operator_control_catalog.v1';
    machine_serial: string;
    ownership_generation: number;
    registry_sha256: string;
    evidence_lock_sha256: string;
    source_authority_verified: boolean;
    dashboard: BioXpOperatorDashboard;
    actions: BioXpOperatorActionSpec[];
}

export interface BioXpOperatorActionReceipt {
    schema_version: 'bioxp.operator_action_receipt.v1';
    command_id: string;
    action_id: string;
    kind: BioXpOperatorActionKind;
    safety_class: BioXpOperatorSafetyClass;
    status: 'acknowledged' | 'admission_pending' | 'queued' | 'completed' | 'failed' | 'blocked' | 'rejected' | 'reconciliation_required';
    idempotency_key: string;
    idempotency_replay_enabled: boolean;
    ownership_generation: number;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
    request_received_at: number | null;
    lock_acquired_at: number | null;
    admission_completed_at: number | null;
    provider_entry_at: number | null;
    provider_returned_at: number | null;
    receipt_persist_started_at: number | null;
    remote_acknowledged: boolean;
    controller_acknowledged: boolean;
    controller_terminal_state_verified: boolean;
    physical_effect_verified: boolean;
    automatic_retry: boolean | null;
    physical_outcome: string | null;
    persistence_fallback: Record<string, unknown> | null;
    machine_assessment: 'pass' | 'fail' | 'unverified';
    operator_assessment: 'pass' | 'fail' | null;
    operator_note: string | null;
    operator_assessment_idempotency_key: string | null;
    operator_assessed_at: number | null;
    inputs: Record<string, unknown>;
    requested_inputs: Record<string, unknown> | null;
    response: Record<string, unknown> | null;
    authority_receipt_id: string | null;
    authority_receipt_status: BioXpOperatorActionReceipt['status'] | { omitted: string } | null;
    authority_fingerprint: string | null;
    observation_receipt_id: string | null;
    observes_command_id: string | null;
    error: string | null;
    stage_receipts: Record<string, unknown>[];
}

export interface BioXpOperatorActionHistory {
    schema_version: 'bioxp.operator_action_history.v1';
    receipts: BioXpOperatorActionReceipt[];
}

export function bioXpOperatorGenerationPayload(
    connectionGeneration: number,
    ownershipGeneration: number,
) {
    if (!Number.isSafeInteger(connectionGeneration) || connectionGeneration <= 0) {
        throw new Error('A positive safe BMS connection generation is required');
    }
    if (!Number.isSafeInteger(ownershipGeneration) || ownershipGeneration <= 0) {
        throw new Error('A positive safe robot ownership generation is required');
    }
    return {
        expected_connection_generation: connectionGeneration,
        expected_ownership_generation: ownershipGeneration,
    };
}


export interface BioXpProfileView {
    configured: boolean;
    valid: boolean;
    display_name: string | null;
    target_url: string | null;
    freshness_budget_seconds: number | null;
    detail?: string;
}

export interface BioXpProfileWrite {
    schema_version?: 1;
    display_name: string;
    api_url: string;
    freshness_budget_seconds?: number | null;
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
const operatorCatalogKey = ['bioxp', 'operator-controls', 'catalog'] as const;
const operatorDashboardKey = ['bioxp', 'operator-controls', 'dashboard'] as const;
const operatorHistoryKey = ['bioxp', 'operator-controls', 'history'] as const;
const operatorV2DashboardKey = ['bioxp', 'operator-controls', 'v2', 'dashboard'] as const;
const operatorV2CatalogKey = ['bioxp', 'operator-controls', 'v2', 'catalog'] as const;

export interface BioXpOperatorReportFilters {
    status?: string;
    operation?: string;
    action?: string;
    event_kind?: string;
    channel?: number;
    entrypoint?: string;
    caller_class?: string;
    control_class?: string;
    protocol_job_id?: string;
    protocol_action_id?: string;
    lifecycle_stage_id?: string;
    outcome?: string;
    event_source?: string;
    pressure_stream_id?: string;
    delivery_verified?: boolean;
    controller_acknowledged?: boolean;
    completion_verified?: boolean;
    hardware_postcondition_verified?: boolean;
    physical_effect_verified?: boolean;
    evidence_state?: string;
    command_id?: string;
    pipette_operation_id?: string;
    connection_generation?: number;
    ownership_generation?: number;
    start?: number;
    end?: number;
}

export interface BioXpOperatorReportSnapshot {
    database_incarnation_id?: string;
    schema_identity?: Record<string, unknown>;
    release_identity?: Record<string, unknown>;
    source_high_waters?: Record<string, number>;
    high_water_sequence?: number;
    high_water_rowid?: number;
}

export interface BioXpOperatorReportSummary {
    scope?: string;
    filters?: BioXpOperatorReportFilters;
    snapshot?: BioXpOperatorReportSnapshot;
    commands?: { total?: number; by_status?: Record<string, number> };
    pipette_operations?: { total?: number; by_status?: Record<string, number> };
    runtime_events?: { total?: number; by_kind?: Record<string, number> };
    pressure?: { streams?: number; chunks?: number };
    rates?: { delivery_rate?: number; ack_rate?: number; completion_rate?: number; postcondition_rate?: number; physical_effect_rate?: number; failure_rate?: number };
    latency?: { average_ms?: number; maximum_ms?: number };
    errors?: { by_code?: Record<string, number> };
}

export interface BioXpOperatorReportCommandRow {
    sequence?: number;
    command_id?: string;
    idempotency_key?: string;
    operation?: string;
    command_kind?: string;
    entrypoint_id?: string;
    caller_class?: string;
    control_class?: string;
    action_id?: string;
    status?: string;
    outcome?: string | null;
    failure_code?: string | null;
    started_at?: number | string | null;
    finished_at?: number | string | null;
    duration_ms?: number | null;
    delivery_verified?: boolean;
    controller_acknowledged?: boolean;
    completion_verified?: boolean;
    semantic_query_response_verified?: boolean;
    physical_effect_verified?: boolean;
    evidence_state?: string | null;
}

export interface BioXpOperatorReportCommands {
    filters?: BioXpOperatorReportFilters;
    snapshot?: BioXpOperatorReportSnapshot;
    returned_count?: number;
    filtered_total?: number;
    commands?: BioXpOperatorReportCommandRow[];
    has_more?: boolean;
    next_cursor?: string | null;
}

export interface BioXpOperatorReportTransition {
    transition_id?: number;
    state?: string;
    observed_at?: number | string | null;
    detail?: unknown;
}

export interface BioXpOperatorReportPipette {
    pipette_operation_id?: string;
    command_id?: string;
    operation?: string;
    status?: string;
    outcome?: string | null;
    failure_code?: string | null;
    delivery_verified?: boolean;
    controller_acknowledged?: boolean;
    completion_verified?: boolean;
    semantic_query_response_verified?: boolean;
    physical_effect_verified?: boolean;
    evidence_state?: string | null;
    channels?: Array<Record<string, unknown>>;
    exchanges?: Array<Record<string, unknown>>;
    events?: Array<Record<string, unknown>>;
    pressure_streams?: Array<Record<string, unknown>>;
}

export interface BioXpOperatorReportPipettePage {
    filters?: BioXpOperatorReportFilters;
    snapshot?: BioXpOperatorReportSnapshot;
    returned_count?: number;
    filtered_total?: number;
    has_more?: boolean;
    next_cursor?: string | null;
    pipette?: BioXpOperatorReportPipette[];
}

export interface BioXpOperatorReportCommandDetail extends BioXpOperatorReportCommandRow {
    requested_inputs?: unknown;
    effective_inputs?: unknown;
    source_identity?: unknown;
    transitions?: BioXpOperatorReportTransition[];
    evidence?: Array<Record<string, unknown>>;
    pipette?: BioXpOperatorReportPipette | null;
}

export interface BioXpOperatorReportEvents {
    returned_count?: number;
    events?: Array<Record<string, unknown>>;
}

export interface BioXpOperatorReportPressureStreams {
    returned_count?: number;
    pressure_streams?: Array<Record<string, unknown>>;
}

export interface BioXpOperatorReportExport {
    export_id: string;
    evidence_artifact_id: string;
    status: string;
    format: 'json' | 'csv';
    row_count: number;
    sha256: string;
    byte_count: number;
    release_identity: Record<string, unknown>;
    download: string;
}

export interface BioXpOperatorReportExportList {
    items: Array<{
        export_id: string;
        format: string;
        row_count: number;
        sha256: string;
        byte_count: number;
        status: string;
        created_at: number | string;
        publication_state: string;
        evidence_state: string;
        legal_hold: boolean;
        evidence_available: boolean;
        download: string | null;
    }>;
    returned_count: number;
    limit: number;
}

const operatorReportSummaryKey = ['bioxp', 'operator-reports', 'summary'] as const;
const operatorReportCommandsKey = ['bioxp', 'operator-reports', 'commands'] as const;
const operatorReportParams = (filters?: BioXpOperatorReportFilters, limit?: number, cursor?: string | null) => ({
    ...(filters ?? {}),
    ...(limit === undefined ? {} : { limit }),
    ...(cursor ? { cursor } : {}),
});

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
const OPERATOR_DETAIL_LIMIT = 2_048;

const TRUNCATED_SUFFIX = '…[truncated]';

function boundedOperatorText(value: string, limit = OPERATOR_DETAIL_LIMIT): string {
    const normalized = value.trim();
    if (normalized.length <= limit) return normalized;
    return `${normalized.slice(0, Math.max(0, limit - TRUNCATED_SUFFIX.length))}${TRUNCATED_SUFFIX}`;
}

function nestedOperatorDetail(value: unknown, depth = 0): string | null {
    if (depth > 8 || value === null || value === undefined) return null;
    if (typeof value === 'string') return boundedOperatorText(value) || null;
    if (Array.isArray(value)) {
        const normalized = value.map((entry) => {
            if (entry && typeof entry === 'object' && 'msg' in entry) {
                const item = entry as { loc?: unknown; msg?: unknown };
                const location = Array.isArray(item.loc) ? item.loc.map(String).join('.') : '';
                const message = typeof item.msg === 'string'
                    ? item.msg
                    : nestedOperatorDetail(item.msg, depth + 1);
                return message ? (location ? `${location}: ${message}` : message) : null;
            }
            return nestedOperatorDetail(entry, depth + 1);
        }).filter((entry): entry is string => Boolean(entry));
        const joined = normalized.length ? normalized.join('; ') : null;
        return joined ? boundedOperatorText(joined) : null;
    }
    if (typeof value !== 'object') return null;
    const record = value as Record<string, unknown>;
    for (const key of ['detail', 'error', 'message', 'reason', 'block_reason', 'startup_error']) {
        if (key in record) {
            const found = nestedOperatorDetail(record[key], depth + 1);
            if (found) return found;
        }
    }
    return null;
}


export interface BioXpErrorPresentation {
    status: number | null;
    summary: string;
    rawJson: string;
}

const OPERATOR_ERROR_BODY_LIMIT = 8_192;

export function bioXpErrorPresentation(error: unknown): BioXpErrorPresentation {
    const response = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { status?: unknown; data?: unknown } }).response
        : undefined;
    const status = typeof response?.status === 'number' && Number.isInteger(response.status)
        ? response.status
        : null;
    const summary = nestedOperatorDetail(
        response?.data && typeof response.data === 'object' && 'detail' in response.data
            ? (response.data as { detail?: unknown }).detail
            : response?.data,
    ) ?? (
        error && typeof error === 'object' && 'message' in error && typeof error.message === 'string'
            ? boundedOperatorText(error.message)
            : String(error ?? 'Unknown error')
    );
    let rawJson: string;
    try {
        rawJson = JSON.stringify(response?.data ?? null, null, 2);
    } catch {
        rawJson = String(response?.data ?? null);
    }
    return {
        status,
        summary,
        rawJson: boundedOperatorText(rawJson, OPERATOR_ERROR_BODY_LIMIT),
    };
}

export function bioXpErrorText(error: unknown): string {
    return bioXpErrorPresentation(error).summary;
}

export const useBioXpStatus = (enabled = true) => useQuery({
    queryKey: statusKey,
    queryFn: async () => (await api.get<BioXpStatusResponse>('/api/bioxp/status')).data,
    enabled,
    refetchInterval: enabled ? 10_000 : false,
    retry: false,
});


export const useBioXpOperatorControlCatalog = (
    connectionGeneration: number,
    enabled = true,
    lifecycleState?: string | null,
) => useQuery({
    queryKey: [...operatorCatalogKey, connectionGeneration, enabled, lifecycleState ?? null],
    queryFn: async () => (
        await api.get<BioXpOperatorControlCatalog>('/api/bioxp/operator-controls/catalog')
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 15_000 : false,
    refetchIntervalInBackground: false,
});

export const useBioXpOperatorDashboard = (connectionGeneration: number, enabled = true) => useQuery({
    queryKey: [...operatorDashboardKey, connectionGeneration, enabled],
    queryFn: async () => (
        await api.get<BioXpOperatorDashboard>('/api/bioxp/operator-controls/dashboard')
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    refetchInterval: enabled && connectionGeneration > 0 ? 15_000 : false,
    refetchIntervalInBackground: false,
    retry: false,
});

export const useBioXpOperatorDashboardV2 = (connectionGeneration: number, enabled = true) => useQuery({
    queryKey: [...operatorV2DashboardKey, connectionGeneration, enabled],
    queryFn: async () => (await api.get<BioXpOperatorDashboardV2>('/api/bioxp/operator-controls/v2/dashboard')).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    staleTime: 2_000,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 1_000 : false,
    refetchIntervalInBackground: false,
});

export const useBioXpOperatorControlCatalogV2 = (
    connectionGeneration: number,
    enabled = true,
    authorityVersion: string | null = null,
) => useQuery({
    queryKey: [...operatorV2CatalogKey, connectionGeneration, enabled, authorityVersion],
    queryFn: async () => (await api.get<BioXpOperatorControlCatalogV2>('/api/bioxp/operator-controls/v2/catalog')).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    staleTime: 2_000,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 1_000 : false,
    refetchIntervalInBackground: false,
});

export const BIOXP_Y_RELATIVE_MIN_STEPS = -(2 ** 31);
export const BIOXP_Y_RELATIVE_MAX_STEPS = 2 ** 31 - 1;
export const BIOXP_Y_ABSOLUTE_MIN_STEPS = -(2 ** 31);
export const BIOXP_Y_ABSOLUTE_MAX_STEPS = 2 ** 31 - 1;

function assertCanonicalBoardEpochMap(value: Record<string, number>): void {
    for (const [key, epoch] of Object.entries(value)) {
        if (!/^(0|[1-9][0-9]*)$/.test(key) || !Number.isSafeInteger(epoch) || epoch < 0) {
            throw new Error('Board epoch keys must be canonical nonnegative decimal board IDs');
        }
    }
}

function assertBioXpOperatorActionV2Request(request: BioXpOperatorActionV2Request): void {
    assertCanonicalBoardEpochMap(request.expected_board_epoch_by_board);
    if ((request.action_id === 'oem.x.move_steps' || request.action_id === 'oem.y.move_steps' || request.action_id === 'oem.z.move_steps')
        && (!Number.isSafeInteger(request.inputs.steps)
            || request.inputs.steps < BIOXP_Y_RELATIVE_MIN_STEPS
            || request.inputs.steps > BIOXP_Y_RELATIVE_MAX_STEPS)) {
        throw new Error('Relative steps must fit signed int32');
    }
    if (request.action_id === 'oem.y.move_absolute'
        && (!Number.isSafeInteger(request.inputs.target_steps)
            || request.inputs.target_steps < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.target_steps > BIOXP_Y_ABSOLUTE_MAX_STEPS)) {
        throw new Error('Y absolute target must fit signed int32');
    }
    if ((request.action_id === 'oem.x.move_absolute' || request.action_id === 'oem.z.move_absolute')
        && (!Number.isSafeInteger(request.inputs.position_steps)
            || request.inputs.position_steps < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.position_steps > BIOXP_Y_ABSOLUTE_MAX_STEPS)) {
        throw new Error('Absolute position must fit signed int32');
    }
    if (request.action_id === 'oem.xy.move_absolute'
        && (!Number.isSafeInteger(request.inputs.x)
            || !Number.isSafeInteger(request.inputs.y)
            || request.inputs.x < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.x > BIOXP_Y_ABSOLUTE_MAX_STEPS
            || request.inputs.y < BIOXP_Y_ABSOLUTE_MIN_STEPS
            || request.inputs.y > BIOXP_Y_ABSOLUTE_MAX_STEPS)) {
        throw new Error('XY absolute positions must fit signed int32');
    }
}

interface BioXpOperatorActionV2Envelope {
    expected_connection_generation: number;
    schema_version: 'bioxp.operator_action_request.v2';
    expected_ownership_generation: number;
    idempotency_key: string;
    expected_board_epoch_by_board: Record<string, number>;
}

export type BioXpOperatorActionV2Request =
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.move_steps' | 'oem.z.move_steps'; inputs: { steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.move_absolute' | 'oem.z.move_absolute'; inputs: { position_steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.manual_panel_home' | 'oem.z.manual_home' | 'oem.z.clear' | 'oem.xy.home'; inputs: Record<string, never> })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.xy.move_absolute'; inputs: { x: number; y: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.move_steps'; inputs: { steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.move_absolute'; inputs: { target_steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.manual_panel_home'; inputs: Record<string, never> });

export interface BioXpOperatorInterruptV1Request {
    expected_connection_generation: number;
    schema_version: 'bioxp.operator_interrupt_request.v1';
    idempotency_key: string;
    reason: string;
    observed_ownership_generation: number | null;
    observed_board_epoch_by_board: Record<string, number>;
}

export interface BioXpOperatorInterruptReceiptV1 {
    schema_version: 'bioxp.operator_interrupt_receipt.v1';
    robot_identity: string;
    ownership_generation: number;
    observed_ownership_generation: number | null;
    observed_board_epoch_by_board: Record<string, number>;
    interrupt_attempt_id: string;
    interrupt_id: string;
    action_id: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.z.abort' | 'oem.abort_all';
    scope: 'x' | 'y' | 'z' | 'aggregate';
    cutoff: number | null;
    active_command_id: string | null;
    active_command_ids: string[];
    global_safety_epoch: number | null;
    x_safety_epoch: number | null;
    y_safety_epoch: number | null;
    z_safety_epoch: number | null;
    oem_abort_latched: boolean;
    controller_stop_attempted: true;
    source_call_completed: boolean;
    source_return_ok: boolean;
    controller_stop_acknowledged: boolean;
    controller_response: unknown;
    controller_response_evidence?: {
        evidence_id: string;
        evidence_kind: 'controller_response';
        content_sha256: string;
        payload_bytes: number;
    } | null;
    error: string | null;
    physical_effect_verified: false;
    persistence_state: 'committed' | 'recovery_required';
    recovery_hold: boolean;
    transition_sequence: number | null;
    terminal_transition_sequences: number[];
    idempotent_replay?: boolean;
}

export const useInvokeBioXpOperatorActionV2 = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ request }: { request: BioXpOperatorActionV2Request }) => {
            assertBioXpOperatorActionV2Request(request);
            const { action_id: actionId, ...body } = request;
            return (
                await api.post<BioXpOperatorReceiptV2>(
                    `/api/bioxp/operator-controls/v2/actions/${encodeURIComponent(actionId)}`,
                    body,
                )
            ).data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: operatorV2DashboardKey });
        },
    });
};

export const useInterruptBioXpOperatorActionV1 = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ actionId, request }: { actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.z.abort' | 'oem.abort_all'; request: BioXpOperatorInterruptV1Request }) => {
            assertCanonicalBoardEpochMap(request.observed_board_epoch_by_board);
            return (
                await api.post<BioXpOperatorInterruptReceiptV1>(
                    `/api/bioxp/operator-controls/v2/interrupts/${encodeURIComponent(actionId)}`,
                    request,
                )
            ).data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: operatorV2DashboardKey });
        },
    });
};

export const BIOXP_V2_PENDING_COMPLETION_CLASS = 'issued_pending' as const;

export const bioXpReceiptV2IsNonTerminal = (receipt: BioXpOperatorReceiptV2 | null | undefined): boolean =>
    receipt !== null && receipt !== undefined && receipt.terminal !== true;

export const useBioXpOperatorReceiptV2 = (
    commandId: string | null,
    connectionGeneration: number,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'v2', 'receipt', commandId, connectionGeneration],
    queryFn: async () => (
        await api.get<BioXpOperatorReceiptDetailV2>(
            `/api/bioxp/operator-controls/v2/receipts/${encodeURIComponent(commandId ?? '')}`,
            { params: { detail: true } },
        )
    ).data,
    enabled: enabled && Boolean(commandId) && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) => {
        if (!query.state.data) return 500;
        return bioXpReceiptV2IsNonTerminal(query.state.data) ? 500 : false;
    },
    refetchIntervalInBackground: false,
});

export const useSubmitBioXpOperatorMethodV1 = () => useMutation({
    mutationFn: async (request: BioXpOperatorMethodV1Request) => {
        assertBioXpOperatorMethodV1Request(request);
        return (
            await api.post<BioXpOperatorMethodV1>('/api/bioxp/operator-controls/v2/methods', request)
        ).data;
    },
});

const BIOXP_METHOD_V1_TERMINAL = new Set<BioXpOperatorMethodV1Status>([
    'completed', 'completed_partial', 'failed', 'cleared', 'interrupted', 'ambiguous',
]);

export const bioXpMethodV1IsTerminal = (
    method: Pick<BioXpOperatorMethodV1, 'status'> | { status?: unknown } | null | undefined,
): boolean => typeof method?.status !== 'string'
    || BIOXP_METHOD_V1_TERMINAL.has(method.status as BioXpOperatorMethodV1Status);

export const useBioXpOperatorMethodV1 = (
    methodId: string | null,
    connectionGeneration: number,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'v2', 'method', methodId, connectionGeneration],
    queryFn: async () => (
        await api.get<BioXpOperatorMethodV1>(
            `/api/bioxp/operator-controls/v2/methods/${encodeURIComponent(methodId ?? '')}`,
        )
    ).data,
    enabled: enabled && Boolean(methodId) && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) => bioXpMethodV1IsTerminal(query.state.data) ? false : 500,
    refetchIntervalInBackground: false,
});

export const useBioXpOperatorCommandV2 = (
    commandId: string | null,
    connectionGeneration: number,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'v2', 'command', commandId, connectionGeneration],
    queryFn: async () => (
        await api.get<BioXpOperatorReceiptDetailV2>(
            `/api/bioxp/operator-controls/v2/commands/${encodeURIComponent(commandId ?? '')}`,
            { params: { detail: true } },
        )
    ).data,
    enabled: enabled && Boolean(commandId) && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) => bioXpReceiptV2IsNonTerminal(query.state.data) ? 500 : false,
    refetchIntervalInBackground: false,
});
export const useReadBioXpPipetteReadback = () => useMutation({
    mutationFn: async (request: BioXpPipetteReadbackRequest) => (
        await api.post<BioXpPipetteReadback>(
            '/api/bioxp/operator-controls/pipettes/readback',
            request,
        )
    ).data,
});

export const useBioXpPipetteApplicationStatus = (connectionGeneration: number, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'pipettes', 'application-status', connectionGeneration, enabled],
    queryFn: async () => (
        await api.get<BioXpPipetteApplicationStatus>('/api/bioxp/operator-controls/pipettes/application/status')
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
});

export const usePlanBioXpPipetteApplication = () => useMutation({
    mutationFn: async (request: BioXpPipetteApplicationPlanRequest) => (
        await api.post<BioXpPipetteApplicationPlan>(
            '/api/bioxp/operator-controls/pipettes/application/plan',
            request,
        )
    ).data,
});

export const useBioXpOperatorActionAdmission = (
    actionId: string | null,
    connectionGeneration: number,
    ownershipGeneration: number,
    inputs: Record<string, unknown> | null,
    enabled = true,
    lifecycleState?: string | null,
) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'admission', actionId, connectionGeneration, ownershipGeneration, inputs, lifecycleState ?? null],
    queryFn: async () => (
        await api.post<BioXpOperatorAdmission>(
            `/api/bioxp/operator-controls/actions/${encodeURIComponent(actionId ?? '')}/admission`,
            {
                ...bioXpOperatorGenerationPayload(connectionGeneration, ownershipGeneration),
                inputs: inputs ?? {},
            },
        )
    ).data,
    enabled: enabled && Boolean(actionId) && connectionGeneration > 0 && ownershipGeneration > 0 && inputs !== null,
    retry: false,
});

export const bioXpReceiptIsNonTerminal = (receipt: { status?: unknown } | null | undefined): boolean =>
    typeof receipt?.status === 'string'
    && receipt.status !== 'completed'
    && receipt.status !== 'failed'
    && receipt.status !== 'rejected'
    && receipt.status !== 'blocked'
    && receipt.status !== 'cleared';

export const useBioXpOperatorActionHistory = (
    connectionGeneration: number,
    enabled = true,
    limit = 100,
) => useQuery({
    queryKey: [...operatorHistoryKey, connectionGeneration, enabled, limit],
    queryFn: async () => (
        await api.get<BioXpOperatorActionHistory>(`/api/bioxp/operator-controls/history?limit=${limit}`)
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) => bioXpReceiptIsNonTerminal(query.state.data?.receipts?.[0] ?? null) ? 400 : false,
});

export const useBioXpOperatorReportSummary = (
    connectionGeneration: number,
    enabled = true,
    filters?: BioXpOperatorReportFilters,
) => useQuery({
    queryKey: [...operatorReportSummaryKey, connectionGeneration, enabled, filters ?? null],
    queryFn: async () => (await api.get<BioXpOperatorReportSummary>('/api/bioxp/operator-controls/reports/summary', {
        params: operatorReportParams(filters),
    })).data,
    enabled: enabled && connectionGeneration > 0,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 15_000 : false,
});

export const useBioXpOperatorReportCommands = (
    connectionGeneration: number,
    enabled = true,
    limit = 25,
    cursor: string | null = null,
    filters?: BioXpOperatorReportFilters,
) => useQuery({
    queryKey: [...operatorReportCommandsKey, connectionGeneration, enabled, limit, cursor, filters ?? null],
    queryFn: async () => (await api.get<BioXpOperatorReportCommands>('/api/bioxp/operator-controls/reports/commands', {
        params: operatorReportParams(filters, limit, cursor),
    })).data,
    enabled: enabled && connectionGeneration > 0,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 15_000 : false,
});

export const useBioXpOperatorReportCommandDetail = (commandId: string | null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'command-detail', commandId],
    queryFn: async () => (await api.get<BioXpOperatorReportCommandDetail>(
        `/api/bioxp/operator-controls/reports/commands/${encodeURIComponent(commandId ?? '')}`,
    )).data,
    enabled: enabled && Boolean(commandId),
    retry: false,
});

export const useBioXpOperatorReportPipette = (
    connectionGeneration: number,
    enabled = true,
    filters?: BioXpOperatorReportFilters,
) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pipette', connectionGeneration, filters ?? null],
    queryFn: async () => (await api.get<BioXpOperatorReportPipettePage>('/api/bioxp/operator-controls/reports/pipette', {
        params: operatorReportParams(filters, 20),
    })).data,
    enabled: enabled && connectionGeneration > 0,
    retry: false,
});

export const useBioXpOperatorReportEvents = (
    connectionGeneration: number,
    enabled = true,
    filters?: BioXpOperatorReportFilters,
) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'events', connectionGeneration, filters ?? null],
    queryFn: async () => (await api.get<BioXpOperatorReportEvents>('/api/bioxp/operator-controls/reports/events', {
        params: operatorReportParams(filters, 20),
    })).data,
    enabled: enabled && connectionGeneration > 0,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 15_000 : false,
});

export const useBioXpOperatorReportPressureStreams = (
    connectionGeneration: number,
    enabled = true,
    filters?: BioXpOperatorReportFilters,
) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pressure', connectionGeneration, filters ?? null],
    queryFn: async () => (await api.get<BioXpOperatorReportPressureStreams>('/api/bioxp/operator-controls/reports/pressure-streams', {
        params: operatorReportParams(filters, 20),
    })).data,
    enabled: enabled && connectionGeneration > 0,
    retry: false,
});

export const useCreateBioXpOperatorReportExport = () => useMutation({
    mutationFn: async ({ format, filters, limit = 1000 }: {
        format: 'json' | 'csv';
        filters?: BioXpOperatorReportFilters;
        limit?: number;
    }) => (await api.post<BioXpOperatorReportExport>('/api/bioxp/operator-controls/reports/exports', {
        format,
        limit,
        ...(filters ?? {}),
    })).data,
});

export const useBioXpOperatorReportExports = (
    connectionGeneration: number,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'exports', connectionGeneration],
    queryFn: async () => (await api.get<BioXpOperatorReportExportList>(
        '/api/bioxp/operator-controls/reports/exports',
        { params: { limit: 20 } },
    )).data,
    enabled: enabled && connectionGeneration > 0,
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
    retry: false,
});

export const useBioXpCameraStreamState = (
    connectionGeneration: number | null,
    enabled = true,
) => useQuery({
    queryKey: ['bioxp', 'camera', 'stream', connectionGeneration],
    queryFn: async () => {
        if (connectionGeneration === null) throw new Error('An active BioXP connection generation is required');
        return (await api.get<BioXpCameraStream>(BIOXP_CAMERA_ENDPOINTS.streamState, {
            params: { expected_generation: connectionGeneration },
        })).data;
    },
    enabled: enabled && connectionGeneration !== null,
    refetchInterval: enabled && connectionGeneration !== null ? 2_000 : false,
    retry: false,
});

export async function startBioXpCameraStream(connectionGeneration: number): Promise<BioXpCameraStream> {
    return (await api.post<BioXpCameraStream>(BIOXP_CAMERA_ENDPOINTS.streamStart, {
        expected_generation: connectionGeneration,
    })).data;
}

export async function stopBioXpCameraStream(connectionGeneration: number): Promise<BioXpCameraStream> {
    return (await api.post<BioXpCameraStream>(BIOXP_CAMERA_ENDPOINTS.streamStop, {
        expected_generation: connectionGeneration,
    })).data;
}

export function buildBioXpCameraMjpegUrl(connectionGeneration: number): string {
    return `${BIOXP_CAMERA_ENDPOINTS.mjpeg}?expected_generation=${encodeURIComponent(String(connectionGeneration))}`;
}

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
        onSuccess: () => {
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: statusKey }),

                queryClient.invalidateQueries({ queryKey: profileKey }),
                queryClient.invalidateQueries({ queryKey: jobsKey }),
                queryClient.invalidateQueries({ queryKey: fullLifecycleContractKey }),
                queryClient.invalidateQueries({ queryKey: operatorCatalogKey }),
                queryClient.invalidateQueries({ queryKey: operatorDashboardKey }),
                queryClient.invalidateQueries({ queryKey: operatorHistoryKey }),
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

export const useUpdateBioXpFreshness = () => useRefreshMutation(
    async (freshnessBudgetSeconds: number | null) => (
        await api.put<BioXpConnectionSnapshot>('/api/bioxp/settings/freshness', {
            freshness_budget_seconds: freshnessBudgetSeconds,
        })
    ).data,
);

export const useRecoverBioXpMotion = () => useRefreshMutation(
    async ({ generation, reason }: { generation: number; reason: string }) => (
        await api.post<Record<string, unknown>>('/api/bioxp/connection/recover-motion-non-homing', {
            expected_generation: generation,
            operator_reason: reason,
        })
    ).data,
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


export const useInvokeBioXpOperatorAction = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ actionId, connectionGeneration, ownershipGeneration, inputs }: {
            actionId: string;
            connectionGeneration: number;
            ownershipGeneration: number;
            inputs: Record<string, unknown>;
        }) => (
            await api.post<BioXpOperatorActionReceipt>(
                `/api/bioxp/operator-controls/actions/${encodeURIComponent(actionId)}`,
                {
                    ...bioXpOperatorGenerationPayload(connectionGeneration, ownershipGeneration),
                    idempotency_key: crypto.randomUUID(),
                    inputs,
                },
            )
        ).data,
        onMutate: async () => {
            await queryClient.cancelQueries({ queryKey: operatorHistoryKey });
        },
        onSuccess: (receipt, variables) => {
            queryClient.setQueryData<BioXpOperatorActionHistory>(
                [...operatorHistoryKey, variables.connectionGeneration, true],
                (current) => ({
                    schema_version: 'bioxp.operator_action_history.v1',
                    receipts: [
                        receipt,
                        ...(current?.receipts ?? []).filter((row) => row.command_id !== receipt.command_id),
                    ].slice(0, 100),
                }),
            );
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: operatorCatalogKey }),
                queryClient.invalidateQueries({ queryKey: operatorDashboardKey }),
                queryClient.invalidateQueries({ queryKey: operatorHistoryKey }),
            ]);
        },
    });
};

export const useAssessBioXpOperatorAction = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ commandId, connectionGeneration, ownershipGeneration, verdict, note }: {
            commandId: string;
            connectionGeneration: number;
            ownershipGeneration: number;
            verdict: 'pass' | 'fail';
            note: string;
        }) => (
            await api.post<BioXpOperatorActionReceipt>(
                `/api/bioxp/operator-controls/receipts/${encodeURIComponent(commandId)}/assessment`,
                {
                    ...bioXpOperatorGenerationPayload(connectionGeneration, ownershipGeneration),
                    idempotency_key: crypto.randomUUID(),
                    verdict,
                    note,
                },
            )
        ).data,
        onMutate: async () => {
            await queryClient.cancelQueries({ queryKey: operatorHistoryKey });
        },
        onSuccess: (receipt, variables) => {
            queryClient.setQueryData<BioXpOperatorActionHistory>(
                [...operatorHistoryKey, variables.connectionGeneration, true],
                (current) => ({
                    schema_version: 'bioxp.operator_action_history.v1',
                    receipts: [
                        receipt,
                        ...(current?.receipts ?? []).filter((row) => row.command_id !== receipt.command_id),
                    ].slice(0, 100),
                }),
            );
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: operatorDashboardKey }),
                queryClient.invalidateQueries({ queryKey: operatorHistoryKey }),
            ]);
        },
    });
};

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
