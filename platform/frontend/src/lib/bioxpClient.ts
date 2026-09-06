import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

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
    | 'rejected'
    | 'stopped'
    | 'aborted'
    | 'cancelled';

export interface BioXpOperatorReceiptFailureDetailV2 {
    provider_failure: string;
    failure: string;
    axis: string;
    board: number;
    motor: number;
    source_return_code: number;
    controller_acknowledged: boolean;
    controller_terminal_state_verified: boolean;
    physical_effect_verified: boolean;
    lifecycle_state: string;
    reference_state: string;
}

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
    error: {
        code: string;
        message: string;
        retryable: boolean;
        detail?: BioXpOperatorReceiptFailureDetailV2 | null;
    } | null;
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
    deck_movement?: {
        target: string;
        target_label: string | null;
        source_branch: string | null;
        controller_completion_verified: boolean | null;
        semantic_state_committed: boolean | null;
        physical_observation_verified: boolean | null;
    } | null;
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

export interface BioXpOperatorActionHistoryV2 {
    schema_version: 'bioxp.operator_action_history.v2';
    items: BioXpOperatorReceiptV2[];
    next_cursor: string | null;
    limit: number;
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
    telemetry: BioXpOperatorDashboard | null;
    deck?: {
        current_location: string | null;
        current_well: string | null;
        position_table_revision: string;
        destination_catalog_revision: string;
        semantic_state_revision: number;
        ownership_generation: number;
        expected_board_epoch_by_board: Record<string, number>;
        destinations: BioXpDeckDestinationV1[];
    } | null;
}

