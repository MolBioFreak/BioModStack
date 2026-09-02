import React, { act } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
    stableReset: vi.fn(),
    admissionCalls: 0,
    v1DashboardEnabled: null as boolean | null,
    v1CatalogEnabled: null as boolean | null,
    invokeCalls: [] as Array<Record<string, unknown>>,
    invokePending: false,
    catalog: {
        data: {
            machine_serial: '206',
            ownership_generation: 1,
            source_authority_verified: true,
            dashboard: {
                pipettes: [],
                snapshot: { freshness: null },
            },
            actions: [] as Array<Record<string, unknown>>,
        },
        error: null,
    },
    dashboard: {
        data: {
            motion: { enabled: true, reason: null },
            x_axis: {
                axis: 'x',
                status: {
                    axis: 'x',
                    reference: 'referenced',
                    position_steps: 0,
                    speed_steps_s: 0,
                    left_switch_state: 1,
                    right_switch_state: 1,
                    left_switch_disabled: false,
                    right_switch_disabled: false,
                    coordinate_contract: 'serial206_x_source_0_90263_effective_min_60_relative_margin_20',
                    min_steps: 0,
                    max_steps: 90263,
                    physical_position_verified: true,
                },
                provider: {
                    lifecycle: {
                        schema_version: 'bioxp.serial206_x_lifecycle.v2',
                        state: 'referenced_ready',
                        generation: 1,
                        board_lifecycle_generation: 1,
                        reference_state: 'referenced',
                        prepared_receipt: null,
                        active_receipt: null,
                        pending_ticket: null,
                        awaiting_observation_receipt_id: null,
                        terminal_state: null,
                        last_failure: null,
                    },
                    current_generation: 1,
                    reference_state: 'referenced',
                    state: 'referenced_ready',
                    live_status: {
                        position_steps: 0,
                        speed_steps_s: 0,
                        max_speed: 'unknown',
                        max_acceleration: 'unknown',
                        max_current: 'unknown',
                        stall_guard: 'unknown',
                        profile_verified: true,
                        switch_mask_verified: true,
                    },
                    profile: { verified: true },
                    switch_masks: { verified: true },
                },
                last_failure: null,
                latest_receipt: null,
            },
            z_axis: {
                axis: 'z',
                status: {
                    axis: 'z',
                    reference: 'referenced',
                    position_steps: 0,
                    speed_steps_s: 0,
                    left_switch_state: 1,
                    right_switch_state: 1,
                    left_switch_disabled: false,
                    right_switch_disabled: false,
                },
                provider: {
                    state: 'referenced_ready',
                },
            },
            successive_move_queue: {},
        },
        error: null,
    },
    connectionGeneration: 1,
    history: {
        data: {
            receipts: [] as Array<Record<string, unknown>>,
        },
        error: null,
    },
    historyCalls: [] as number[],
    lifecycleInvokeCalls: [] as Array<Record<string, unknown>>,
    yInvokeCalls: [] as Array<Record<string, unknown>>,
    deckInvokeCalls: [] as Array<Record<string, unknown>>,
    yInterruptCalls: [] as Array<Record<string, unknown>>,
    methodCalls: [] as Array<Record<string, unknown>>,
    yInvokeError: null as unknown,
    lifecycleInvokeError: null as unknown,
    lifecycleInvokeData: undefined as Record<string, unknown> | undefined,
    lifecycleInvokePending: false,
    lifecycleDeferred: false,
    lifecycleCallbacks: null as null | { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
    v2MutationHookCalls: 0,
    deckInvokeError: null as unknown,
    deckInvokePending: false,
    deckDeferred: false,
    deckCallbacks: null as null | { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
    receiptHookCalls: [] as Array<{ commandId: string | null; generation: number; enabled: boolean }>,
    yInterruptError: null as unknown,
    yInvokeData: undefined as Record<string, unknown> | undefined,
    normalQueuedReceipt: undefined as Record<string, unknown> | undefined,
    yInterruptData: undefined as Record<string, unknown> | undefined,
    yReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isStale: false },
    zReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isStale: false },
    lifecycleReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isStale: false },
    deckReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isStale: false },
    v2Dashboard: {
        data: {
            schema_version: 'bioxp.operator_dashboard.v2',
            generated_at: 1,
            ownership_generation: 1,
            board4: {
                state: 'active', prior_board_epoch: 1, active_board_epoch: 2,
                transition_phase: 'committed', transition_evidence: {},
                member_motors: { y: 0, z: 1, gripper: 2 }, state_version: 3, updated_at: 1,
            },
            y_axis: {
                axis: 'y', board_id: 4, motor_id: 0, ownership_generation: 1,
                prior_board_epoch: 1, active_board_epoch: 2, prepared_board_epoch: 2,
                lifecycle_state: 'referenced_ready', reference_state: 'referenced',
                position_steps: 100, position_reply_valid: true, position_status_code: 100,
                speed_steps_s: 0, speed_reply_valid: true, speed_status_code: 100,
                left_switch_raw: 0, left_switch_reply_valid: true, left_switch_status_code: 100,
                home_effective: false, profile_fingerprint: 'a'.repeat(64), profile_readback_valid: true,
                profile_mismatches: [], active_command: null, interrupt_epoch: 0,
                latest_compact_receipt: null as Record<string, unknown> | null, last_discrepancy_steps: null,
                state_version: 4, updated_at: 1, physical_position_verified: false,
            },
            active_commands: [], command_queue: { schema_version: 'bioxp.oem_command_queue.v1', generated_at: 1, items: [] }, latest_receipts: [],
        },
        error: null as unknown,
        isStale: false,
    },
    v2Catalog: {
        data: undefined as Record<string, unknown> | undefined,
        error: null as unknown,
        isStale: false,
    },
}));

const dep = (key: string, met: boolean, reason: string | null = null) => ({ key, met, reason });

const xMoveAction = () => ({
    action_id: 'oem.x.move_steps',
    label: 'X Relative Move',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X relative move',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/relative',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_relative_oem_envelope', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'steps', type: 'integer', required: true, minimum: -90243, maximum: 90243 }],
    stages: [],
});

const xAbsoluteAction = () => ({
    action_id: 'oem.x.move_absolute',
    label: 'X Absolute Move',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X absolute move',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/absolute',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_target_oem_envelope', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: 60, maximum: 90263 }],
    stages: [],
});

const zHomeAction = () => ({
    ...xMoveAction(),
    action_id: 'oem.z.manual_home',
    label: 'Z OEM Home',
    subsystem: 'motion.z',
    informational_path: '/motion/oem/manual/home',
    enabled: true,
    disabled_reason: null,
    unavailable_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_z_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
    ],
    inputs: [],
});

const zMoveAction = () => ({
    ...xMoveAction(),
    action_id: 'oem.z.move_steps',
    label: 'Z Relative Move',
    subsystem: 'motion.z',
    informational_path: '/motion/oem/manual/relative',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
    inputs: [{ name: 'steps', type: 'integer', required: true, minimum: -250000, maximum: 250000 }],
});

const zAbsoluteAction = () => ({
    ...xAbsoluteAction(),
    action_id: 'oem.z.move_absolute',
    label: 'Z Absolute Move',
    subsystem: 'motion.z',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
    inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: -5000, maximum: 250000 }],
});