export interface BioXpDeckDestinationV1 {
    key: string;
    label: string;
    aliases: string[];
    branch_kind: 'ordinary' | 'barcode' | 'park';
    camera_offset_supported: boolean;
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
        destination_catalog_revision?: string | null;
        position_table_revision?: string | null;
        required_board_ids?: Array<4 | 5> | null;
        expected_board_epoch_by_board?: Record<string, number> | null;
        destinations?: BioXpDeckDestinationV1[] | null;
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
        semantic_query_response_verified: boolean;
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
    hardware_truth_level: 'hardware_query';
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

export interface BioXpOperatorLegacyReconciliationReceipt {
    action_id: string;
    automatic_retry: false;
    callback_session_id: string | null;
    caller_class: string;
    command_id: string;
    connection_generation: number | null;
    control_class: string;
    duration_ms: number;
    entrypoint_id: string;
    finished_at: string;
    idempotency_key: string;
    idempotency_replay_enabled: boolean;
    lifecycle_stage_id: string | null;
    operation: string;
    ownership_generation: number;
    physical_outcome: 'ambiguous';
    protocol_action_id: string | null;
    protocol_job_id: string | null;
    requested_inputs: Record<string, unknown>;
    response: Record<string, unknown>;
    stage_receipts: Record<string, unknown>[];
    status: 'reconciliation_required';
}

export interface BioXpOperatorLegacyUnindexedPipetteReceipt {
    error: string;
    failure_code: string;
    ok: false;
    outcome: string;
    response: Record<string, unknown>;
    runtime_binding: {
        callback_session_id: string;
        caller_class: string;
        control_class: string;
        entrypoint_id: string;
        lifecycle_stage_id: string;
        owner: string;
        transport_owner_bound: true;
    };
    stage_receipts: Record<string, unknown>[];
}

export type BioXpOperatorHistoryReceipt =
    | BioXpOperatorActionReceipt
    | BioXpPipetteReceipt
    | BioXpOperatorLegacyReconciliationReceipt
    | BioXpOperatorLegacyUnindexedPipetteReceipt;

export interface BioXpOperatorActionHistory {
    schema_version: 'bioxp.operator_action_history.v1';
    receipts: BioXpOperatorHistoryReceipt[];
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
    lifecycle_attempt_id?: string;
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

export type BioXpOperatorReportJsonValue = null | boolean | number | string | BioXpOperatorReportJsonValue[] | BioXpOperatorReportJsonObject;
export interface BioXpOperatorReportJsonObject { [key: string]: BioXpOperatorReportJsonValue }

export interface BioXpOperatorReportListener { host: string | null; port: number | null }
export interface BioXpOperatorReportReleaseSource { commit: string | null; tree: string | null; mode: string | null; manifest_sha256: string | null; aggregate_sha256: string | null }
export interface BioXpOperatorReportReleaseImage { id: string | null; inspection_receipt_sha256: string | null }
export interface BioXpOperatorReportReleaseDeployment { receipt_id: string | null; installed_at: number | string | null; receipt_sha256: string | null }
export interface BioXpOperatorReportReleaseBinding { service_unit: string | null; unit_sha256: string | null; launcher_sha256: string | null; configuration_sha256: string | null; oem_lock_sha256: string | null; udocker_sha256: string | null; udocker_tree_sha256: string | null; declared_listener: BioXpOperatorReportListener | null; observed_listener: BioXpOperatorReportListener | null }
export interface BioXpOperatorReportReleaseIdentity { schema?: string | null; status: string | null; verified: boolean; reason_code: string | null; release_id: string | null; source: BioXpOperatorReportReleaseSource; image: BioXpOperatorReportReleaseImage; deployment: BioXpOperatorReportReleaseDeployment; binding: BioXpOperatorReportReleaseBinding }
export interface BioXpOperatorReportUnavailableReleaseIdentity { status: string; verified: false; reason_code: string }
export interface BioXpOperatorReportSourceHighWaters { operator_commands: number; operator_transitions: number; pipette_operations: number; pipette_channel_observations: number; pipette_transport_exchanges: number; runtime_events: number; pipette_pressure_streams: number; pipette_pressure_chunks: number; runtime_evidence_objects: number; runtime_evidence_links: number | null; runtime_evidence_events: number; operator_plane_command_versions: number; operator_plane_pipette_versions: number; operator_plane_pressure_stream_versions: number; operator_plane_evidence_versions: number }
export interface BioXpOperatorReportSchemaIdentity { database_identity: 'robot_authoritative_sqlite'; schema_version: 5; identity_version: number | null; release_identity: BioXpOperatorReportReleaseIdentity }
export interface BioXpOperatorReportCurrentSnapshot {
    database_incarnation_id: string;
    schema_identity: BioXpOperatorReportSchemaIdentity;
    release_identity: BioXpOperatorReportReleaseIdentity;
    source_high_waters: BioXpOperatorReportSourceHighWaters;
    high_water_sequence?: number;
    high_water_rowid?: number;
    high_water_event_id?: number;
}

export interface BioXpOperatorReportLegacySnapshot {
    database_identity: 'robot_authoritative_sqlite';
    schema_version: 2;
    database_path_exposed: false;
    identity_version: 2;
    high_water_sequence?: number;
    high_water_rowid?: number;
    high_water_event_id?: number;
}

export type BioXpOperatorReportSnapshot = BioXpOperatorReportCurrentSnapshot | BioXpOperatorReportLegacySnapshot;

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
    sequence: number;
    command_id: string;
    idempotency_key: string;
    operation: string | null;
    command_kind: string | null;
    entrypoint_id: string | null;
    caller_class: string | null;
    control_class: string | null;
    action_id: string | null;
    status: string;
    outcome: string | null;
    failure_code: string | null;
    ownership_generation: number | null;
    connection_generation: number | null;
    started_at: number | string | null;
    admitted_at: number | string | null;
    dispatched_at: number | string | null;
    finished_at: number | string | null;
    duration_ms: number | null;
    delivery_verified: boolean;
    controller_acknowledged: boolean;
    completion_verified: boolean;
    semantic_query_response_verified: boolean;
    hardware_precondition_verified: boolean;
    hardware_postcondition_verified: boolean;
    physical_effect_verified: boolean;
    evidence_state: string | null;
}

export interface BioXpOperatorReportCommands { filters: BioXpOperatorReportFilters; snapshot: BioXpOperatorReportSnapshot; returned_count: number; filtered_total: number; commands: BioXpOperatorReportCommandRow[]; has_more: boolean; next_cursor: string | null }
export interface BioXpOperatorReportTransition { transition_id: number; state: string; observed_at: number | string; detail: BioXpOperatorReportJsonObject }
export interface BioXpOperatorReportLegalHoldAssessment { event_id: string; observed_at: number | string; legal_hold_requested: boolean; assessment: BioXpOperatorReportJsonValue; actor: string | null; retained_deadline: number | string | null; legal_hold_projection_updated: boolean }
export interface BioXpOperatorReportEvidence { evidence_artifact_id: string; sha256: string; byte_count: number; created_at: number | string; retention_deadline: number | string | null; legal_hold: boolean; latest_legal_hold_assessment: BioXpOperatorReportLegalHoldAssessment | null; expiry_state: string; expiry_receipt_id: string | null }
export interface BioXpOperatorReportChannel { observation_id: string; command_id: string; pipette_operation_id: string; channel: number; phase: string | null; observed_at: number | string; semantic_validity: string | null; truth_source: string | null; tip_loaded: boolean | null; pressure: number | null; pressure_units: string | null; status: string | number | null; error_code: string | number | null; firmware_class: string | null; detail: BioXpOperatorReportJsonObject }
export interface BioXpOperatorReportExchange { exchange_id: string; transaction_id: string | number | null; channel: number | null; transaction_phase: string | null; command_family: string | null; matcher_name: string | null; tx_id: number | null; expected_rx_id: number | null; observed_rx_id: number | null; tx_bytes: number[]; rx_bytes: number[]; delivery_verified: boolean; semantic_match: boolean; controller_acknowledged: boolean; completion_verified: boolean; completion_before_ack: boolean; sent_at: number | string | null; received_at: number | string | null; ack_at: number | string | null; completion_at: number | string | null }
export interface BioXpOperatorReportEvent { event_id: number; command_id: string | null; pipette_operation_id: string | null; event_source: string; event_kind: string; channel: number | null; observed_at: number | string; event: BioXpOperatorReportJsonObject; snapshot?: BioXpOperatorReportSnapshot | null }
export interface BioXpOperatorReportPressureStream { stream_session_id: string; pipette_operation_id: string | null; channels: number[]; sample_period_ms: number | null; started_at: number | string; stopped_at: number | string | null; source_generation: number | null; reader_generation: number | null; offset_identity: string | null; terminal_state: string | null; loss_count: number | null }
export interface BioXpOperatorReportPressureChunk { chunk_id: string; channel: number; chunk_sequence: number; sample_count: number; lost_sample_count: number; units: string; sha256: string; byte_count: number; evidence_artifact_id: string | null; summary: BioXpOperatorReportJsonObject | null }

export interface BioXpOperatorReportPipette {
    pipette_operation_id: string;
    command_id: string;
    operation: string | null;
    entrypoint_id: string | null;
    caller_class: string | null;
    control_class: string | null;
    action_id: string | null;
    protocol_job_id: string | null;
    protocol_action_id: string | null;
    lifecycle_stage_id: string | null;
    lifecycle_attempt_id: string | null;
    callback_session_id: string | null;
    status: string;
    outcome: string | null;
    failure_code: string | null;
    delivery_verified: boolean;
    controller_acknowledged: boolean;
    completion_verified: boolean;
    semantic_query_response_verified: boolean;
    hardware_postcondition_verified: boolean;
    physical_effect_verified: boolean;
    evidence_state: string | null;
    channels?: BioXpOperatorReportChannel[];
    exchanges?: BioXpOperatorReportExchange[];
    events?: BioXpOperatorReportEvent[];
    pressure_streams?: BioXpOperatorReportPressureStream[];
}

export interface BioXpOperatorReportPipettePage { filters: BioXpOperatorReportFilters; snapshot: BioXpOperatorReportSnapshot; returned_count: number; filtered_total: number; has_more: boolean; next_cursor: string | null; pipette: BioXpOperatorReportPipette[] }
export interface BioXpOperatorReportPageContinuation { returned_count: number; filtered_total: number; has_more: boolean; next_cursor: string | null }
export interface BioXpOperatorReportCommandDetail extends BioXpOperatorReportCommandRow { requested_inputs: BioXpOperatorReportJsonObject; effective_inputs: BioXpOperatorReportJsonObject; source_identity: BioXpOperatorReportJsonObject; transitions: BioXpOperatorReportTransition[]; evidence: BioXpOperatorReportEvidence[]; evidence_preview: BioXpOperatorReportEvidence[]; evidence_continuation: BioXpOperatorReportPageContinuation; pipette: BioXpOperatorReportPipette | null; snapshot: BioXpOperatorReportSnapshot; child_page_limit: number }
export interface BioXpOperatorReportEvents { event_kind: string | null; filters: BioXpOperatorReportFilters; snapshot: BioXpOperatorReportSnapshot; returned_count: number; filtered_total: number; has_more: boolean; next_cursor: string | null; events: BioXpOperatorReportEvent[] }
export interface BioXpOperatorReportPressureStreams { filters: BioXpOperatorReportFilters; snapshot: BioXpOperatorReportSnapshot; returned_count: number; filtered_total: number; has_more: boolean; next_cursor: string | null; pressure_streams: BioXpOperatorReportPressureStream[] }
export interface BioXpOperatorReportTransitionPage extends BioXpOperatorReportPageContinuation { command_id: string; filters: { command_id: string; limit: number }; snapshot: BioXpOperatorReportSnapshot; transitions: BioXpOperatorReportTransition[] }
export interface BioXpOperatorReportEvidencePage extends BioXpOperatorReportPageContinuation { command_id: string; filters: { command_id: string; limit: number }; snapshot: BioXpOperatorReportSnapshot; evidence: BioXpOperatorReportEvidence[] }
export interface BioXpOperatorReportChannelPage extends BioXpOperatorReportPageContinuation { pipette_operation_id: string; filters: { pipette_operation_id: string; limit: number }; snapshot: BioXpOperatorReportSnapshot; channels: BioXpOperatorReportChannel[] }
export interface BioXpOperatorReportExchangePage extends BioXpOperatorReportPageContinuation { pipette_operation_id: string; filters: { pipette_operation_id: string; limit: number }; snapshot: BioXpOperatorReportSnapshot; exchanges: BioXpOperatorReportExchange[] }
export interface BioXpOperatorReportPressureSamplePage extends BioXpOperatorReportPageContinuation { stream_session_id: string; filters: BioXpOperatorReportFilters; snapshot: BioXpOperatorReportSnapshot; samples: BioXpOperatorReportPressureChunk[] }
export interface BioXpOperatorReportEventDetail extends BioXpOperatorReportEvent { snapshot: BioXpOperatorReportSnapshot | null }
export interface BioXpOperatorReportPressureDetail { stream_session_id: string; pipette_operation_id: string | null; channels: number[]; sample_period_ms: number | null; started_at: number | string; stopped_at: number | string | null; terminal_state: string | null; loss_count: number | null; chunks: BioXpOperatorReportPressureChunk[]; child_page_limit: number; snapshot: BioXpOperatorReportSnapshot }

export interface BioXpOperatorReportExport {
    export_id: string;
    evidence_artifact_id: string;
    status: string;
    format: 'json' | 'csv';
    row_count: number;
    sha256: string;
    byte_count: number;
    release_identity: BioXpOperatorReportReleaseIdentity;
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
        release_identity: BioXpOperatorReportReleaseIdentity | BioXpOperatorReportUnavailableReleaseIdentity;
        publication_state: string;
        evidence_state: string;
        legal_hold: boolean;
        evidence_available: boolean;
        download: string | null;
    }>;
    returned_count: number;
    limit: number;
    available: boolean;
    unavailable_reason: string | null;
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
    staleTime: 15_000,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 10_000 : false,
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
    staleTime: 15_000,
    retry: false,
    refetchInterval: enabled && connectionGeneration > 0 ? 10_000 : false,
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

export function assertBioXpOperatorActionV2Request(request: BioXpOperatorActionV2Request): void {
    assertCanonicalBoardEpochMap(request.expected_board_epoch_by_board);
    if (request.action_id === 'oem.deck.move_to_location') {
        if (Object.keys(request.expected_board_epoch_by_board).sort().join(',') !== '4,5') {
            throw new Error('Deck movement requires exact board epochs for boards 4 and 5');
        }
        const keys = Object.keys(request.inputs).sort().join(',');
        if (keys !== 'camera_offset,target'
            || !/^[A-Z0-9][A-Z0-9_]*$/.test(request.inputs.target)
            || typeof request.inputs.camera_offset !== 'boolean') {
            throw new Error('Deck movement inputs must contain target and camera_offset only');
        }
    }
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
    | (BioXpOperatorActionV2Envelope & { action_id: 'meta.activate_motion' | 'meta.recover_motion_non_homing'; inputs: Record<string, never> })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.move_steps' | 'oem.z.move_steps'; inputs: { steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.move_absolute' | 'oem.z.move_absolute'; inputs: { position_steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.x.manual_panel_home' | 'oem.z.manual_home' | 'oem.z.clear' | 'oem.xy.home'; inputs: Record<string, never> })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.xy.move_absolute'; inputs: { x: number; y: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.move_steps'; inputs: { steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.move_absolute'; inputs: { target_steps: number } })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.y.manual_panel_home'; inputs: Record<string, never> })
    | (BioXpOperatorActionV2Envelope & { action_id: 'oem.deck.move_to_location'; inputs: { target: string; camera_offset: boolean } });

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

const useInvokeBioXpOperatorActionV2Mutation = () => {
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
        onSettled: (_receipt, _error, variables) => {
            void queryClient.invalidateQueries({ queryKey: [...operatorHistoryKey, variables.request.expected_connection_generation] });
            void queryClient.invalidateQueries({ queryKey: operatorV2DashboardKey });
            void queryClient.invalidateQueries({ queryKey: operatorV2CatalogKey });
        },
    });
};

export const useInvokeBioXpOperatorActionV2 = () => useInvokeBioXpOperatorActionV2Mutation();

// Deck and generic axis actions intentionally own separate mutation state.
export const useInvokeBioXpDeckActionV2 = () => useInvokeBioXpOperatorActionV2Mutation();

export interface BioXpPostDispatchCommandIdentity {
    commandId: string;
    statusPath: string;
    retryGuidance: 'do_not_resubmit_reconcile_by_command_id';
}

export const bioXpPostDispatchCommandIdentity = (error: unknown): BioXpPostDispatchCommandIdentity | null => {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (detail === null || typeof detail !== 'object' || Array.isArray(detail)) return null;
    const candidate = detail as Record<string, unknown>;
    if (candidate.error !== 'post_dispatch_receipt_validation_failed'
        || typeof candidate.command_id !== 'string'
        || candidate.command_id.length < 1
        || candidate.command_id.length > 160
        || typeof candidate.status_path !== 'string'
        || candidate.status_path.length < 1
        || candidate.status_path.length > 500
        || candidate.retry_guidance !== 'do_not_resubmit_reconcile_by_command_id') {
        return null;
    }
    return {
        commandId: candidate.command_id,
        statusPath: candidate.status_path,
        retryGuidance: candidate.retry_guidance,
    };
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
) => {
    const queryClient = useQueryClient();
    return useQuery({
        queryKey: ['bioxp', 'operator-controls', 'v2', 'method', methodId, connectionGeneration],
        queryFn: async () => {
            const method = (await api.get<BioXpOperatorMethodV1>(
                `/api/bioxp/operator-controls/v2/methods/${encodeURIComponent(methodId ?? '')}`,
            )).data;
            if (method.method_id !== methodId) throw new Error('XY method identity mismatch; outcome remains unresolved');
            if (bioXpMethodV1IsTerminal(method)) {
                void queryClient.invalidateQueries({ queryKey: [...operatorHistoryKey, connectionGeneration] });
                void queryClient.invalidateQueries({ queryKey: [...operatorV2DashboardKey, connectionGeneration] });
                void queryClient.invalidateQueries({ queryKey: [...operatorV2CatalogKey, connectionGeneration] });
            }
            return method;
        },
        enabled: enabled && Boolean(methodId) && connectionGeneration > 0,
        gcTime: 0,
        retry: false,
        refetchInterval: (query) => query.state.data && bioXpMethodV1IsTerminal(query.state.data) ? false : 500,
        refetchIntervalInBackground: false,
    });
};

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
type BioXpDirectLiquidKind = 'readback' | 'application_plan';
type BioXpDirectLiquidRequest = BioXpPipetteReadbackRequest | BioXpPipetteApplicationPlanRequest;
type BioXpDirectLiquidResult = BioXpPipetteReadback | BioXpPipetteApplicationPlan;
export type BioXpDirectLiquidSubmission = Readonly<{
    requestKind: BioXpDirectLiquidKind;
    idempotencyKey: string;
    request: Readonly<BioXpDirectLiquidRequest & { home_z_after?: boolean }>;
    expectedConnectionGeneration: number;
}>;
export interface BioXpDirectLiquidLookup {
    schema: 'bioxp.direct-liquid.lookup.v1';
    request_kind: BioXpDirectLiquidKind;
    idempotency_key: string;
    lookup_state: 'unknown' | 'pending' | 'incomplete' | 'resolved' | 'conflict' | 'unavailable';
    reason: 'identity_not_found' | 'nonterminal' | 'outcome_unresolved' | 'receipt_incomplete' | 'identity_scope_conflict' | 'store_unavailable' | 'stored_binding_invalid' | null;
    retry_forbidden: true;
    live_query_performed: false;
    record: null | {
        command_id: string; pipette_operation_id: string | null; canonical_request_sha256: string;
        operation: string; entrypoint_id: string; caller_class: string; control_class: string; action_id: string;
        command_status: string; pipette_status: string | null; outcome: string | null; failure_code: string | null;
        ownership_generation: number; connection_generation: number | null;
        requested_inputs: BioXpDirectLiquidSubmission['request']; result: BioXpDirectLiquidResult | null;
    };
}