const zClearAction = () => ({
    ...zHomeAction(),
    action_id: 'oem.z.clear',
    label: 'Z Clear',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
});

const xHomeAction = () => ({
    action_id: 'oem.x.manual_panel_home',
    label: 'X Home',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X home',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/home',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [],
    stages: [],
});

vi.mock('../../src/lib/bioxpClient', () => ({
    BIOXP_Y_RELATIVE_MIN_STEPS: -2_147_483_648,
    BIOXP_Y_RELATIVE_MAX_STEPS: 2_147_483_647,
    BIOXP_Y_ABSOLUTE_MIN_STEPS: -2_147_483_648,
    BIOXP_Y_ABSOLUTE_MAX_STEPS: 2_147_483_647,
    useBioXpStatus: () => ({
        data: {
            connection: {
                active: true,
                reachable: true,
                configured: true,
                runtime_ready: true,
                generation: state.connectionGeneration,
                freshness_budget_seconds: 1800,
                last_error: null,
            },
        },
        error: null,
    }),
    useBioXpOperatorDashboard: (_generation: number, enabled: boolean) => {
        state.v1DashboardEnabled = enabled;
        return state.dashboard;
    },
    useBioXpOperatorDashboardV2: () => state.v2Dashboard,
    useBioXpOperatorControlCatalogV2: () => state.v2Catalog,
    useBioXpOperatorReceiptV2: (commandId: string | null, generation: number, enabled: boolean) => {
        state.receiptHookCalls.push({ commandId, generation, enabled });
        if (commandId?.startsWith('deck-command-')) return state.deckReceipt;
        if (commandId?.startsWith('lifecycle-command-')) return state.lifecycleReceipt;
        if (commandId?.startsWith('z-command-')) return state.zReceipt;
        return state.yReceipt;
    },
    useBioXpOperatorActionHistory: (...args: unknown[]) => {
        state.historyCalls.push(args[2] as number);
        return state.history;
    },
    bioXpReceiptIsNonTerminal: (receipt: { status?: unknown } | null | undefined): boolean =>
        typeof receipt?.status === 'string'
        && receipt.status !== 'completed'
        && receipt.status !== 'failed'
        && receipt.status !== 'rejected'
        && receipt.status !== 'blocked'
        && receipt.status !== 'cleared',
    useBioXpOperatorControlCatalog: (_generation: number, enabled: boolean) => {
        state.v1CatalogEnabled = enabled;
        return state.catalog;
    },
    useBioXpOperatorActionAdmission: () => {
        state.admissionCalls += 1;
        return { data: { enabled: true, disabled_reason: null, dependencies: [] }, error: null };
    },
    useInvokeBioXpOperatorAction: () => ({
        data: undefined,
        error: null,
        isPending: state.invokePending,
        mutate: (payload: Record<string, unknown>) => state.invokeCalls.push(payload),
        reset: state.stableReset,
    }),
    useInvokeBioXpOperatorActionV2: () => {
        const lifecycle = state.v2MutationHookCalls % 2 === 0;
        state.v2MutationHookCalls += 1;
        return lifecycle
            ? {
                data: state.lifecycleInvokeData,
                error: state.lifecycleInvokeError,
                isPending: state.lifecycleInvokePending,
                mutate: (
                    payload: Record<string, unknown>,
                    callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
                ) => {
                    state.lifecycleInvokeCalls.push(payload);
                    if (state.lifecycleDeferred) {
                        state.lifecycleCallbacks = callbacks ?? null;
                        return;
                    }
                    const actionId = (payload.request as Record<string, unknown>).action_id as string;
                    const receipt = {
                        command_id: `lifecycle-command-${state.lifecycleInvokeCalls.length}`,
                        action_id: actionId,
                        status: 'completed',
                        terminal: true,
                        ownership_generation: state.v2Dashboard.data.ownership_generation,
                        accepted_at: state.v2Dashboard.data.generated_at + 1,
                    };
                    state.lifecycleReceipt.data = receipt;
                    callbacks?.onSuccess?.(receipt);
                },
                reset: state.stableReset,
            }
            : {
                data: state.yInvokeData,
                error: state.yInvokeError,
                isPending: false,
                mutate: (
                    payload: Record<string, unknown>,
                    callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
                ) => {
                    state.yInvokeCalls.push(payload);
                    if (state.normalQueuedReceipt !== undefined) callbacks?.onSuccess?.(state.normalQueuedReceipt);
                },
                reset: state.stableReset,
            };
    },
    useInvokeBioXpDeckActionV2: () => ({
        data: undefined,
        error: state.deckInvokeError,
        isPending: state.deckInvokePending,
        mutate: (
            payload: Record<string, unknown>,
            callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
        ) => {
            state.deckInvokeCalls.push(payload);
            state.deckCallbacks = callbacks ?? {};
            if (!state.deckDeferred) {
                callbacks?.onSuccess?.({ command_id: 'deck-command-mounted-1', status: 'queued', terminal: false });
            }
        },
        reset: state.stableReset,
    }),
    bioXpPostDispatchCommandIdentity: (error: unknown) => {
        const detail = (error as { response?: { data?: { detail?: Record<string, unknown> } } })?.response?.data?.detail;
        return typeof detail?.command_id === 'string'
            ? { commandId: detail.command_id, statusPath: detail.status_path, retryGuidance: detail.retry_guidance }
            : null;
    },
    useInterruptBioXpOperatorActionV1: () => ({
        data: state.yInterruptData,
        error: state.yInterruptError,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.yInterruptCalls.push(payload),
        reset: state.stableReset,
    }),
    useSubmitBioXpOperatorMethodV1: () => ({
        data: undefined,
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.methodCalls.push(payload),
        reset: state.stableReset,
    }),
    useConnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useDisconnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useUpdateBioXpFreshness: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    bioXpErrorText: (error: unknown) => String(error),
    bioXpErrorPresentation: (error: unknown) => {
        const response = (error as { response?: { status?: number; data?: unknown } })?.response;
        const detail = (response?.data as { detail?: { error?: string } } | undefined)?.detail;
        return {
            status: response?.status ?? null,
            summary: detail?.error ?? String(error),
            rawJson: JSON.stringify(response?.data ?? null, null, 2),
        };
    },
}));

vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
vi.mock('../../src/components/BioXpOperatorControlTabs', () => ({ BioXpOperatorControlTabs: () => null }));
vi.mock('../../src/components/BioXpPipetteControlPanel', () => ({ BioXpPipetteControlPanel: () => null }));
vi.mock('../../src/components/BioXpQuickDashboard', () => ({ BioXpQuickDashboard: () => null }));
vi.mock('../../src/components/BioXpOperatorReports', () => ({ BioXpOperatorReports: () => null }));

import { BioXpCockpit } from '../../src/components/BioXpCockpit';

let container: HTMLDivElement;
let root: Root;