const directLiquidNormalize = (kind: BioXpDirectLiquidKind, request: BioXpDirectLiquidRequest) => (
    kind === 'readback' ? { include_data: false, ...request } : { home_z_after: true, ...request }
);
const directLiquidEqual = (a: object, b: object) => {
    // All expected request maps are flat scalars; never stringify unknown values.
    const expected = Object.entries(b);
    return Object.keys(a).length === expected.length && expected.every(([key, value]) =>
        Object.hasOwn(a, key) && (a as Record<string, unknown>)[key] === value);
};

// Runtime checks for the existing public direct-liquid DTOs only. Private POST
// envelope metadata is neither required nor learned as browser authority.
const directLiquidObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const directLiquidString = (v: unknown, max: number, min = 0): v is string => typeof v === 'string' && v.length >= min && v.length <= max;
const directLiquidStrings = (v: unknown, max: number, min = 0): v is string[] => Array.isArray(v) && v.length >= min && v.length <= max && v.every(x => typeof x === 'string');
const directLiquidKeys = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).every(k => keys.includes(k));
const directLiquidInteger = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);
const directLiquidNullableString = (v: unknown, max: number) => v === null || directLiquidString(v, max, 1);

function directLiquidResultValid(v: unknown, s: BioXpDirectLiquidSubmission): v is BioXpDirectLiquidResult {
    if (!directLiquidObject(v) || !directLiquidString(v.receipt_id, 32, 32) || !/^[0-9a-f]{32}$/.test(v.receipt_id)
        || typeof v.ok !== 'boolean' || !directLiquidObject(v.receipt_truth)) return false;
    const truth = v.receipt_truth;
    const truthBooleans = ['semantic_query_response_verified', 'delivery_verified', 'controller_acknowledged',
        'completion_verified', 'hardware_precondition_verified', 'hardware_postcondition_verified'];
    if (!directLiquidKeys(truth, [...truthBooleans, 'physical_effect_verified', 'physical_effect_claim_suppressed'])
        || !truthBooleans.every(k => typeof truth[k] === 'boolean')
        || truth.physical_effect_verified !== false || truth.physical_effect_claim_suppressed !== true) return false;
    if (s.requestKind === 'readback') {
        return typeof v.semantic_ok === 'boolean' && typeof v.available === 'boolean'
            && typeof v.include_data === 'boolean' && 'include_data' in s.request && v.include_data === s.request.include_data
            && v.hardware_truth_level === 'hardware_query' && v.live_query_performed === true
            && v.truth_source === 'live_hardware_queries' && v.channel_count === 4
            && v.oem_source_anchor === 'ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData'
            && ['delivery_verified', 'controller_acknowledged', 'completion_verified', 'hardware_postcondition_verified', 'physical_effect_verified'].every(k => v[k] === false)
            && Array.isArray(v.channels_constructed_unconditionally) && v.channels_constructed_unconditionally.length === 4
            && v.channels_constructed_unconditionally.every((id, i) => id === i)
            && Array.isArray(v.channels) && v.channels.length === 4 && v.channels.every((c, i) => directLiquidObject(c)
                && directLiquidKeys(c, ['channel', 'semantic_ok', 'firmware', 'status', 'tip', 'pressure', 'data'])
                && c.channel === i && typeof c.semantic_ok === 'boolean'
                && directLiquidObject(c.firmware) && directLiquidObject(c.status) && directLiquidObject(c.tip)
                && (c.pressure === null || directLiquidObject(c.pressure))
                && (v.include_data ? directLiquidObject(c.data) : c.data === null));
    }
    const request = s.request;
    if (!('operation' in request) || v.operation !== request.operation || v.mode !== 'plan_only'
        || !['execution_admitted', 'motion_commanded', 'liquid_mutation_commanded', 'controller_acknowledged',
            'completion_verified', 'physical_effect_verified', 'state_reconciled'].every(k => v[k] === false)
        || !directLiquidObject(v.requested_inputs) || (v.effective_inputs !== undefined && v.effective_inputs !== null)) return false;
    const expected = request.operation === 'load_tip'
        ? { tip_tray: request.tip_tray, tip_well: request.tip_well, tip_type: request.tip_type, tip_location: request.tip_location, home_z_after: request.home_z_after }
        : request.operation === 'detect_fluid' ? { fluid_class: request.fluid_class }
            : request.operation === 'plunger_up' ? { direction: 'up' }
                : request.operation === 'plunger_down' ? { direction: 'down' } : {};
    if (!directLiquidEqual(v.requested_inputs, expected)
        || !Array.isArray(v.steps) || v.steps.length < 1 || v.steps.length > 32
        || !v.steps.every(step => directLiquidObject(step)
            && directLiquidKeys(step, ['action', 'mutates', 'location_id', 'wire_command', 'current', 'owner'])
            && directLiquidString(step.action, 240, 1) && typeof step.mutates === 'boolean'
            && typeof step.owner === 'string' && ['deck', 'gantry', 'z', 'pressure', 'pipette', 'machine_state'].includes(step.owner)
            && ['location_id', 'current'].every(k => step[k] === undefined || step[k] === null || directLiquidInteger(step[k]))
            && (step.wire_command === undefined || step.wire_command === null || directLiquidString(step.wire_command, 120)))
        || !directLiquidObject(v.dependencies) || !directLiquidStrings(v.required_dependencies, 6, 1)
        || !directLiquidStrings(v.missing_dependencies, 6) || !directLiquidStrings(v.dependency_blockers, 64)
        || !directLiquidStrings(v.required_completion_evidence, 32) || !directLiquidObject(v.constants)
        || !directLiquidString(v.oem_source_anchor, 1000, 1)) return false;
    const required = v.required_dependencies;
    if (Object.keys(v.dependencies).length < 1 || Object.keys(v.dependencies).length > 6
        || Object.keys(v.dependencies).some(k => !required.includes(k))
        || required.some(k => !Object.hasOwn(v.dependencies as object, k))
        || v.missing_dependencies.some(k => !required.includes(k))
        || !Object.values(v.dependencies).every(d => directLiquidObject(d)
            && directLiquidKeys(d, ['bound', 'authority', 'generation', 'state', 'blockers'])
            && typeof d.bound === 'boolean' && directLiquidInteger(d.generation) && directLiquidObject(d.state)
            && (d.authority === undefined || d.authority === null || directLiquidString(d.authority, 240))
            && directLiquidStrings(d.blockers, 32))) return false;
    const satisfied = v.missing_dependencies.length === 0 && v.dependency_blockers.length === 0;
    return v.dependencies_satisfied === satisfied && v.ok === satisfied
        && v.blocker === (satisfied ? 'physical_pipette_execution_not_authorized' : 'application_dependencies_unbound');
}

function directLiquidLookupValid(v: unknown, status: number, s: BioXpDirectLiquidSubmission): v is BioXpDirectLiquidLookup {
    if (!directLiquidObject(v) || v.schema !== 'bioxp.direct-liquid.lookup.v1' || v.request_kind !== s.requestKind
        || v.idempotency_key !== s.idempotencyKey || v.live_query_performed !== false || v.retry_forbidden !== true
        || !directLiquidKeys(v, ['schema', 'request_kind', 'idempotency_key', 'lookup_state', 'reason', 'retry_forbidden', 'live_query_performed', 'record'])) return false;
    const reasons: Record<string, unknown[]> = { unknown: ['identity_not_found'], pending: ['nonterminal'],
        incomplete: ['outcome_unresolved', 'receipt_incomplete'], resolved: [null], conflict: ['identity_scope_conflict'],
        unavailable: ['store_unavailable', 'stored_binding_invalid'] };
    if (typeof v.lookup_state !== 'string' || !Object.hasOwn(reasons, v.lookup_state)
        || !reasons[v.lookup_state].includes(v.reason)
        || status !== (v.lookup_state === 'conflict' ? 409 : v.lookup_state === 'unavailable' ? 503 : 200)) return false;
    if (['unknown', 'conflict', 'unavailable'].includes(v.lookup_state)) return v.record === null;
    const r = v.record;
    if (!directLiquidObject(r) || !directLiquidKeys(r, ['command_id', 'pipette_operation_id', 'canonical_request_sha256',
        'operation', 'entrypoint_id', 'caller_class', 'control_class', 'action_id', 'command_status', 'pipette_status',
        'outcome', 'failure_code', 'ownership_generation', 'connection_generation', 'requested_inputs', 'result'])
        || !['command_id', 'operation', 'entrypoint_id', 'caller_class', 'control_class'].every(k => directLiquidString(r[k], 160, 1))
        || !directLiquidString(r.action_id, 240, 1) || !directLiquidString(r.command_status, 120, 1)
        || !directLiquidNullableString(r.pipette_operation_id, 160) || !directLiquidNullableString(r.pipette_status, 120)
        || !directLiquidNullableString(r.outcome, 120) || !directLiquidNullableString(r.failure_code, 240)
        || !directLiquidString(r.canonical_request_sha256, 64, 64) || !/^[0-9a-f]{64}$/.test(r.canonical_request_sha256)
        || !directLiquidInteger(r.ownership_generation) || r.ownership_generation < 0
        || !(r.connection_generation === null || (directLiquidInteger(r.connection_generation) && r.connection_generation >= 0))
        || !directLiquidObject(r.requested_inputs) || !directLiquidEqual(r.requested_inputs, s.request)
        || (r.result !== null && !directLiquidResultValid(r.result, s))) return false;
    const plan = s.requestKind === 'application_plan';
    const operation = plan && 'operation' in s.request ? 'application_plan:' + s.request.operation : 'live_readback';
    if (r.operation !== operation || r.action_id !== 'pipette.' + operation
        || r.entrypoint_id !== (plan ? 'legacy.record' : 'direct.liquid.readback')
        || r.caller_class !== (plan ? 'legacy' : 'direct_api') || r.control_class !== (plan ? 'pipette_state_command' : 'hardware_query')
        || (r.pipette_operation_id === null) !== (r.pipette_status === null)
        || (v.lookup_state !== 'resolved' && r.result !== null)) return false;
    const pending = ['reserved', 'queued', 'admitted', 'dispatched', 'acknowledged', 'executing', 'running', 'blocked'];
    const terminal = ['completed', 'observed', 'failed', 'rejected', 'cleared', 'cancelled'];
    if (v.lookup_state === 'pending' && (!pending.includes(r.command_status) || !pending.includes(String(r.pipette_status)))) return false;
    if (v.lookup_state === 'resolved' && (r.outcome === null || !terminal.includes(r.command_status)
        || r.command_status !== r.pipette_status || (['completed', 'observed'].includes(r.command_status) && r.result === null))) return false;
    return !(v.lookup_state === 'incomplete' && v.reason === 'receipt_incomplete' && r.pipette_operation_id !== null
        && !(terminal.includes(r.command_status) && r.command_status === r.pipette_status));
}