const setXAbsolute = async (value: string) => {
    const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
    const inputs = [...article.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    const absolute = inputs[1];
    await act(async () => {
        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        valueSetter?.call(absolute, value);
        absolute.dispatchEvent(new Event('input', { bubbles: true }));
        await Promise.resolve();
    });
};

const setZInput = async (index: number, value: string) => {
    const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
    const inputs = [...article.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    const input = inputs[index];
    await act(async () => {
        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        valueSetter?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        await Promise.resolve();
    });
};

const xReceipt = (status: string, index = 0) => ({
    schema_version: 'bioxp.operator_action_receipt.v1',
    command_id: `cmd_x_${index}`,
    action_id: 'oem.x.move_steps',
    kind: 'primitive',
    safety_class: 'motion',
    status,
    idempotency_key: 'ik_x',
    idempotency_replay_enabled: false,
    ownership_generation: 1,
    started_at: '2026-08-18T00:00:00.000Z',
    finished_at: status === 'completed' ? '2026-08-18T00:00:01.000Z' : null,
    duration_ms: status === 'completed' ? 1000 : null,
    request_received_at: null,
    lock_acquired_at: null,
    admission_completed_at: null,
    provider_entry_at: null,
    provider_returned_at: null,
    receipt_persist_started_at: null,
    remote_acknowledged: false,
    controller_acknowledged: false,
    controller_terminal_state_verified: false,
    physical_effect_verified: false,
    automatic_retry: null,
    physical_outcome: null,
    persistence_fallback: null,
    machine_assessment: 'unverified',
    operator_assessment: null,
    operator_note: null,
    operator_assessment_idempotency_key: null,
    response: null,
    stage_receipts: [],
});

beforeEach(() => {
    state.admissionCalls = 0;
    state.v1DashboardEnabled = null;
    state.v1CatalogEnabled = null;
    state.connectionGeneration = 1;
    state.invokeCalls = [];
    state.invokePending = false;
    state.historyCalls = [];
    state.lifecycleInvokeCalls = [];
    state.yInvokeCalls = [];
    state.deckInvokeCalls = [];
    state.yInterruptCalls = [];
    state.methodCalls = [];
    state.yInvokeError = null;
    state.lifecycleInvokeError = null;
    state.lifecycleInvokeData = undefined;
    state.lifecycleInvokePending = false;
    state.lifecycleDeferred = false;
    state.lifecycleCallbacks = null;
    state.v2MutationHookCalls = 0;
    state.deckInvokeError = null;
    state.deckInvokePending = false;
    state.deckDeferred = false;
    state.deckCallbacks = null;
    state.receiptHookCalls = [];
    state.yInterruptError = null;
    state.yInvokeData = undefined;
    state.normalQueuedReceipt = undefined;
    state.yInterruptData = undefined;
    state.yReceipt.data = undefined;
    state.yReceipt.error = null;
    state.zReceipt.data = undefined;
    state.zReceipt.error = null;
    state.lifecycleReceipt.data = undefined;
    state.lifecycleReceipt.error = null;
    state.deckReceipt.data = undefined;
    state.deckReceipt.error = null;
    state.v2Dashboard.error = null;
    state.v2Dashboard.isStale = false;
    state.v2Dashboard.data.ownership_generation = 1;
    state.v2Dashboard.data.board4.state_version = 3;
    state.v2Dashboard.data.y_axis.ownership_generation = 1;
    state.v2Dashboard.data.y_axis.state_version = 4;
    state.v2Dashboard.data.y_axis.latest_compact_receipt = null;
    state.v2Dashboard.data.latest_receipts = [];
    state.v2Catalog.error = null;
    state.v2Catalog.isStale = false;
    state.v2Catalog.data = {
        schema_version: 'bioxp.operator_control_catalog.v2',
        dashboard: structuredClone(state.v2Dashboard.data),
        actions: [
            'oem.x.move_steps',
            'oem.x.move_absolute',
            'oem.x.manual_panel_home',
            'oem.y.move_steps',
            'oem.y.move_absolute',
            'oem.y.manual_panel_home',
            'oem.y.diagnostic_home',
            'oem.z.move_steps',
            'oem.z.move_absolute',
            'oem.z.manual_home',
            'oem.z.clear',
        ].map((action_id) => ({
            action_id,
            request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: false,
            enabled: true,
            disabled_reason: null,
        })).concat([
            'oem.x.stop',
            'oem.y.stop',
            'oem.z.stop',
            'oem.z.abort',
            'oem.abort_all',
        ].map((action_id) => ({
            action_id,
            request_schema_version: 'bioxp.operator_interrupt_request.v1',
            response_schema_version: 'bioxp.operator_interrupt_receipt.v1',
            interrupt: true,
            enabled: true,
            disabled_reason: null,
        }))),
    };
    Object.assign(state.v2Dashboard.data, {
        deck: {
            current_location: 'LOC_TC',
            current_well: 'B2',
            position_table_revision: 'pt-206-9',
            destination_catalog_revision: 'deck-206-4',
            semantic_state_revision: 17,
            ownership_generation: 1,
            expected_board_epoch_by_board: { '4': 2, '5': 8 },
            destinations: [
                { key: 'LOC_TC', label: 'TC station', aliases: ['TC'], branch_kind: 'ordinary', camera_offset_supported: true },
                { key: 'LOC_OC', label: 'OC chiller', aliases: ['OC chiller'], branch_kind: 'ordinary', camera_offset_supported: true },
            ],
        },
    });
    state.v2Catalog.data.dashboard = structuredClone(state.v2Dashboard.data);
    (state.v2Catalog.data.actions as Array<Record<string, unknown>>).push({
        action_id: 'oem.deck.move_to_location',
        request_schema_version: 'bioxp.operator_action_request.v2',
        response_schema_version: 'bioxp.operator_action_receipt.v2',
        interrupt: false,
        enabled: true,
        disabled_reason: null,
        destination_catalog_revision: 'deck-206-4',
        position_table_revision: 'pt-206-9',
        required_board_ids: [4, 5],
        expected_board_epoch_by_board: { '4': 2, '5': 8 },
        destinations: [
            { key: 'LOC_TC', label: 'TC station', aliases: ['TC'], branch_kind: 'ordinary', camera_offset_supported: true },
            { key: 'LOC_OC', label: 'OC chiller', aliases: ['OC chiller'], branch_kind: 'ordinary', camera_offset_supported: true },
        ],
    });
    state.history.data.receipts = [];
    state.dashboard.data.motion = { enabled: true, reason: null };
    state.dashboard.data.x_axis.latest_receipt = null;
    state.dashboard.data.successive_move_queue = {};
    state.catalog.data.actions = [
        xMoveAction(),
        xAbsoluteAction(),
        xHomeAction(),
        zHomeAction(),
        zMoveAction(),
        zAbsoluteAction(),
        zClearAction(),
        {
            action_id: 'oem.x.stop',
            label: 'X Stop',
            subsystem: 'motion.x',
            category: 'route',
            kind: 'primitive',
            safety_class: 'stop',
            description: 'X stop',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/motion/oem/manual/stop',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
        {
            action_id: 'oem.abort_all',
            label: 'Aggregate Abort',
            subsystem: 'motion.x',
            category: 'route',
            kind: 'primitive',
            safety_class: 'emergency',
            description: 'abort',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/motion/oem/abort',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
        {
            action_id: 'meta.activate_motion',
            label: 'Activate Motion',
            subsystem: 'meta',
            category: 'meta',
            kind: 'meta',
            safety_class: 'motion',
            description: 'activate',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/operator/actions/meta.activate_motion',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
    ];
    state.dashboard.data.x_axis.provider.lifecycle.state = 'referenced_ready';
    state.dashboard.data.x_axis.status.reference = 'referenced';
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('mounted BioXP cockpit admission fan-out collapse (R-A1)', () => {
    it('uses the always-loaded v2 catalog for OEM activation and non-homing recovery', async () => {
        state.catalog.data.actions = [];
        (state.v2Catalog.data?.actions as Array<Record<string, unknown>>).push(
            {
                action_id: 'meta.activate_motion',
                request_schema_version: 'bioxp.operator_action_request.v2',
                response_schema_version: 'bioxp.operator_action_receipt.v2',
                interrupt: false,
                enabled: true,
                disabled_reason: null,
            },
            {
                action_id: 'meta.recover_motion_non_homing',
                request_schema_version: 'bioxp.operator_action_request.v2',
                response_schema_version: 'bioxp.operator_action_receipt.v2',
                interrupt: false,
                enabled: true,
                disabled_reason: null,
            },
        );

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find(
            (node) => node.textContent?.includes('Controller Activation & Recovery'),
        ) as HTMLElement;
        const activate = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Activate 24 V / Prepare Motion',
        ) as HTMLButtonElement;
        const recover = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Non-homing Recovery',
        ) as HTMLButtonElement;
        expect(activate.disabled).toBe(false);
        expect(recover.disabled).toBe(false);

        await act(async () => {
            activate.click();
            recover.click();
            await Promise.resolve();
        });
        expect(state.lifecycleInvokeCalls).toHaveLength(2);
        expect((state.lifecycleInvokeCalls[0].request as Record<string, unknown>).action_id).toBe('meta.activate_motion');
        expect((state.lifecycleInvokeCalls[1].request as Record<string, unknown>).action_id).toBe('meta.recover_motion_non_homing');
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
        expect(panel.textContent).toContain('meta.recover_motion_non_homing');
        expect(panel.textContent).toContain('lifecycle-command-2');
        expect(panel.textContent).toContain('completed');
        expect(state.receiptHookCalls.some((call) => call.commandId === 'lifecycle-command-2')).toBe(true);
    });

    it('treats a dispatched activation timeout as pending and reconciles the terminal dashboard receipt', async () => {
        (state.v2Catalog.data?.actions as Array<Record<string, unknown>>).push(
            {
                action_id: 'meta.activate_motion',
                request_schema_version: 'bioxp.operator_action_request.v2',
                response_schema_version: 'bioxp.operator_action_receipt.v2',
                interrupt: false,
                enabled: true,
                disabled_reason: null,
            },
            {
                action_id: 'meta.recover_motion_non_homing',
                request_schema_version: 'bioxp.operator_action_request.v2',
                response_schema_version: 'bioxp.operator_action_receipt.v2',
                interrupt: false,
                enabled: true,
                disabled_reason: null,
            },
        );
        state.lifecycleDeferred = true;
        state.lifecycleInvokeData = {
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'stale-activation-receipt',
            action_id: 'meta.activate_motion',
            status: 'completed',
            terminal: true,
            ownership_generation: 1,
            accepted_at: 0,
            error: null,
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find(
            (node) => node.textContent?.includes('Controller Activation & Recovery'),
        ) as HTMLElement;
        const activate = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Activate 24 V / Prepare Motion',
        ) as HTMLButtonElement;
        const recover = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Non-homing Recovery',
        ) as HTMLButtonElement;
        await act(async () => {
            activate.click();
            state.lifecycleInvokeError = {
                response: {
                    status: 409,
                    data: {
                        detail: {
                            error: 'bioxp_robot_timeout',
                            dispatch_state: 'outcome_ambiguous',
                        },
                    },
                },
            };
            state.lifecycleCallbacks?.onError?.(state.lifecycleInvokeError);
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('Activation / recovery failed · HTTP 409 · bioxp_robot_timeout');
        expect(panel.textContent).not.toContain('result pending');
        expect(activate.disabled).toBe(false);
        expect(recover.disabled).toBe(false);

        state.lifecycleInvokeError = {
            response: {
                status: 504,
                data: {
                    detail: {
                        error: 'bioxp_robot_timeout',
                        dispatch_state: 'outcome_ambiguous',
                        retry_guidance: 'do_not_retry_until_status_recovery',
                    },
                },
            },
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('Activation / recovery result pending');
        expect(panel.textContent).toContain('Do not retry');
        expect(panel.textContent).not.toContain('Activation / recovery failed');
        expect(
            [...(container.firstElementChild?.children ?? [])]
                .filter((element) => element.getAttribute('role') === 'alert'),
        ).toHaveLength(0);
        expect(panel.textContent).not.toContain('stale-activation-receipt');
        expect(activate.disabled).toBe(true);
        expect(recover.disabled).toBe(true);

        state.v2Dashboard.data.ownership_generation = 2;
        state.v2Dashboard.data.latest_receipts = [{
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'other-generation-activation',
            action_id: 'meta.activate_motion',
            status: 'completed',
            terminal: true,
            ownership_generation: 2,
            accepted_at: 2,
            error: null,
        }];
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('Activation / recovery result pending');
        expect(panel.textContent).not.toContain('other-generation-activation');
        expect(activate.disabled).toBe(true);
        expect(recover.disabled).toBe(true);

        state.v2Dashboard.data.ownership_generation = 1;
        state.v2Dashboard.data.latest_receipts = [{
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'lifecycle-command-activation',
            action_id: 'meta.activate_motion',
            status: 'dispatched',
            terminal: false,
            ownership_generation: 1,
            accepted_at: 2,
            error: null,
        }];
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('lifecycle-command-activation');
        expect(panel.textContent).toContain('dispatched');
        expect(panel.textContent).toContain('Activation / recovery result pending');
        expect(activate.disabled).toBe(true);
        expect(recover.disabled).toBe(true);

        state.lifecycleReceipt.data = {
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'lifecycle-command-activation',
            action_id: 'meta.activate_motion',
            status: 'dispatched',
            terminal: false,
            ownership_generation: 1,
            accepted_at: 2,
            error: null,
        };
        state.v2Dashboard.data.latest_receipts = [
            {
                schema_version: 'bioxp.operator_action_receipt.v2',
                command_id: 'lifecycle-command-activation',
                action_id: 'meta.activate_motion',
                status: 'dispatched',
                terminal: false,
                ownership_generation: 1,
                accepted_at: 2,
                error: null,
            },
            {
                schema_version: 'bioxp.operator_action_receipt.v2',
                command_id: 'lifecycle-command-activation',
                action_id: 'meta.activate_motion',
                status: 'completed',
                terminal: true,
                ownership_generation: 1,
                accepted_at: 2,
                error: null,
            },
        ];
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('lifecycle-command-activation');
        expect(panel.textContent).toContain('completed');
        expect(panel.textContent).not.toContain('result pending');
        expect(panel.textContent).not.toContain('failed');
        expect(activate.disabled).toBe(false);
        expect(recover.disabled).toBe(false);
    });

    it('renders the bounded robot failure detail from a terminal lifecycle receipt', async () => {
        (state.v2Catalog.data?.actions as Array<Record<string, unknown>>).push({
            action_id: 'meta.recover_motion_non_homing',
            request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: false,
            enabled: true,
            disabled_reason: null,
        });
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find(
            (node) => node.textContent?.includes('Controller Activation & Recovery'),
        ) as HTMLElement;
        const recover = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Non-homing Recovery',
        ) as HTMLButtonElement;
        await act(async () => {
            recover.click();
            await Promise.resolve();
        });
        state.lifecycleReceipt.data = {
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'lifecycle-command-1',
            action_id: 'meta.recover_motion_non_homing',
            status: 'failed',
            terminal: true,
            ownership_generation: 1,
            accepted_at: 2,
            error: {
                code: 'robot route returned HTTP 409',
                message: 'robot route returned HTTP 409',
                retryable: false,
                detail: {
                    provider_failure: 'z_manual_home_evidence_not_verified',
                    failure: 'board_not_initialized',
                    axis: 'z',
                    board: 4,
                    motor: 1,
                    source_return_code: 1,
                    controller_acknowledged: false,
                    controller_terminal_state_verified: false,
                    physical_effect_verified: false,
                    lifecycle_state: 'failed_latched',
                    reference_state: 'desynced',
                },
            },
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(panel.textContent).toContain('board_not_initialized');
        expect(panel.textContent).toContain('z_manual_home_evidence_not_verified');
        expect(panel.textContent).toContain('Axis z · Board 4 · Motor 1 · Source return 1');
        expect(panel.textContent).toContain('Controller acknowledged: no');
        expect(panel.textContent).toContain('Terminal state verified: no');
        expect(panel.textContent).toContain('Physical effect verified: no');
        expect(panel.textContent).toContain('Lifecycle: failed_latched · Reference: desynced');
    });

    it('polls a queued Home Z command and renders its bounded terminal failure detail in the Z card', async () => {
        state.normalQueuedReceipt = {
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'z-command-1',
            action_id: 'oem.z.manual_home',
            status: 'queued',
            terminal: false,
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const zArticle = [...container.querySelectorAll('article')].find(
            (node) => node.textContent?.includes('Z Axis'),
        ) as HTMLElement;
        const home = [...zArticle.querySelectorAll('button')].find(
            (button) => button.textContent === 'Home',
        ) as HTMLButtonElement;
        await act(async () => {
            home.click();
            await Promise.resolve();
        });
        expect(state.receiptHookCalls.some((call) => call.commandId === 'z-command-1')).toBe(true);

        state.zReceipt.data = {
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'z-command-1',
            action_id: 'oem.z.manual_home',
            status: 'failed',
            terminal: true,
            error: {
                code: 'robot route returned HTTP 409',
                message: 'robot route returned HTTP 409',
                retryable: false,
                detail: {
                    provider_failure: 'z_manual_home_evidence_not_verified',
                    failure: 'board_not_initialized',
                    axis: 'z',
                    board: 4,
                    motor: 1,
                    source_return_code: 1,
                    controller_acknowledged: false,
                    controller_terminal_state_verified: false,
                    physical_effect_verified: false,
                    lifecycle_state: 'failed_latched',
                    reference_state: 'desynced',
                },
            },
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(zArticle.textContent).toContain('board_not_initialized');
        expect(zArticle.textContent).toContain('z_manual_home_evidence_not_verified');
        expect(zArticle.textContent).toContain('Axis z · Board 4 · Motor 1 · Source return 1');
        expect(zArticle.textContent).toContain('Controller acknowledged: no');
        expect(zArticle.textContent).toContain('Terminal state verified: no');
        expect(zArticle.textContent).toContain('Physical effect verified: no');
        expect(zArticle.textContent).toContain('Lifecycle: failed_latched · Reference: desynced');
    });

    it('hides lifecycle command and receipt state during a connection generation transition', async () => {
        (state.v2Catalog.data?.actions as Array<Record<string, unknown>>).push({
            action_id: 'meta.recover_motion_non_homing',
            request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: false,
            enabled: true,
            disabled_reason: null,
        });
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find(
            (node) => node.textContent?.includes('Controller Activation & Recovery'),
        ) as HTMLElement;
        const recover = [...panel.querySelectorAll('button')].find(
            (button) => button.textContent === 'Non-homing Recovery',
        ) as HTMLButtonElement;
        await act(async () => {
            recover.click();
            await Promise.resolve();
        });
        expect(panel.textContent).toContain('lifecycle-command-1');

        state.connectionGeneration = 2;
        act(() => {
            flushSync(() => root.render(<BioXpCockpit />));
            expect(panel.textContent).not.toContain('lifecycle-command-1');
            expect(panel.textContent).not.toContain('meta.recover_motion_non_homing');
        });
    });

    it('renders finite deck movement and submits exactly one semantic enqueue', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        expect(panel).toBeTruthy();
        expect(panel.textContent).toContain('LOC_TC');
        expect(panel.textContent).toContain('B2');
        expect(panel.textContent).toContain('pt-206-9');
        expect(panel.textContent).toContain('deck-206-4');
        const selector = panel.querySelector('select') as HTMLSelectElement;
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set;
            setter?.call(selector, 'LOC_OC');
            selector.dispatchEvent(new Event('change', { bubbles: true }));
            await Promise.resolve();
        });
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        expect(state.deckInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.deckInvokeCalls[0]).toMatchObject({ request: {
            action_id: 'oem.deck.move_to_location',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: { '4': 2, '5': 8 },
            inputs: { target: 'LOC_OC', camera_offset: false },
        } });
        const submitted = state.deckInvokeCalls[0].request as { inputs: Record<string, unknown> };
        expect(Object.keys(submitted.inputs).sort()).toEqual(['camera_offset', 'target']);
        expect(panel.textContent).toContain('deck-command-mounted-1');
        expect(state.receiptHookCalls.some((call) => call.commandId === 'deck-command-mounted-1' && call.generation === 1)).toBe(true);
    });

    it.each(['stopped', 'aborted', 'cancelled'])('renders truthful terminal deck lifecycle %s', async (status) => {
        state.deckReceipt.data = {
            command_id: 'deck-command-mounted-1',
            action_id: 'oem.deck.move_to_location',
            status,
            terminal: true,
            completion_class: status,
            error: null,
            deck_movement: {
                target: 'LOC_TC',
                target_label: 'TC station',
                source_branch: 'ordinary.scriptmoveTo',
                controller_completion_verified: false,
                semantic_state_committed: false,
                physical_observation_verified: false,
            },
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());

        const lifecycle = [...panel.querySelectorAll('dl > div')]
            .find((row) => row.querySelector('dt')?.textContent === 'Lifecycle');
        expect(lifecycle?.textContent).toContain(status);
        expect(panel.textContent).toContain('not pending');
    });

    it('disables deck movement on stale generation authority with an exact reason', async () => {
        state.v2Catalog.isStale = true;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        expect(move.disabled).toBe(true);
        expect(panel.textContent).toContain('Fresh v2 catalog or dashboard authority is unavailable.');
    });

    it.each([
        ['ownership', () => { (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).ownership_generation = 2; }],
        ['position table', () => { (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).deck!.position_table_revision = 'pt-stale'; }],
        ['destination catalog', () => { (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).deck!.destination_catalog_revision = 'deck-stale'; }],
        ['board epochs', () => { (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).deck!.expected_board_epoch_by_board['5'] = 9; }],
        ['destination list', () => { (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).deck!.destinations = []; }],
    ])('disables deck movement when separately fetched dashboard mismatches catalog embedded %s authority', async (_label, mutate) => {
        mutate();
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        expect(move.disabled).toBe(true);
        expect(panel.textContent).toContain('matching catalog and dashboard deck authority is unavailable');
    });

    it('keeps deck movement enabled for semantically equal board epochs in reversed key order', async () => {
        const reversedEpochs = new Proxy({ '4': 2, '5': 8 }, {
            ownKeys: () => ['5', '4'],
        });
        (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).deck!.expected_board_epoch_by_board = reversedEpochs;

        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        expect(move.disabled).toBe(false);
        expect(panel.textContent).toContain('Robot action enabled for the selected finite destination.');
    });

    it('renders receipt unavailable and outcome uncertain instead of inventing queued state', async () => {
        state.yReceipt.error = new Error('detail parse failed');
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        expect(panel.textContent).toContain('receipt unavailable / outcome uncertain');
        expect(panel.textContent).toContain('Do not resubmit');
        expect(panel.textContent).not.toContain('Lifecyclequeued');
    });

    it('keeps a recovered wrong-action receipt uncertain instead of confirming deck completion', async () => {
        state.deckDeferred = true;
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        await act(async () => state.deckCallbacks?.onError?.({
            response: {
                status: 502,
                data: {
                    detail: {
                        error: 'operator_action_receipt_action_mismatch',
                        command_id: 'deck-command-wrong-action',
                        action_id: 'oem.y.move_steps',
                        status_path: '/api/bioxp/operator/actions/v2/receipts/deck-command-wrong-action',
                        retry_guidance: 'Do not resubmit; poll the command ID.',
                    },
                },
            },
        }));
        state.deckReceipt.data = {
            command_id: 'deck-command-wrong-action',
            action_id: 'oem.y.move_steps',
            status: 'completed',
            terminal: true,
            completion_class: 'event_128',
            physical_effect_verified: true,
            error: null,
        };
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });

        expect(panel.textContent).toContain('receipt unavailable / outcome uncertain');
        expect(panel.textContent).toContain('Do not resubmit');
        expect(panel.textContent).toContain('Lifecycleunavailable');
        expect(panel.textContent).toContain('Ambiguous outcomeambiguous');
        expect(panel.textContent).toContain('Recovery requiredrequired');
        expect(panel.textContent).not.toContain('Lifecyclecompleted');
    });

    it('ignores a deferred deck enqueue completion after connection generation changes', async () => {
        state.deckDeferred = true;
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        const oldCallbacks = state.deckCallbacks;
        state.connectionGeneration = 2;
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        await act(async () => oldCallbacks?.onSuccess?.({ command_id: 'deck-old-generation', status: 'queued', terminal: false }));
        expect(container.textContent).not.toContain('deck-old-generation');
    });

    it('keeps Y and deck errors on their independent control surfaces', async () => {
        state.yInvokeError = { response: { status: 409, data: { detail: { error: 'y_conflict' } } } };
        state.deckInvokeError = { response: { status: 502, data: { detail: { error: 'deck_uncertain' } } } };
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const deck = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('OEM Deck Movement')) as HTMLElement;
        const y = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const deckMove = [...deck.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        const yMove = [...y.querySelectorAll('button')].find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        await act(async () => { deckMove.click(); yMove.click(); await Promise.resolve(); });
        expect(deck.textContent).toContain('Deck enqueue failed');
        expect(deck.textContent).toContain('deck_uncertain');
        expect(deck.textContent).not.toContain('y_conflict');
        expect(y.textContent).toContain('Y enqueue failed');
        expect(y.textContent).toContain('y_conflict');
        expect(y.textContent).not.toContain('deck_uncertain');
    });

    it('derives X enablement from catalog and dashboard with zero always-on admission calls', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(state.admissionCalls).toBe(0);
        expect(state.v1DashboardEnabled).toBe(true);
        expect(state.v1CatalogEnabled).toBe(true);
        expect(document.body.textContent).not.toContain('Checking exact robot admission.');

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const goAbsolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(goAbsolute.disabled).toBe(false);

        await act(async () => movePositive.click());
        expect(state.yInvokeCalls[0]).toMatchObject({ request: {
            action_id: 'oem.x.move_steps',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: { steps: 10000 },
        } });

        await act(async () => home.click());
        expect(state.yInvokeCalls[1]).toMatchObject({ request: {
            action_id: 'oem.x.manual_panel_home',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: {},
        } });

        await act(async () => goAbsolute.click());
        expect(state.yInvokeCalls[2]).toMatchObject({ request: {
            action_id: 'oem.x.move_absolute',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: { position_steps: 60 },
        } });
        expect(state.admissionCalls).toBe(0);
    });

    it('does not add BMS lifecycle, motion-banner, receipt, or queue gates to robot-authorized X actions', async () => {
        state.dashboard.data.x_axis.provider.lifecycle.state = 'unprepared';
        state.dashboard.data.x_axis.status.reference = 'desynced';
        state.dashboard.data.motion = { enabled: false, reason: 'stale dashboard blocker' };
        state.history.data.receipts = [xReceipt('acknowledged')];
        state.dashboard.data.successive_move_queue = {
            x: { active_command_id: 'cmd_prev', depth: 8, head_action_id: 'oem.x.move_steps', state: 'queued' },
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const goAbsolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(goAbsolute.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        await act(async () => movePositive.click());
        expect(state.yInvokeCalls[0]).toMatchObject({
            request: {
                action_id: 'oem.x.move_steps',
                expected_connection_generation: 1,
                expected_ownership_generation: 1,
                inputs: { steps: 10000 },
            },
        });
        expect(state.admissionCalls).toBe(0);
    });

    it('uses robot enabled state and catalog input schemas without local X/Z re-adjudication', async () => {
        const xMove = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.move_steps')!;
        xMove.dependencies = [dep('stale_bms_projection', false, 'Stale BMS projection')];
        const xAbsolute = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.move_absolute')!;
        xAbsolute.inputs = [{ name: 'position_steps', type: 'integer', required: true, minimum: -5000, maximum: 120000 }];

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        await setXAbsolute('100000');
        await setZInput(0, '200000');
        await setZInput(1, '200000');

        const xArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const xButtons = [...xArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const xMovePositive = xButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const xGoAbsolute = xButtons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zButtons = [...zArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const zMovePositive = zButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const zGoAbsolute = zButtons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(xMovePositive.disabled).toBe(false);
        expect(xGoAbsolute.disabled).toBe(false);
        expect(zMovePositive.disabled).toBe(false);
        expect(zGoAbsolute.disabled).toBe(false);
        await act(async () => xMovePositive.click());
        await act(async () => xGoAbsolute.click());
        await act(async () => zMovePositive.click());
        await act(async () => zGoAbsolute.click());
        expect(state.yInvokeCalls.map((call) => {
            const request = call.request as Record<string, unknown>;
            return { actionId: request.action_id, inputs: request.inputs };
        })).toEqual([
            { actionId: 'oem.x.move_steps', inputs: { steps: 10000 } },
            { actionId: 'oem.x.move_absolute', inputs: { position_steps: 100000 } },
            { actionId: 'oem.z.move_steps', inputs: { steps: 200000 } },
            { actionId: 'oem.z.move_absolute', inputs: { position_steps: 200000 } },
        ]);
    });

    it('uses the robot action enabled flag as final X command authority', async () => {
        const xHome = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.manual_panel_home')!;
        Object.assign(xHome, {
            enabled: false,
            disabled_reason: 'Robot denied X Home.',
        });
        const v2XHome = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>)
            .find((row) => row.action_id === 'oem.x.manual_panel_home')!;
        Object.assign(v2XHome, {
            enabled: false,
            disabled_reason: 'Robot denied X Home.',
        });

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const home = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Home') as HTMLButtonElement;
        expect(home.disabled).toBe(true);
        expect(home.title).toContain('Robot denied X Home.');
    });

    it('does not turn a pending unrelated mutation into a Z Clear lockout', async () => {
        state.invokePending = true;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zClear = [...zArticle.querySelectorAll('button')].find((button) => button.textContent === 'Z Clear (automatic OEM position)') as HTMLButtonElement;
        expect(zClear.disabled).toBe(false);
    });

    it('applies the robot catalog X input schema to absolute targets', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        await setXAbsolute('999999');

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const goAbsolute = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        expect(goAbsolute.disabled).toBe(true);
        expect(goAbsolute.title).toContain('Requested X target must be an integer from 60 through 90263.');
        expect(state.admissionCalls).toBe(0);
    });

    it('leaves successive X move and Home admission to the robot while an X command is active', async () => {
        state.history.data.receipts = [xReceipt('acknowledged')];
        state.dashboard.data.successive_move_queue = {
            x: { active_command_id: 'cmd_prev', depth: 1, head_action_id: 'oem.x.move_steps', state: 'queued' },
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(movePositive.title).not.toContain('in progress');
        expect(home.disabled).toBe(false);
        expect(home.title).not.toContain('acknowledged');

        const queueStrip = article.querySelector('[data-testid="successive-move-queue"]') as HTMLElement;
        expect(queueStrip).not.toBeNull();
        expect(queueStrip.textContent).toContain('X:');
        expect(queueStrip.textContent).toContain('oem.x.move_steps');
        expect(state.admissionCalls).toBe(0);
    });

    it('leaves X queue admission to the robot when the cached queue projection is full', async () => {
        state.history.data.receipts = [xReceipt('acknowledged')];
        state.dashboard.data.successive_move_queue = {
            x: { active_command_id: 'cmd_prev', depth: 8, head_action_id: 'oem.x.move_steps', state: 'queued' },
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps moves and cross-axis Home submittable while a command is pending (successive entry)', async () => {
        state.invokePending = true;

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const xArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const xButtons = [...xArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const xMovePositive = xButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const xHome = xButtons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zButtons = [...zArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const zHome = zButtons.find((button) => button.textContent === 'Home') as HTMLButtonElement;

        expect(xMovePositive.disabled).toBe(false);
        expect(xHome.disabled).toBe(false);
        expect(zHome.disabled).toBe(false);
        expect(state.invokeCalls).toHaveLength(0);
    });

    it('submits a successive move while the prior move is pending', async () => {
        state.invokePending = true;

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;

        await act(async () => {
            movePositive.click();
            await Promise.resolve();
        });

        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: 'oem.x.move_steps' } });
    });

    it('re-enables X controls from the terminal receipt even when the dashboard snapshot lags (R-A3)', async () => {
        state.history.data.receipts = [xReceipt('completed')];
        state.dashboard.data.x_axis.latest_receipt = {
            command_id: 'cmd_x',
            intent: 'x_move_steps',
            status: 'acknowledged',
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('passes the selected depth to the history query and renders that many receipts (R-A5)', async () => {
        state.history.data.receipts = Array.from({ length: 30 }, (_, i) => xReceipt('completed', i));

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(state.historyCalls.at(-1)).toBe(25);

        const select = [...container.querySelectorAll('select')]
            .find((node) => node.getAttribute('aria-label') === 'Recent robot actions depth') as HTMLSelectElement;
        expect(select).not.toBeUndefined();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set;
        setter?.call(select, '50');
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await act(async () => {
            await Promise.resolve();
        });

        expect(state.historyCalls.at(-1)).toBe(50);
        const historySection = [...container.querySelectorAll('section')]
            .find((node) => node.querySelector('h2')?.textContent === 'Recent Robot Actions') as HTMLElement;
        const receiptArticles = [...historySection.querySelectorAll('article')]
            .filter((node) => node.textContent?.includes('oem.x.move_steps'));
        expect(receiptArticles.length).toBe(30);
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps X controls usable when the connection token differs from the robot ownership generation (regression)', async () => {
        state.connectionGeneration = 3189298922692611;
        state.history.data.receipts = [];

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('mounts operational strict Y controls with exact bounds and payloads while generic Y remains absent', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section).toBeTruthy();
        const manualControls = section.closest('section') as HTMLElement;
        expect(manualControls.querySelector('h2')?.textContent).toBe('Exact OEM Manual Controls');
        expect(manualControls.textContent).toContain('X Axis');
        expect(manualControls.textContent).toContain('Z Axis');
        expect(manualControls.textContent).toContain('Gripper');
        const inputs = [...section.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
        expect(inputs[0]?.min).toBe('0');
        expect(inputs[0]?.max).toBe('2147483647');
        expect(inputs[1]?.min).toBe('-2147483648');
        expect(inputs[1]?.max).toBe('2147483647');
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const stop = buttons.find((button) => button.textContent === 'Stop') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(false);
        expect(stop.disabled).toBe(false);
        await act(async () => movePositive.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({
            request: {
                action_id: 'oem.y.move_steps',
                expected_connection_generation: 1,
                schema_version: 'bioxp.operator_action_request.v2',
                expected_ownership_generation: 1,
                expected_board_epoch_by_board: {},
                inputs: { steps: 1000 },
            },
        });
        await act(async () => stop.click());
        expect(state.yInterruptCalls[0]).toMatchObject({
            actionId: 'oem.y.stop',
            request: {
                expected_connection_generation: 1,
                schema_version: 'bioxp.operator_interrupt_request.v1',
                reason: 'BMS operator requested recovered-OEM addressed Y STOP',
                observed_ownership_generation: 1,
                observed_board_epoch_by_board: {},
            },
        });
        expect((state.yInterruptCalls[0].request as { idempotency_key?: unknown }).idempotency_key).toEqual(expect.any(String));
        const yCards = [...container.querySelectorAll('article')].filter((node) => node.querySelector('h3')?.textContent === 'Y Axis');
        expect(yCards).toEqual([section]);
    });

    it('uses the robot action enabled state and reason for normal Y controls while STOP stays independent', async () => {
        const yMove = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>)
            .find((row) => row.action_id === 'oem.y.move_steps')!;
        Object.assign(yMove, {
            enabled: false,
            disabled_reason: 'Robot denied Y movement.',
        });

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const stop = buttons.find((button) => button.textContent === 'Stop') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(true);
        expect(movePositive.title).toBe('Robot denied Y movement.');
        expect(stop.disabled).toBe(false);
    });

    it('rejects a negative Y step magnitude before directional transformation can overflow signed int32', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const stepInput = section.querySelector('input[type="number"]') as HTMLInputElement;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        setter?.call(stepInput, '-2147483648');
        stepInput.dispatchEvent(new Event('input', { bubbles: true }));
        await act(async () => { await Promise.resolve(); });

        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(true);
        expect(movePositive.title).toContain('Step magnitude must be an integer from 0 through 2147483647.');
        expect(state.yInvokeCalls).toHaveLength(0);
    });

    it('renders Y telemetry value plus reply validity, status, profile health, and observation time', async () => {
        Object.assign(state.v2Dashboard.data.y_axis, {
            position_reply_valid: false,
            position_status_code: 13,
            speed_reply_valid: false,
            speed_status_code: 14,
            left_switch_raw: 1,
            left_switch_reply_valid: true,
            left_switch_status_code: 100,
            profile_readback_valid: false,
            profile_mismatches: ['SAP4 expected 1800; observed 1700'],
            updated_at: 1,
        });

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const metric = (label: string) => [...section.querySelectorAll('dl > div')]
            .find((row) => row.querySelector('dt')?.textContent === label)?.textContent ?? '';
        expect(metric('Position')).toContain('Invalid reply · status 13');
        expect(metric('Speed')).toContain('Invalid reply · status 14');
        expect(metric('Home switch')).toContain('Valid reply · status 100');
        expect(metric('Profile')).toContain('Invalid · SAP4 expected 1800; observed 1700');
        expect(metric('Updated')).toContain('1970-01-01T00:00:01.000Z');
    });

    it('treats ownership-generation mismatch as observational while keeping normal Y and STOP reachable', async () => {
        (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).ownership_generation = 2;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        expect((buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement).disabled).toBe(false);
        expect((buttons.find((button) => button.textContent === 'Stop') as HTMLButtonElement).disabled).toBe(false);
        expect(section.textContent).not.toContain('matching v2 catalog and dashboard authority is unavailable');
    });

    it('renders bounded structured enqueue and STOP errors adjacent to Y with status and raw evidence', async () => {
        state.yInvokeError = { response: { status: 409, data: { detail: { error: 'board_epoch_conflict', expected: { '4': 2 }, actual: { '4': 3 } } } } };
        state.yInterruptError = { response: { status: 504, data: { detail: { error: 'bioxp_robot_timeout', dispatch_state: 'outcome_ambiguous' } } } };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const movePositive = [...section.querySelectorAll('button')].find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        await act(async () => { movePositive.click(); await Promise.resolve(); });
        expect(section.textContent).toContain('Y enqueue failed · HTTP 409 · board_epoch_conflict');
        expect(section.textContent).toContain('Y STOP failed · HTTP 504 · bioxp_robot_timeout');
        const raw = [...section.querySelectorAll('pre')].map((node) => node.textContent).join('\n');
        expect(raw).toContain('"expected"');
        expect(raw).toContain('"actual"');
        expect(raw).toContain('"dispatch_state"');
    });


    it('fails normal Y closed when current robot control state is unavailable', async () => {
        state.v2Catalog.error = new Error('catalog query failed');
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        expect((buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement).disabled).toBe(true);
        expect((buttons.find((button) => button.textContent === 'Stop') as HTMLButtonElement).disabled).toBe(false);
        expect(section.textContent).not.toContain('Fresh v2 catalog or dashboard authority is unavailable');
    });

    it('disables Z normal controls when current robot control state is unavailable instead of rendering dead controls', async () => {
        state.catalog.data.actions.push({
            ...xAbsoluteAction(),
            action_id: 'oem.z.move_absolute',
            enabled: true,
            disabled_reason: null,
            inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: 0, maximum: 160000 }],
        });
        state.v2Catalog.error = new Error('catalog query failed');
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const absolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(true);
        expect(home.disabled).toBe(true);
        expect(absolute.disabled).toBe(true);
        await act(async () => {
            movePositive.click();
            home.click();
            absolute.click();
            await Promise.resolve();
        });
        expect(state.yInvokeCalls).toHaveLength(0);
    });

    it('renders successful detailed Y action and independent STOP receipts', async () => {
        state.v2Dashboard.data.y_axis.latest_compact_receipt = { command_id: 'cmd-y-detail' };
        state.yReceipt.data = {
            command_id: 'cmd-y-detail',
            status: 'completed',
            completion_class: 'event_128',
            requested_values: { steps: 100 },
            effective_values: { target_steps: 1100 },
            observed_values: {
                terminal_position_steps: 1100,
                terminal_speed_steps_s: 0,
                discrepancy_steps: 0,
            },
            physical_effect_verified: false,
            controller_evidence: { addressed_event_128: true, speed_zero: true },
            raw_return_layers: { provider: { ok: true } },
            transport_artifacts: [{ kind: 'tmcl_reply', status: 100 }],
        };
        state.yInterruptData = {
            schema_version: 'bioxp.operator_interrupt_receipt.v1',
            interrupt_attempt_id: 'interrupt-attempt-12345678',
            action_id: 'oem.y.stop',
            controller_stop_acknowledged: true,
            persistence_state: 'committed',
            recovery_hold: false,
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section.textContent).toContain('Command cmd-y-detail: completed');
        expect(section.textContent).toContain('class=event_128');
        expect(section.textContent).toContain('terminal position=1100');
        expect(section.textContent).toContain('terminal speed=0');
        expect(section.textContent).toContain('Latest independent Y STOP receipt');
        expect(section.textContent).toContain('interrupt-attempt-12345678');
        const raw = [...section.querySelectorAll('pre')].map((node) => node.textContent).join('\n');
        expect(raw).toContain('"addressed_event_128": true');
        expect(raw).toContain('"status": 100');
    });

    it('submits recovered OEM moveXY and HomeXY through the typed method route', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = container.querySelector('[data-testid="serial206-xy-oem-panel"]') as HTMLElement;
        expect(panel).not.toBeNull();
        expect(panel.textContent).toContain('Combined XY Capability');
        expect(panel.textContent).toContain('one backend OEM moveXY transaction');
        const buttons = [...panel.querySelectorAll('button')] as HTMLButtonElement[];
        const move = buttons.find((button) => button.textContent === 'Move X + Y together') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home X + Y') as HTMLButtonElement;
        expect(move.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        await act(async () => move.click());
        await act(async () => home.click());
        expect(state.methodCalls).toHaveLength(2);
        expect(state.methodCalls[0]).toMatchObject({
            method_action_id: 'oem.xy.move_absolute',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: { x_steps: 60, y_steps: 0 },
        });
        expect(state.methodCalls[1]).toMatchObject({
            method_action_id: 'oem.xy.home',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: {},
        });
    });
});