// Mounted owner only: no reload persistence or reconnect reassociation.
function useDirectLiquid<Request extends BioXpDirectLiquidRequest, Result extends BioXpDirectLiquidResult>(
    kind: BioXpDirectLiquidKind, path: string, generation: number, connected: boolean,
) {
    const [submission, setSubmission] = useState<BioXpDirectLiquidSubmission | null>(null);
    const [lookup, setLookup] = useState<BioXpDirectLiquidLookup | null>(null);
    const [data, setData] = useState<Result | undefined>();
    const [recover, setRecover] = useState(false);
    const [identityConflict, setIdentityConflict] = useState(false);
    const detachedOwners = useRef(new WeakSet<BioXpDirectLiquidSubmission>());
    const retainedKeys = useRef(new Set<string>());
    const [retainedHistory, setRetainedHistory] = useState<BioXpDirectLiquidSubmission[]>([]);
    const owner = useRef(submission);
    const context = useRef({ generation, connected });
    context.current = { generation, connected };
    const sent = useRef<BioXpDirectLiquidSubmission | null>(null);
    const learned = useRef<{ command?: string; pipette?: string; digest?: string; receipt?: string }>({});
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    // A disconnect is terminal for this owner, including same-generation reconnect.
    if (submission && (!connected || generation !== submission.expectedConnectionGeneration)) detachedOwners.current.add(submission);
    const detached = Boolean(submission && detachedOwners.current.has(submission));
    const owns = (s: BioXpDirectLiquidSubmission) => mounted.current && owner.current === s
        && context.current.connected && context.current.generation === s.expectedConnectionGeneration && !detachedOwners.current.has(s);
    const mutation = useMutation({
        mutationFn: async (s: BioXpDirectLiquidSubmission) => {
            // BMS POST union stays unchanged; home_z_after is a robot default for non-load plans.
            const body = { ...s.request };
            if ('operation' in body && body.operation !== 'load_tip') delete body.home_z_after;
            const result = (await api.post<unknown>(path, body, {
                headers: { 'Idempotency-Key': s.idempotencyKey },
                params: { expected_connection_generation: s.expectedConnectionGeneration },
            })).data;
            if (!directLiquidResultValid(result, s)) throw new Error('Invalid or mismatched direct-liquid result; reconcile stored evidence only');
            return result as Result;
        },
        retry: false,
        onSuccess: (result, s) => {
            if (!owns(s)) return;
            if (!result.receipt_id || (learned.current.receipt && learned.current.receipt !== result.receipt_id)) {
                setIdentityConflict(true); setData(undefined); setLookup(null); setRecover(true); return;
            }
            learned.current.receipt = result.receipt_id;
            setData(result);
        },
        onError: (_error, s) => { if (owns(s)) setRecover(true); },
    });
    // Publish the immutable owner in a committed render before transport starts.
    useEffect(() => {
        if (submission && sent.current !== submission && !detached) {
            sent.current = submission;
            mutation.mutate(submission);
        }
    }, [submission, detached]);
    const query = useQuery({
        queryKey: ['bioxp', 'direct-liquid-request', submission?.expectedConnectionGeneration, kind, submission?.idempotencyKey],
        queryFn: async () => {
            const s = submission!;
            const response = await api.get<unknown>('/api/bioxp/operator-controls/pipettes/requests', {
                params: { request_kind: s.requestKind, expected_connection_generation: s.expectedConnectionGeneration },
                headers: { 'Idempotency-Key': s.idempotencyKey },
                validateStatus: (status) => status === 200 || status === 409 || status === 503,
            });
            const value = response.data;
            if (!owns(s)) return null;
            if (!directLiquidLookupValid(value, response.status, s)) {
                setIdentityConflict(true); setData(undefined); setLookup(null); return null;
            }
            const r = value.record;
            const prior = learned.current;
            const mismatch = value.schema !== 'bioxp.direct-liquid.lookup.v1' || value.request_kind !== s.requestKind
                || value.idempotency_key !== s.idempotencyKey || value.live_query_performed !== false || value.retry_forbidden !== true
                || (r && (!directLiquidEqual(r.requested_inputs, s.request)
                    || (r.outcome === null ? !['pending', 'incomplete'].includes(value.lookup_state)
                        : typeof r.outcome !== 'string' || r.outcome.length < 1 || r.outcome.length > 120)
                    || (prior.command && prior.command !== r.command_id)
                    || (prior.pipette && prior.pipette !== r.pipette_operation_id)
                    || (prior.digest && prior.digest !== r.canonical_request_sha256)
                    || (prior.receipt && r.result && prior.receipt !== r.result.receipt_id)));
            if (mismatch) { setIdentityConflict(true); setData(undefined); setLookup(null); return null; }
            if (r) learned.current = { command: r.command_id, pipette: r.pipette_operation_id ?? prior.pipette,
                digest: r.canonical_request_sha256, receipt: r.result?.receipt_id ?? prior.receipt };
            setLookup(value);
            setData(value.lookup_state === 'resolved' && r?.result ? r.result as Result : undefined);
            return value;
        },
        enabled: Boolean(submission) && recover && !detached && !identityConflict,
        retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
        refetchInterval: (q) => !detached && !identityConflict && q.state.data?.lookup_state === 'pending' ? 500 : false,
        refetchIntervalInBackground: false,
    });
    const start = (input: Request & { idempotencyKey?: string }, explicitlyNew = false) => {
        if (generation < 1 || !connected) return;
        if (owner.current && !explicitlyNew) return;
        const previousOwner = owner.current;
        const { idempotencyKey, ...body } = input;
        const key = idempotencyKey ?? crypto.randomUUID();
        if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}$/.test(key)) throw new Error('Invalid direct-liquid identity');
        if (retainedKeys.current.has(key)) throw new Error('Direct-liquid identity already retained');
        const request = Object.freeze(JSON.parse(JSON.stringify(directLiquidNormalize(kind, body as Request)))) as BioXpDirectLiquidSubmission['request'];
        const next = Object.freeze({ requestKind: kind, idempotencyKey: key, request, expectedConnectionGeneration: generation });
        retainedKeys.current.add(key);
        if (previousOwner) setRetainedHistory((previous) => [...previous, previousOwner]);
        owner.current = next; learned.current = {}; setLookup(null); setData(undefined);
        setRecover(false); setIdentityConflict(false); mutation.reset(); setSubmission(next);
    };
    return { isPending: mutation.isPending, error: mutation.error, data: detached || identityConflict ? undefined : data,
        mutate: (input: Request & { idempotencyKey?: string }) => start(input),
        newOperation: (input: Request & { idempotencyKey?: string }) => start(input, true),
        submission, retainedHistory, lookup, detached, identityConflict,
        recoveryError: query.error,
        refreshRecovery: async () => { if (submission && !detached && !identityConflict) await query.refetch({ cancelRefetch: false }); },
    };
}

export const useReadBioXpPipetteReadback = (generation = 0, connected = true) =>
    useDirectLiquid<BioXpPipetteReadbackRequest, BioXpPipetteReadback>('readback', '/api/bioxp/operator-controls/pipettes/readback', generation, connected);

export const useBioXpPipetteApplicationStatus = (connectionGeneration: number, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-controls', 'pipettes', 'application-status', connectionGeneration, enabled],
    queryFn: async () => (
        await api.get<BioXpPipetteApplicationStatus>('/api/bioxp/operator-controls/pipettes/application/status')
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
});

export const usePlanBioXpPipetteApplication = (generation = 0, connected = true) =>
    useDirectLiquid<BioXpPipetteApplicationPlanRequest, BioXpPipetteApplicationPlan>('application_plan', '/api/bioxp/operator-controls/pipettes/application/plan', generation, connected);

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

export const bioXpReceiptIsNonTerminal = (receipt: unknown): boolean => {
    if (!receipt || typeof receipt !== 'object' || !('status' in receipt)) return false;
    const status = receipt.status;
    return typeof status === 'string'
        && status !== 'completed'
        && status !== 'failed'
        && status !== 'rejected'
        && status !== 'blocked'
        && status !== 'cleared';
};

export const useBioXpOperatorActionHistory = (
    connectionGeneration: number,
    enabled = true,
    limit = 100,
) => useQuery({
    queryKey: [...operatorHistoryKey, connectionGeneration, limit],
    queryFn: async () => (
        await api.get<BioXpOperatorActionHistory>(`/api/bioxp/operator-controls/history?limit=${limit}`)
    ).data,
    enabled: enabled && connectionGeneration > 0,
    gcTime: 0,
    retry: false,
    refetchInterval: (query) => query.state.data?.receipts.some(bioXpReceiptIsNonTerminal) ? 1000 : false,
    refetchIntervalInBackground: false,
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

export const useBioXpOperatorReportCommandTransitions = (commandId: string | null, cursor: string | null = null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'command-transitions', commandId, cursor],
    queryFn: async () => (await api.get<BioXpOperatorReportTransitionPage>(
        `/api/bioxp/operator-controls/reports/commands/${encodeURIComponent(commandId ?? '')}/transitions`,
        { params: operatorReportParams(undefined, 50, cursor) },
    )).data,
    enabled: enabled && Boolean(commandId),
    retry: false,
});

export const useBioXpOperatorReportCommandEvidence = (commandId: string | null, cursor: string | null = null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'command-evidence', commandId, cursor],
    queryFn: async () => (await api.get<BioXpOperatorReportEvidencePage>(
        `/api/bioxp/operator-controls/reports/commands/${encodeURIComponent(commandId ?? '')}/evidence`,
        { params: operatorReportParams(undefined, 50, cursor) },
    )).data,
    enabled: enabled && Boolean(commandId),
    retry: false,
});

export const useBioXpOperatorReportPipetteDetail = (pipetteOperationId: string | null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pipette-detail', pipetteOperationId],
    queryFn: async () => (await api.get<BioXpOperatorReportPipette>(
        `/api/bioxp/operator-controls/reports/pipette/${encodeURIComponent(pipetteOperationId ?? '')}`,
    )).data,
    enabled: enabled && Boolean(pipetteOperationId),
    retry: false,
});

export const useBioXpOperatorReportPipetteChannels = (pipetteOperationId: string | null, cursor: string | null = null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pipette-channels', pipetteOperationId, cursor],
    queryFn: async () => (await api.get<BioXpOperatorReportChannelPage>(
        `/api/bioxp/operator-controls/reports/pipette/${encodeURIComponent(pipetteOperationId ?? '')}/channels`,
        { params: operatorReportParams(undefined, 50, cursor) },
    )).data,
    enabled: enabled && Boolean(pipetteOperationId),
    retry: false,
});

export const useBioXpOperatorReportPipetteExchanges = (pipetteOperationId: string | null, cursor: string | null = null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pipette-exchanges', pipetteOperationId, cursor],
    queryFn: async () => (await api.get<BioXpOperatorReportExchangePage>(
        `/api/bioxp/operator-controls/reports/pipette/${encodeURIComponent(pipetteOperationId ?? '')}/exchanges`,
        { params: operatorReportParams(undefined, 50, cursor) },
    )).data,
    enabled: enabled && Boolean(pipetteOperationId),
    retry: false,
});

export const useBioXpOperatorReportEventDetail = (eventId: number | null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'event-detail', eventId],
    queryFn: async () => (await api.get<BioXpOperatorReportEventDetail>(
        `/api/bioxp/operator-controls/reports/events/${encodeURIComponent(String(eventId ?? ''))}`,
    )).data,
    enabled: enabled && eventId !== null,
    retry: false,
});

export const useBioXpOperatorReportPressureDetail = (streamSessionId: string | null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pressure-detail', streamSessionId],
    queryFn: async () => (await api.get<BioXpOperatorReportPressureDetail>(
        `/api/bioxp/operator-controls/reports/pressure-streams/${encodeURIComponent(streamSessionId ?? '')}`,
    )).data,
    enabled: enabled && Boolean(streamSessionId),
    retry: false,
});

export const useBioXpOperatorReportPressureSamples = (streamSessionId: string | null, cursor: string | null = null, enabled = true) => useQuery({
    queryKey: ['bioxp', 'operator-reports', 'pressure-samples', streamSessionId, cursor],
    queryFn: async () => (await api.get<BioXpOperatorReportPressureSamplePage>(
        `/api/bioxp/operator-controls/reports/pressure-streams/${encodeURIComponent(streamSessionId ?? '')}/samples`,
        { params: operatorReportParams(undefined, 50, cursor) },
    )).data,
    enabled: enabled && Boolean(streamSessionId),
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


const updateBioXpHistoryCaches = (queryClient: QueryClient, generation: number, receipt: BioXpOperatorActionReceipt) => {
    for (const query of queryClient.getQueryCache().findAll({ queryKey: [...operatorHistoryKey, generation] })) {
        const limit = query.queryKey[operatorHistoryKey.length + 1];
        if (typeof limit !== 'number') continue;
        queryClient.setQueryData<BioXpOperatorActionHistory>(query.queryKey, (current) => ({
            schema_version: 'bioxp.operator_action_history.v1',
            receipts: [receipt, ...(current?.receipts ?? []).filter((row) => !('command_id' in row) || row.command_id !== receipt.command_id)].slice(0, limit),
        }));
    }
};

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
            updateBioXpHistoryCaches(queryClient, variables.connectionGeneration, receipt);
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: operatorCatalogKey }),
                queryClient.invalidateQueries({ queryKey: operatorDashboardKey }),
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
            updateBioXpHistoryCaches(queryClient, variables.connectionGeneration, receipt);
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: operatorDashboardKey }),
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
