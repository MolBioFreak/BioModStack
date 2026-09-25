import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BioXpOperatorReceiptDetailV2 } from '../../src/lib/bioxpClient';
import coherentFailureProducer from '../fixtures/bioxp_xy_coherent_failure.json';
import deckQueryHistory from '../fixtures/bioxp_deck_query_history.json';
import actualParkReceipt from '../fixtures/bioxp_park_completed_receipt.json';
import bmsMetadata from '../fixtures/bioxp_xy_bms_metadata.json';
import actualY5 from '../fixtures/bioxp_xy_y5_compact.json';
import manualReport from '../fixtures/bioxp_xy_manual_report.json';
import actualY5History from '../fixtures/bioxp_xy_y5_history.json';
import actualY5Detail from '../fixtures/bioxp_xy_y5_detail.json';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { api } from '../../src/lib/api';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
const nativeMetadataMode = vi.hoisted(() => ({ enabled: false, receipts: false, mutations: false, protocols: false }));
import retainedHistory from '../fixtures/bioxp_retained_history.json';
import manualCatalogProducer from '../fixtures/bioxpManualCatalogProducer.json';
import zTargetProducer from '../fixtures/bioxp_z_target_producer.json';
import stopSourceProducer from '../fixtures/bioxp_stop_source_producer.json';
import { historyItem } from '../fixtures/bioxpHistory';

const completeDeckReceiptFixture = {
    schema_version: 'bioxp.operator_action_receipt.v2',
    command_id: 'deck-command-contract-1',
    action_id: 'oem.deck.move_to_location',
    status: 'completed',
    terminal: true,
    sequence: 42,
    method_id: null,
    ownership_generation: 7,
    expected_board_epoch_by_board: { '4': 2, '5': 8 },
    state_version: 3,
    status_path: '/operator/v2/actions/receipts/deck-command-contract-1',
    accepted_at: 0.5,
    queued_at: 1,
    dispatched_at: 1.5,
    finished_at: 2,
    terminal_receipt_id: 'deck-command-contract-1',
    completion_class: 'completed',
    physical_effect_verified: false,
    error: null,
    canonical_inputs: { target: 'LOC_OC', camera_offset: false },
    requested_values: { target: 'LOC_OC', camera_offset: false },
    effective_values: { target: 'LOC_OC', camera_offset: false },
    observed_values: {},
    raw_return_layers: {},
    controller_evidence: {},
    transport_artifacts: [],
    child_receipts: [],
    transitions: [],
    deck_movement: {
        target: 'LOC_OC',
        target_label: 'LOC_OC',
        source_branch: 'ordinary',
        resolved_location_id: 1,
        destination_catalog_revision: 'b'.repeat(64),
        position_table_revision: 'a'.repeat(64),
        authority_snapshot_digest: 'c'.repeat(64),
        complete_authority_digest: 'd'.repeat(64),
        plan_digest: 'e'.repeat(64),
        source_anchors: ['ClassControlInterface.moveTo:3691-3716'],
        delivery_attempted: true,
        controller_command_acknowledged: true,
        controller_completion_verified: true,
        hardware_postcondition_verified: true,
        semantic_state_committed: true,
        physical_observation_verified: false,
        transition_revision: 3,
        ambiguity_state: 'ambiguous',
        stages: [{
            order: 0,
            operation: 'ForceToHighHome',
            source_anchor: 'ClassControlInterface.btnLOC1_Click:1932-1959',
            resources: ['axis:z'],
            arguments: {},
            dependencies: [],
            terminal_state: 'completed',
            terminal_evidence: { controller_acknowledged: true },
        }, {
            order: 1,
            operation: 'StopDeck',
            source_anchor: 'operator_command_plane:deck_plan',
            resources: ['axis:x', 'axis:y'],
            arguments: {},
            dependencies: [0],
            terminal_state: 'stopped',
            terminal_evidence: { interrupt: 'stopped' },
        }, {
            order: 2,
            operation: 'AbortDeck',
            source_anchor: 'operator_command_plane:deck_plan',
            resources: ['axis:x', 'axis:y'],
            arguments: {},
            dependencies: [1],
            terminal_state: 'aborted',
            terminal_evidence: { interrupt: 'aborted' },
        }],
    },
} satisfies BioXpOperatorReceiptDetailV2;

const state = vi.hoisted(() => ({
    stableReset: vi.fn(),
    quickDashboardProps: undefined as Record<string, unknown> | undefined,
    admissionCalls: 0,
    catalogArgs: [] as unknown[][],
    methodReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown },
    methodHookArgs: [] as unknown[][],
    methodCallbacks: null as null | { onSuccess?: (receipt: Record<string, unknown>) => void },
    historyEnabled: false,
    pipetteProps: null as Record<string, unknown> | null,
    v1DashboardEnabled: null as boolean | null,
    v1CatalogEnabled: null as boolean | null,
    invokeCalls: [] as Array<Record<string, unknown>>,
    invokePending: false,
    invokeData: undefined as Record<string, unknown> | undefined,
    componentStopData: undefined as Record<string, unknown> | undefined,
    componentStopPending: false,
    componentStopCalls: [] as Array<Record<string, unknown>>,
    axisInvokePending: false,
    invokeVariables: undefined as { actionId: string } | undefined,
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
            items: [] as Array<Record<string, unknown>>,
        },
        error: null as unknown,
        isError: false,
        isLoading: false,
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
    xyCalls: [] as Array<Record<string, unknown>>,
    xyCallbacks: null as null | { onSuccess?: (receipt: Record<string, unknown>) => void },
    xyReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isError: false },
    interruptPending: false,
    softwarePending: false,
    interruptSlot: 0,
    connected: true,
    connectionReachable: null as boolean | null,
    statusError: false,
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

vi.mock('../../src/lib/bioxpClient', async (importOriginal) => {
    const real = await importOriginal<typeof import('../../src/lib/bioxpClient')>();
    return ({
    bioXpDeckRecoveryResolution: real.bioXpDeckRecoveryResolution,
    useBioXpWorkflowJobs: () => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() }),
    useBioXpWorkflowJob: () => ({ data: undefined, isError: false }),
    useSubmitBioXpProtocol: () => nativeMetadataMode.protocols ? real.useSubmitBioXpProtocol() : ({ isPending: false, mutateAsync: vi.fn() }),
    BIOXP_Y_RELATIVE_MIN_STEPS: -2_147_483_648,
    BIOXP_Y_RELATIVE_MAX_STEPS: 2_147_483_647,
    BIOXP_Y_ABSOLUTE_MIN_STEPS: -2_147_483_648,
    BIOXP_Y_ABSOLUTE_MAX_STEPS: 2_147_483_647,
    useBioXpStatus: () => {
        // Record the current render, not stale calls from earlier renders.
        state.receiptHookCalls = [];
        state.interruptSlot = 0;
        state.v2MutationHookCalls = 0;
        return {
        data: {
            connection: {
                active: state.connected,
                reachable: state.connectionReachable ?? state.connected,
                configured: true,
                runtime_ready: true,
                generation: state.connectionGeneration,
                freshness_budget_seconds: 1800,
                last_error: null,
            },
            mutation_access: { enabled: true },
        },
        error: state.statusError ? new Error('status read failed') : null,
        isError: state.statusError,
        };
    },
    useBioXpOperatorDashboard: (_generation: number, enabled: boolean) => {
        state.v1DashboardEnabled = enabled;
        return state.dashboard;
    },
    useBioXpOperatorDashboardV2: () => ({ ...state.v2Dashboard, data: { ...state.v2Dashboard.data, telemetry: state.dashboard.data } }),
    useBioXpOperatorControlCatalogV2: (...args: Parameters<typeof real.useBioXpOperatorControlCatalogV2>) => {
        state.catalogArgs.push(args);
        return nativeMetadataMode.enabled ? real.useBioXpOperatorControlCatalogV2(...args) : state.v2Catalog;
    },
    useBioXpOperatorMethodV1: (...args: unknown[]) => { state.methodHookArgs.push(args); return state.methodReceipt; },
    bioXpMethodV1IsTerminal: (method: { status?: string } | undefined) => !method?.status || ['completed', 'failed', 'interrupted', 'ambiguous', 'completed_partial', 'cleared'].includes(method.status),
    useBioXpOperatorReceiptV2: (commandId: string | null, generation: number, enabled: boolean) => {
        state.receiptHookCalls.push({ commandId, generation, enabled });
        if (nativeMetadataMode.receipts) return real.useBioXpOperatorReceiptV2(commandId, generation, enabled);
        const dashboardReceipt = [
            ...(catalogDashboard().active_commands as Array<Record<string, unknown>>),
            ...(catalogDashboard().latest_receipts as Array<Record<string, unknown>>),
        ].find((receipt) => receipt.command_id === commandId
            && ['oem.deck.move_to_location', 'oem.deck._mov_execution', 'oem.deck._finite_operation'].includes(String(receipt.action_id)));
        if (dashboardReceipt) return { data: dashboardReceipt, error: null, isStale: false };
        if (commandId?.startsWith('xy-') || commandId === coherentFailureProducer.compact.command_id || commandId === bmsMetadata.compact.command_id || commandId === actualY5.command_id || commandId === manualReport.command_id) return state.xyReceipt;
        if (commandId?.startsWith('deck-command-')) return state.deckReceipt;
        if (commandId?.startsWith('lifecycle-command-')) return state.lifecycleReceipt;
        if (commandId?.startsWith('z-command-')) return state.zReceipt;
        return state.yReceipt;
    },
    useBioXpOperatorActionHistory: (...args: unknown[]) => {
        state.historyCalls.push(args[2] as number);
        state.historyEnabled = args[1] as boolean;
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
        return enabled ? state.catalog : { data: undefined };
    },
    useBioXpOperatorActionAdmission: () => {
        state.admissionCalls += 1;
        return { data: { enabled: true, disabled_reason: null, dependencies: [] }, error: null };
    },
    useInvokeBioXpOperatorAction: (lane = 'normal') => lane === 'stop' ? ({
        data: state.componentStopData, error: null, isPending: state.componentStopPending,
        mutate: (payload: Record<string, unknown>) => state.componentStopCalls.push(payload), reset: state.stableReset,
    }) : ({
        data: state.invokeData,
        error: null,
        isPending: state.invokePending,
        variables: state.invokeVariables,
        mutate: (payload: Record<string, unknown>) => state.invokeCalls.push(payload),
        reset: state.stableReset,
    }),
    useInvokeBioXpOperatorActionV2: () => {
        if (nativeMetadataMode.mutations) return real.useInvokeBioXpOperatorActionV2();
        // Cockpit mounts lifecycle, axis, then XY mutations each render.
        const lifecycle = state.v2MutationHookCalls % 3 === 0;
        const xy = state.v2MutationHookCalls % 3 === 2;
        state.v2MutationHookCalls += 1;
        if (xy) return {
            data: undefined, error: null, isPending: false, reset: state.stableReset,
            mutate: (payload: Record<string, unknown>, callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void }) => {
                state.xyCalls.push(payload);
                state.xyCallbacks = callbacks ?? null;
            },
        };
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
                variables: state.yInvokeCalls.at(-1),
                error: state.yInvokeError,
                isPending: state.axisInvokePending,
                mutate: (
                    payload: Record<string, unknown>,
                    callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void; onError?: (error: unknown) => void },
                ) => {
                    state.yInvokeCalls.push(payload);
                    callbacks?.onSuccess?.(state.normalQueuedReceipt ?? {
                        command_id: `axis-command-${state.yInvokeCalls.length}`,
                        action_id: (payload.request as Record<string, unknown>).action_id,
                        status: 'completed', terminal: true,
                    });
                },
                reset: state.stableReset,
            };
    },
    useInvokeBioXpDeckActionV2: () => {
        const [submissions, setSubmissions] = React.useState<any[]>([]);
        return {
            data: undefined, error: state.deckInvokeError, isPending: state.deckInvokePending,
            submissions, retire: () => {},
            submit: (request: Record<string, unknown>) => {
                state.deckInvokeCalls.push({ request });
                const item = { request, state: 'submitting' };
                setSubmissions(items => [...items, item]);
                state.deckCallbacks = {
                    onSuccess: receipt => setSubmissions(items => items.map(row => row.request === request
                        ? { ...row, state: 'accepted', receipt } : row)),
                    onError: error => setSubmissions(items => items.map(row => row.request === request
                        ? { ...row, state: 'uncertain', error, commandId: (error as any)?.response?.data?.detail?.command_id } : row)),
                };
                if (state.deckInvokeError) state.deckCallbacks.onError?.(state.deckInvokeError);
                else if (!state.deckDeferred) state.deckCallbacks.onSuccess?.({
                    command_id: 'deck-command-mounted-1', action_id: 'oem.deck.move_to_location', status: 'queued', terminal: false,
                });
            },
            reset: state.stableReset,
        };
    },
    bioXpPostDispatchCommandIdentity: (error: unknown) => {
        const detail = (error as { response?: { data?: { detail?: Record<string, unknown> } } })?.response?.data?.detail;
        return typeof detail?.command_id === 'string'
            ? { commandId: detail.command_id, statusPath: detail.status_path, retryGuidance: detail.retry_guidance }
            : null;
    },
    useInterruptBioXpOperatorActionV1: () => ({
        data: state.yInterruptData,
        error: state.yInterruptError,
        isPending: (++state.interruptSlot === 4 && state.softwarePending) || state.interruptPending,
        mutate: (payload: Record<string, unknown>) => state.yInterruptCalls.push(payload),
        reset: state.stableReset,
    }),
    useSubmitBioXpOperatorMethodV1: () => ({
        data: undefined,
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>, callbacks?: { onSuccess?: (receipt: Record<string, unknown>) => void }) => { state.methodCalls.push(payload); state.methodCallbacks = callbacks ?? null; },
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
    });
});

vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: (props: Record<string, unknown>) => <output data-testid="camera-session">{JSON.stringify(props)}</output> }));
vi.mock('../../src/components/BioXpOperatorControlTabs', () => ({ BioXpOperatorControlTabs: () => null }));
vi.mock('../../src/components/BioXpPipetteControlPanel', () => ({ BioXpPipetteControlPanel: (props: Record<string, unknown>) => { state.pipetteProps = props; return null; } }));
vi.mock('../../src/components/BioXpQuickDashboard', () => ({ BioXpQuickDashboard: (props: Record<string, unknown>) => { state.quickDashboardProps = props; return null; } }));
vi.mock('../../src/components/BioXpOperatorReports', () => ({ BioXpOperatorReports: () => null }));

import { BioXpCockpit } from '../../src/components/BioXpCockpit';

const initialV2Dashboard = structuredClone(state.v2Dashboard.data);
const catalogDashboard = () => state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data;

describe('critical evidence presentation', () => {
    beforeEach(() => {
        Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now() });
        (state.v2Catalog.data!.dashboard as Record<string, unknown>).generated_at = Date.now() / 1000;
    });
    it('shows the empty ledger only after a successful empty history read', async () => {
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.textContent).toContain('No robot action receipts recorded.');
    });
    it('explains expired authority and does not renew it from cache receipt time', async () => {
        (state.v2Catalog.data!.dashboard as Record<string, unknown>).telemetry = state.dashboard.data;
        (state.v2Catalog.data!.dashboard as Record<string, unknown>).generated_at = Date.now() / 1000 - 16;
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.textContent).toContain('Robot state is missing or stale');
        expect(container.textContent).not.toContain('Updating');
    });
    it.each(['local', 'upstream'])('expires %s authority on the clock while a refresh is pending', async (clock) => {
        vi.useFakeTimers();
        const started = Date.now();
        // Publish a normal admitted XY action, not merely an absent/disabled button.
        (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).push({
            action_id: 'oem.xy.move_absolute',
            request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: false,
            enabled: true,
            disabled_reason: null,
        });
        Object.assign(state.v2Catalog, { dataUpdatedAt: started, isFetching: true });
        const dashboard = state.v2Catalog.data!.dashboard as Record<string, unknown>;
        dashboard.generated_at = started / 1000;
        dashboard.telemetry = state.dashboard.data;
        try {
            await act(async () => root.render(<BioXpCockpit />));
            expect(container.textContent).not.toContain('Robot state is missing or stale');
            const move = [...container.querySelectorAll('button')].find(button => button.textContent === 'Move X + Y together')!;
            expect(move).toBeDefined();
            expect(move.disabled).toBe(false);
            await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
            expect(container.textContent).not.toContain('Robot state is missing or stale');
            expect(move.disabled).toBe(false);
            // Keep the opposite clock fresh; each budget must independently expire.
            if (clock === 'upstream') Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now() });
            else dashboard.generated_at = Date.now() / 1000;
            await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
            expect(container.textContent).toContain('Robot state is missing or stale');
            expect(container.textContent).toContain('MotionUnknown —');
            expect(move.isConnected).toBe(true);
            expect(move.disabled).toBe(true);
            expect(state.quickDashboardProps?.data).toBe(state.dashboard.data);
            expect(state.quickDashboardProps?.stale).toBe(true);
            expect(state.v2Catalog).toMatchObject({ isFetching: true, error: null, isStale: false });
            expect(Date.now() - (clock === 'local'
                ? (state.v2Catalog as typeof state.v2Catalog & { dataUpdatedAt: number }).dataUpdatedAt
                : Number(dashboard.generated_at) * 1000)).toBe(15_000);
            expect(Date.now() - (clock === 'local'
                ? Number(dashboard.generated_at) * 1000
                : (state.v2Catalog as typeof state.v2Catalog & { dataUpdatedAt: number }).dataUpdatedAt)).toBe(1_000);
            await act(async () => move.click());
            expect(state.lifecycleInvokeCalls).toHaveLength(0);
            expect(state.yInvokeCalls).toHaveLength(0);
            expect(state.yInterruptCalls).toHaveLength(0);
            expect(state.xyCalls).toHaveLength(0);
        } finally {
            Object.assign(state.v2Catalog, { isFetching: false });
            vi.useRealTimers();
        }
    });
    it('does not claim no receipts when history failed', async () => {
        state.history.isError = true;
        state.history.error = new Error('invalid operator-control contract');
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.textContent).toContain('Robot action history unavailable');
        expect(container.textContent).not.toContain('No robot action receipts recorded.');
    });
    it('does not claim no receipts while history is loading', async () => {
        state.history.isLoading = true;
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.textContent).toContain('Loading robot action receipts');
        expect(container.textContent).not.toContain('No robot action receipts recorded.');
    });
    it('explains null telemetry instead of indefinite Updating', async () => {
        (state.v2Catalog.data!.dashboard as Record<string, unknown>).telemetry = null;
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.textContent).toContain('Robot did not report telemetry');
        expect(container.textContent).not.toContain('Updating');
    });
    it('puts the bounded failure summary above on-demand evidence', async () => {
        state.history.data.items = [historyItem({
            command_id: 'failed-y', action_id: 'oem.y.home', status: 'failed',
            finished_at: null, stage_receipts: [], error: 'Controller position wait timed out; inspect retained board/axis/position evidence.',
            response: { http_status: 200, body: { ok: false, failure: 'RuntimeError: Reach GZ position time out! board=4; axis=0; position=10000' } },
        })];
        await act(async () => root.render(<BioXpCockpit />));
        const article = [...container.querySelectorAll('article')].find(node => node.textContent?.includes('failed-y') || node.textContent?.includes('oem.y.home'))!;
        expect([...article.querySelectorAll('p')].some(p => p.textContent?.includes('Controller position wait timed out'))).toBe(true);
        expect(article.textContent).toContain('Physical effect unverified');
    });
});

describe('primary cockpit query ownership', () => {
    it('fetches the primary catalog and history without opening Advanced', async () => {
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(state.v1CatalogEnabled).toBe(true);
        expect(state.historyEnabled).toBe(true);
        const x = [...container.querySelectorAll('article')].find(node => node.textContent?.includes('X Axis'))!;
        expect([...x.querySelectorAll('button')].find(node => node.textContent === 'Move +')?.disabled).toBe(false);
    });
    it('preserves published emergency and pipette admission with Advanced closed', async () => {
        const emergency = { ...xHomeAction(), action_id: 'meta.emergency_stop', enabled: true };
        const pipette = { ...xHomeAction(), action_id: 'pipette.connect', enabled: false, disabled_reason: 'Robot refused' };
        state.catalog.data.actions.push(emergency, pipette);
        await act(async () => { root.render(<BioXpCockpit />); });
        const emergencyButton = () => [...container.querySelectorAll('button')].find(node => node.textContent === 'Software Abort (cancel waiters)')!;
        expect(emergencyButton().disabled).toBe(false);
        const disclosure = [...container.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === 'Pipette controls')!;
        await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); });
        expect(state.pipetteProps?.connected).toBe(true);
        expect(state.pipetteProps?.actions).toContain(pipette);
        expect(pipette.enabled).toBe(false);
        emergency.enabled = false;
        await act(async () => { root.render(<BioXpCockpit />); });
        // Aggregate interrupt admission is independent of the V1 catalog.
        expect(emergencyButton().disabled).toBe(false);
        state.interruptPending = true;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(emergencyButton().disabled).toBe(true);
        expect([...container.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === 'Advanced Full Command Catalog')?.open).toBe(false);
    });
    it.each(['interrupt', 'lifecycle', 'axis', 'deck'] as const)('blocks XYZ entrypoints during pending %s without trusting stale-enabled rows', async (pending) => {
        if (pending === 'interrupt') state.interruptPending = true;
        if (pending === 'lifecycle') state.lifecycleInvokePending = true;
        if (pending === 'axis') state.axisInvokePending = true;
        if (pending === 'deck') state.deckInvokePending = true;
        await act(async () => root.render(<BioXpCockpit />));
        for (const axis of ['X', 'Y', 'Z']) {
            const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis} Axis`)!;
            for (const label of ['Move +', 'Move −', 'Go absolute', 'Home']) {
                const control = [...panel.querySelectorAll('button')].find(node => node.textContent === label)!;
                expect(control, `${axis} ${label}`).toBeDefined();
                expect(control.disabled, `${axis} ${label}`).toBe(true);
                await act(async () => control.click());
            }
            const stop = [...panel.querySelectorAll('button')].find(node => node.textContent === 'Stop')!;
            expect(stop.disabled).toBe(pending === 'interrupt');
        }
        expect(state.yInvokeCalls).toHaveLength(0);
    });

    it.each(['X', 'Y', 'Z'])('labels a rejected %s request by its submitted axis without retrying', async axis => {
        const render = () => act(async () => root.render(<BioXpCockpit />));
        await render();
        const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis} Axis`)!;
        const move = [...panel.querySelectorAll('button')].find(node => node.textContent === 'Go absolute')!;
        expect(move.disabled).toBe(false);
        await act(async () => move.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: `oem.${axis.toLowerCase()}.move_absolute` } });
        state.yInvokeError = { response: { status: 409, data: { detail: {
            code: 'operator_action_busy', message: 'A normal action is active; observe its receipt before submitting another.',
        } } } };
        await render();
        expect(container.textContent).toContain(axis === 'Y' ? 'Y enqueue failed' : `${axis} command failed`);
        expect(container.textContent).toContain('operator_action_busy');
        if (axis !== 'Y') expect(container.textContent).not.toContain('Y enqueue failed');
        await render();
        expect(state.yInvokeCalls).toHaveLength(1);
    });

    it('renders real manual producer admission transitions without submitting motion', async () => {
        // Produced by robot testdata/tmcl/test_gripper_door_offline.py through
        // the actual catalog and admission routes. Reference establishment is
        // a synthetic input here, not a physical Home acceptance claim.
        const control = (panelName: string, label: string) => {
            const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === panelName)!;
            return [...panel.querySelectorAll('button')].find(button => button.textContent === label)!;
        };
        for (const phase of ['unarmed', 'armed', 'referenced', 'armed', 'referenced'] as const) {
            state.catalog.data.actions = structuredClone(manualCatalogProducer[phase]);
            await act(async () => root.render(<BioXpCockpit />));
            for (const label of ['Open', 'Close']) {
                expect(control('Gripper', label).disabled, `${phase} G ${label}`).toBe(phase === 'unarmed');
                expect(control('Thermal Door', label).disabled, `${phase} D ${label}`).toBe(phase !== 'referenced');
            }
        }
        expect(state.invokeCalls).toHaveLength(0);
        expect(state.componentStopCalls).toHaveLength(0);
    });

    it.each(stopSourceProducer)('renders real acknowledged Z Stop as completed without claiming physical stopping (armed=$armed, first=$first_ack)', async (producer) => {
        state.yInterruptData = producer.mutation;
        await act(async () => root.render(<BioXpCockpit />));
        const outcome = Array.from(container.querySelectorAll('[role="status"]'))
            .find((node) => node.textContent?.startsWith('Z STOP · completed'));
        expect(outcome).toBeDefined();
        expect(outcome?.textContent).toContain('Source call completed: yes · Source return OK: yes');
        expect(outcome?.textContent).toContain('Controller stop ACK: yes · Controller terminal state verified: no · Physical effect unverified');
        expect(outcome?.textContent).toContain('Receipt persistence: committed');
        expect(outcome?.textContent).not.toContain('Outcome or persistence unresolved');
        const retained = JSON.parse(outcome!.querySelector('pre')!.textContent!);
        expect(retained.interrupt_evidence.first_stop_acknowledged).toBe(producer.first_ack);
        expect(retained.interrupt_evidence.second_stop_acknowledged).toBe(true);
        expect(state.yInterruptCalls).toEqual([]);
        expect(state.invokeCalls).toEqual([]);
    });

    it('keeps addressed gripper Stop independent and retains normal plus stop receipts', async () => {
        state.catalog.data.actions.push(
            { ...xMoveAction(), action_id: 'gripper-open', informational_path: '/motion/gripper/open', safety_class: 'motion', enabled: true },
            { ...xMoveAction(), action_id: 'component-stop', informational_path: '/motion/diagnostics/stop', safety_class: 'stop', enabled: true },
        );
        const render = async () => act(async () => root.render(<BioXpCockpit />));
        await render();
        const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === 'Gripper')!;
        const control = (label: string) => [...panel.querySelectorAll('button')].find(button => button.textContent === label)!;
        await act(async () => control('Open').click());
        expect(state.invokeCalls).toHaveLength(1);
        state.invokePending = true;
        await render();
        expect(control('Stop').disabled).toBe(false);
        await act(async () => control('Stop').click());
        expect(state.componentStopCalls).toEqual([{ actionId: 'component-stop', connectionGeneration: 1, ownershipGeneration: 1, inputs: { axis: 'g' } }]);
        expect(state.invokeCalls).toHaveLength(1);
        state.componentStopData = { command_id: 'g-stop-receipt', action_id: 'component-stop', status: 'completed' };
        await render();
        expect(container.querySelector('[data-testid="component-stop-receipt"]')?.textContent).toContain('g-stop-receipt');
        expect(state.invokePending).toBe(true);
        state.invokeData = { ...xReceipt('completed'), command_id: 'g-normal-receipt', action_id: 'gripper-open' };
        state.invokePending = false;
        await render();
        expect(container.textContent).toContain('g-normal-receipt');
        expect(container.textContent).toContain('g-stop-receipt');
        state.componentStopPending = true;
        await render();
        expect(control('Stop').disabled).toBe(true);
    });

    it('does not convert an explicitly read-only pending query into an axis lock', async () => {
        state.invokePending = true;
        state.invokeVariables = { actionId: 'read-only-fixture' };
        state.catalog.data.actions.push({ action_id: 'read-only-fixture', safety_class: 'read_only' });
        await act(async () => root.render(<BioXpCockpit />));
        for (const axis of ['X', 'Y', 'Z']) {
            const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis} Axis`)!;
            expect([...panel.querySelectorAll('button')].find(node => node.textContent === 'Move +')!.disabled).toBe(false);
        }
    });

    it('rejects a late XY submission callback after generation replacement', async () => {
        await act(async () => { root.render(<BioXpCockpit />); });
        await act(async () => { (container.querySelector('[data-testid="serial206-xy-oem-panel"] button') as HTMLButtonElement).click(); });
        const callback = state.xyCallbacks;
        expect(callback).not.toBeNull();
        state.connectionGeneration = 2;
        await act(async () => { root.render(<BioXpCockpit />); });
        await act(async () => { callback?.onSuccess?.({ command_id: 'xy-old-generation', status: 'dispatched', terminal: false }); });
        expect(state.receiptHookCalls[4]).toEqual({ commandId: null, generation: 2, enabled: true });
        expect(container.textContent).not.toContain('old-generation');
    });
    it('keeps catalog identity stable during dashboard freshness changes but blocks normal controls', async () => {
        await act(async () => { root.render(<BioXpCockpit />); });
        const original = state.catalogArgs.at(-1);
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(state.catalogArgs.at(-1)).toEqual(original);
        expect((container.querySelector('[data-testid="serial206-xy-oem-panel"] button') as HTMLButtonElement).disabled).toBe(true);
    });
    it.each(['status-error', 'unreachable'])('retains XY reconciliation identity during %s while normal motion stays blocked', async (fault) => {
        await act(async () => { root.render(<BioXpCockpit />); });
        const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
        const button = () => panel().querySelector('button') as HTMLButtonElement;
        await act(async () => { button().click(); state.xyCallbacks?.onSuccess?.({ command_id: 'xy-retained', status: 'dispatched', terminal: false }); });
        if (fault === 'status-error') state.statusError = true;
        else state.connectionReachable = false;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(state.receiptHookCalls).toContainEqual({ commandId: 'xy-retained', generation: 1, enabled: true });
        expect(panel().textContent).toContain('XY command pending');
        expect(button().disabled).toBe(true);
        state.xyReceipt = { data: { command_id: 'xy-retained', status: 'failed', terminal: true }, error: null, isError: false };
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(panel().textContent).toContain('XY command failed');
        expect(button().disabled).toBe(true);
        state.statusError = false;
        state.connectionReachable = true;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(panel().textContent).toContain('XY command failed');
        expect(state.xyCalls).toHaveLength(1);
    });
    it('polls actual strict-BMS native metadata with the production hook after failed XY, without retry or activation', async () => {
        vi.useFakeTimers();
        // No payload timestamps/authority are rewritten. Run at native export time;
        // repeated identical responses must eventually expire, not renew authority.
        vi.setSystemTime(bmsMetadata.catalog.dashboard.generated_at * 1000);
        nativeMetadataMode.enabled = true;
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        vi.mocked(api.get).mockReset();
        vi.mocked(api.post).mockReset();
        vi.mocked(api.get).mockImplementation(async (url) => {
            if (url === '/api/bioxp/calibration-settings') throw new Error('offline calibration fixture unavailable');
            expect(url).toBe('/api/bioxp/operator-controls/v2/catalog');
            return { data: structuredClone(bmsMetadata.catalog) };
        });
        const render = () => act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
        const advance = async (ms: number) => {
            await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
            await act(async () => { await vi.advanceTimersByTimeAsync(1); });
        };
        const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
        const button = () => panel().querySelector('button') as HTMLButtonElement;
        try {
            await render(); await advance(1);
            expect(button().disabled).toBe(false);
            // Only the original submission callback is a transport fixture.
            await act(async () => {
                button().click();
                state.xyCallbacks?.onSuccess?.({ command_id: bmsMetadata.compact.command_id, status: 'dispatched', terminal: false });
            });
            expect(button().disabled).toBe(true);
            state.xyReceipt = { data: structuredClone(bmsMetadata.compact), error: null, isError: false };
            await render();
            for (let poll = 0; poll < 3; poll++) {
                if (poll) await advance(5000);
                expect(panel().textContent).toContain('Move timeout reported');
                expect(button().disabled).toBe(false);
                expect(state.xyReceipt.data).toEqual(bmsMetadata.compact);
            }
            expect(vi.mocked(api.get).mock.calls.filter(([url]) => url === '/api/bioxp/operator-controls/v2/catalog')).toHaveLength(3);
            expect(vi.mocked(api.get).mock.calls.filter(([url]) => url === '/api/bioxp/calibration-settings')).toHaveLength(1);
            await advance(6000);
            expect(button().disabled).toBe(true); // same old producer observation expires
            expect(panel().textContent).toContain('Move timeout reported');
            expect(state.xyCalls).toHaveLength(1);
            expect(state.lifecycleInvokeCalls).toHaveLength(0);
            expect(state.yInterruptCalls).toHaveLength(0);
            expect(state.invokeCalls).toHaveLength(0);
            expect(api.post).not.toHaveBeenCalled();
        } finally {
            await act(async () => root.render(null));
            client.clear(); nativeMetadataMode.enabled = false; vi.useRealTimers();
        }
    });

    it.each(['eligible', 'unknown', 'interrupted'] as const)('keeps failed XY truthful and follows fresh producer %s authority without retry', async (authority) => {
        vi.useFakeTimers();
        try {
            const render = () => act(async () => root.render(<BioXpCockpit />));
            const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
            const button = () => panel().querySelector('button') as HTMLButtonElement;
            await render();
            const commandId = coherentFailureProducer.compact.command_id;
            await act(async () => { button().click(); state.xyCallbacks?.onSuccess?.({ command_id: commandId, status: 'dispatched', terminal: false }); });
            expect(button().disabled).toBe(true);
            // Raw native → SQLite export; admission metadata below remains the
            // existing contract fixture, not a producer catalog compatibility claim.
            state.xyReceipt = { data: structuredClone(coherentFailureProducer.compact), error: null, isError: false };
            state.statusError = true;
            await render();
            expect(panel().textContent).toContain('Move timeout reported');
            expect(button().disabled).toBe(true);
            state.statusError = false;
            const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(row => row.action_id === 'oem.xy.move_absolute')!;
            action.enabled = authority === 'eligible';
            action.disabled_reason = authority === 'eligible' ? null : `producer ${authority}: reconciliation required`;
            for (let poll = 0; poll < 3; poll++) {
                await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
                Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now() });
                catalogDashboard().generated_at = Date.now() / 1000;
                await render();
                expect(panel().textContent).toContain('Move timeout reported');
                expect(button().disabled).toBe(authority !== 'eligible');
                expect(state.xyCalls).toHaveLength(1);
                expect(state.lifecycleInvokeCalls).toHaveLength(0);
                expect(state.yInterruptCalls).toHaveLength(0);
                expect(state.invokeCalls).toHaveLength(0);
            }
            state.connectionGeneration = 2;
            await render();
            expect(panel().textContent).not.toContain(commandId);
            expect(state.xyCalls).toHaveLength(1);
        } finally { vi.useRealTimers(); }
    });

    it('reopens the actual saved Y5 in Recent Actions with plain text and zero submissions', async () => {
        const row = structuredClone(actualY5History.items.find(row => row.command_id === actualY5.command_id)!);
        expect(row.xy_failure).toBeNull();
        state.history.data.items = [row];
        const render = () => act(async () => root.render(<BioXpCockpit />));
        const card = () => [...container.querySelectorAll('article')].find(node => node.querySelector('strong')?.textContent === actualY5.action_id)!;
        await render();
        expect(card().textContent).toContain('Robot route reported an HTTP conflict.');
        expect(card().textContent).not.toContain('Recorded stopped position');
        await act(async () => {
            const disclosure = card().querySelector('details')!;
            disclosure.open = true; disclosure.dispatchEvent(new Event('toggle'));
        });
        state.xyReceipt = { data: structuredClone(actualY5Detail), error: null, isError: false };
        for (const phase of ['fresh', 'stale', 'unknown']) {
            if (phase === 'stale') catalogDashboard().generated_at = Date.now() / 1000 - 20;
            if (phase === 'unknown') state.statusError = true;
            await render();
            expect([...card().querySelectorAll('p')].map(node => node.textContent).join(' ')).toContain('Move timeout reported. Recorded stopped position: X85000, Y5 (requested X85000, Y0). Past receipt only; not current position or readiness. Source result remains failed.');
            expect(card().querySelector('details')!.open).toBe(true);
            expect(row.status).toBe('failed');
            expect(row.xy_failure).toBeNull();
            expect(state.xyCalls).toHaveLength(0);
            expect(state.lifecycleInvokeCalls).toHaveLength(0);
            expect(state.yInterruptCalls).toHaveLength(0);
            expect(state.invokeCalls).toHaveLength(0);
            expect(state.componentStopCalls).toHaveLength(0);
        }
    });

    it.each(['eligible', 'unknown', 'interrupted'] as const)('plain actual saved Y5 reporting stays historical through fresh/stale/%s/generation changes', async (authority) => {
        vi.useFakeTimers();
        try {
            const render = () => act(async () => root.render(<BioXpCockpit />));
            const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
            const button = () => panel().querySelector('button') as HTMLButtonElement;
            const visible = () => [...panel().querySelectorAll('p')].map(node => node.textContent).join(' ');
            const explanation = 'Move timeout reported. Recorded stopped position: X85000, Y5 (requested X85000, Y0). Past receipt only; not current position or readiness. Source result remains failed.';
            await render();
            await act(async () => { button().click(); state.xyCallbacks?.onSuccess?.({ command_id: actualY5.command_id, status: 'dispatched', terminal: false }); });
            state.xyReceipt = { data: structuredClone(actualY5), error: null, isError: false };
            const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(row => row.action_id === 'oem.xy.move_absolute')!;
            action.enabled = authority === 'eligible';
            action.disabled_reason = authority === 'eligible' ? null : `producer ${authority}: reconciliation required`;
            for (let poll = 0; poll < 3; poll++) {
                await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
                Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now() });
                catalogDashboard().generated_at = Date.now() / 1000;
                await render();
                expect(visible()).toContain('Move timeout reported');
                expect(visible()).toContain(explanation); // outside closed raw JSON details
                expect(button().disabled).toBe(authority !== 'eligible');
                expect(state.xyReceipt.data).toEqual(actualY5);
            }
            catalogDashboard().generated_at = Date.now() / 1000 - 20;
            await render();
            expect(button().disabled).toBe(true);
            expect(visible()).toContain(explanation);
            state.statusError = true;
            await render();
            expect(button().disabled).toBe(true);
            expect(visible()).toContain(explanation);
            state.statusError = false;
            catalogDashboard().generated_at = Date.now() / 1000;
            await render();
            expect(button().disabled).toBe(authority !== 'eligible');
            state.connectionGeneration = 2;
            await render();
            expect(visible()).not.toContain(explanation);
            expect(panel().textContent).not.toContain(actualY5.command_id);
            expect(state.xyCalls).toHaveLength(1);
            expect(state.lifecycleInvokeCalls).toHaveLength(0);
            expect(state.yInterruptCalls).toHaveLength(0);
            expect(state.invokeCalls).toHaveLength(0);
            expect(state.componentStopCalls).toHaveLength(0);
        } finally { vi.useRealTimers(); }
    });

    it('renders the actual manual API report without a false success or retry across polling', async () => {
        vi.useFakeTimers();
        try {
            const render = () => act(async () => root.render(<BioXpCockpit />));
            const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
            const button = () => panel().querySelector('button') as HTMLButtonElement;
            const visible = () => [...panel().querySelectorAll('p')].map(node => node.textContent).join(' ');
            await render();
            await act(async () => { button().click(); state.xyCallbacks?.onSuccess?.({command_id:manualReport.command_id,status:'dispatched',terminal:false}); });
            state.xyReceipt = {data:structuredClone(manualReport),error:null,isError:false};
            for (let poll=0;poll<3;poll++) {
                await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
                Object.assign(state.v2Catalog,{dataUpdatedAt:Date.now()});
                catalogDashboard().generated_at=Date.now()/1000;
                await render();
                expect(visible()).toContain('Move timeout reported');
                expect(visible()).not.toMatch(/XY command (completed|failed)/);
                expect(visible()).toContain('Source result remains failed');
                expect(button().disabled).toBe(false);
                expect(state.xyCalls).toHaveLength(1);
                expect(state.lifecycleInvokeCalls).toHaveLength(0);
                expect(state.yInterruptCalls).toHaveLength(0);
                expect(state.xyReceipt.data).toEqual(manualReport);
            }
            state.statusError=true;await render();
            expect(button().disabled).toBe(true);
            expect(visible()).toContain('Move timeout reported');
        } finally {vi.useRealTimers();}
    });

    it.each(['completed', 'failed', 'interrupted', 'stopped', 'ambiguous'])('follows XY to %s and ignores other identities', async (status) => {
        await act(async () => { root.render(<BioXpCockpit />); });
        const panel = () => container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
        const button = () => panel().querySelector('button') as HTMLButtonElement;
        await act(async () => { button().click(); state.xyCallbacks?.onSuccess?.({ command_id: 'xy-one', status: 'queued', terminal: false }); });
        expect(state.xyCalls).toHaveLength(1);
        const assertAxesHeld = () => {
            for (const axis of ['X', 'Y', 'Z']) {
                const axisPanel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis} Axis`)!;
                expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Go absolute')!.disabled).toBe(true);
                expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Home')!.disabled).toBe(true);
                expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Stop')!.disabled).toBe(false);
                if (axis === 'Z') expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Z Clear (automatic position)')!.disabled).toBe(true);
            }
        };
        assertAxesHeld();
        expect(state.receiptHookCalls).toContainEqual({ commandId: 'xy-one', generation: 1, enabled: true });
        expect(button().disabled).toBe(true);
        state.xyReceipt.data = { command_id: 'xy-other', status: 'completed', terminal: true };
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(button().disabled).toBe(true);
        state.xyReceipt.error = new Error('status unavailable');
        state.xyReceipt.isError = true;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(panel().textContent).toContain('Do not retry');
        assertAxesHeld();
        state.xyReceipt = { data: { command_id: 'xy-one', status, terminal: true }, error: null, isError: false };
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(panel().textContent).toContain(status === 'ambiguous' ? 'XY command pending · ambiguous' : `XY command ${status}`);
        expect(button().disabled).toBe(status === 'ambiguous');
        if (status === 'ambiguous') {
            assertAxesHeld();
            expect(panel().textContent).toContain('Do not retry');
            await act(async () => button().click());
            expect(state.xyCalls).toHaveLength(1);
        }
        state.connectionGeneration = 2;
        await act(async () => { root.render(<BioXpCockpit />); });
        expect(panel().textContent).not.toContain('xy-one');
        expect(state.receiptHookCalls[4]).toEqual({ commandId: null, generation: 2, enabled: true });
    });
});

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
    state.history.error = null;
    state.history.isError = false;
    state.history.isLoading = false;
    state.admissionCalls = 0;
    state.catalogArgs = [];
    state.methodHookArgs = [];
    state.methodReceipt = { data: undefined, error: null };
    state.methodCallbacks = null;
    state.v1DashboardEnabled = null;
    state.v1CatalogEnabled = null;
    state.connectionGeneration = 1;
    state.invokeCalls = [];
    state.invokeData = undefined;
    state.componentStopData = undefined;
    state.componentStopPending = false;
    state.componentStopCalls = [];
    state.invokePending = false;
    state.axisInvokePending = false;
    state.invokeVariables = undefined;
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
    state.xyCalls = [];
    state.xyCallbacks = null;
    state.xyReceipt = { data: undefined, error: null, isError: false };
    state.interruptPending = false;
    state.softwarePending = false;
    state.connected = true;
    state.connectionReachable = null;
    state.statusError = false;
    state.deckInvokeError = null;
    state.deckInvokePending = false;
    state.deckDeferred = false;
    state.deckCallbacks = null;
    state.receiptHookCalls = [];
    state.yInterruptError = null;
    state.yInvokeData = undefined;
    state.normalQueuedReceipt = undefined;
    nativeMetadataMode.mutations = false;
    nativeMetadataMode.receipts = false;
    state.yInterruptData = undefined;
    state.yReceipt.data = undefined;
    state.yReceipt.error = null;
    state.zReceipt.data = undefined;
    state.zReceipt.error = null;
    state.lifecycleReceipt.data = undefined;
    state.lifecycleReceipt.error = null;
    state.deckReceipt.data = undefined;
    state.deckReceipt.error = null;
    state.v2Dashboard.data = structuredClone(initialV2Dashboard);
    state.v2Dashboard.error = null;
    state.v2Dashboard.isStale = false;
    state.v2Dashboard.data.ownership_generation = 1;
    state.v2Dashboard.data.board4.state_version = 3;
    state.v2Dashboard.data.y_axis.ownership_generation = 1;
    state.v2Dashboard.data.y_axis.state_version = 4;
    state.v2Dashboard.data.y_axis.latest_compact_receipt = null;
    state.v2Dashboard.data.active_commands = [];
    state.v2Dashboard.data.latest_receipts = [];
    state.v2Catalog.error = null;
    state.v2Catalog.isStale = false;
    state.v2Catalog.data = {
        schema_version: 'bioxp.operator_control_catalog.v2',
        dashboard: structuredClone(state.v2Dashboard.data),
        actions: [
            'oem.xy.move_absolute',
            'oem.xy.home',
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
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: true,
            enabled: true,
            disabled_reason: null,
        }))),
    };
    Object.assign(state.v2Dashboard.data, {
        deck: {
            current_location: 'LOC_TC',
            current_well: 2,
            position_table_revision: 'a'.repeat(64),
            destination_catalog_revision: 'b'.repeat(64),
            semantic_state_revision: 17,
            ambiguity_state: 'none',
        },
    });
    // Admission comes from one fresh catalog observation, not the retired
    // separately polled dashboard. Publish both clocks explicitly per test.
    Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now(), isFetching: false });
    state.v2Dashboard.data.generated_at = Date.now() / 1000;
    state.v2Catalog.data.dashboard = { ...structuredClone(state.v2Dashboard.data), telemetry: state.dashboard.data };
    (state.v2Catalog.data.actions as Array<Record<string, unknown>>).push({
        action_id: 'oem.deck.move_to_location',
        request_schema_version: 'bioxp.operator_action_request.v2',
        response_schema_version: 'bioxp.operator_action_receipt.v2',
        interrupt: false,
        enabled: true,
        disabled_reason: null,
        destination_catalog_revision: 'b'.repeat(64),
        position_table_revision: 'a'.repeat(64),
        required_boards: [4, 5],
        expected_board_epoch_by_board: { '4': 2, '5': 8 },
        required_references: ['x', 'y', 'z', 'g'],
        destination_options: [
            { target: 'LOC_TC', label: 'Thermal Cycler', aliases: ['Thermal Cycler'], location_id: 2, branch_kind: 'ordinary', camera_offset_option: true, source_anchors: ['ClassControlInterface.moveTo:3691-3716'], enabled: true, disabled_reason: null },
            { target: 'LOC_OC', label: 'Output Chiller', aliases: ['OC chiller', 'Output Chiller', 'Output Tray'], location_id: 1, branch_kind: 'ordinary', camera_offset_option: true, source_anchors: ['ClassControlInterface.moveTo:3691-3716'], enabled: true, disabled_reason: null },
        ],
    });
    state.history.data.items = [];
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

describe('L3 rapid submission and receipt reconciliation', () => {
    beforeEach(() => { vi.mocked(api.post).mockReset(); vi.mocked(api.get).mockReset(); });
    const settle = async () => { await new Promise(resolve => setTimeout(resolve, 25)); };
    const moveFor = (axis: string) => [...container.querySelectorAll('article')]
        .find(node => node.querySelector('h3')?.textContent === `${axis.toUpperCase()} Axis`)!
        .querySelectorAll<HTMLButtonElement>('button');
    const positive = (axis: string) => [...moveFor(axis)].find(button => button.textContent === 'Move +')!;

    it.each(['x', 'y', 'z'])('L3 reserves %s synchronously, drops rapid cross-axis clicks and preserves a real 409 without retry', async axis => {
        nativeMetadataMode.mutations = true;
        let reject!: (reason: unknown) => void;
        vi.mocked(api.post).mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
        const client = new QueryClient({ defaultOptions: { mutations: { retry: 3 }, queries: { retry: false } } });
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            await act(async () => {
                positive(axis).click(); positive(axis).click(); positive(axis === 'x' ? 'y' : 'x').click();
                await settle();
            });
            expect(api.post).toHaveBeenCalledTimes(1);
            expect(vi.mocked(api.post).mock.calls[0][0]).toContain(`oem.${axis}.move_steps`);
            expect(positive(axis).disabled).toBe(true);
            expect([...moveFor(axis)].find(button => button.textContent === 'Stop')!.disabled).toBe(false);
            await act(async () => {
                reject({ response: { status: 409, data: { detail: { error: 'operator_action_busy' } } } });
                await settle();
            });
            expect(container.textContent).toContain(`${axis === 'y' ? 'Y enqueue' : `${axis.toUpperCase()} command`} failed · HTTP 409`);
            expect(positive(axis).disabled).toBe(false);
            await act(async () => { await settle(); });
            expect(api.post).toHaveBeenCalledTimes(1);
        } finally { await act(async () => root.unmount()); client.clear(); root = createRoot(container); }
    });

    it('L3 never parks a click for online replay and fences a late response after generation replacement', async () => {
        nativeMetadataMode.mutations = true;
        const pending: Array<(value: unknown) => void> = [];
        vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { pending.push(resolve as (value: unknown) => void); }));
        const client = new QueryClient();
        const render = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>); await settle(); }); };
        try {
            await render();
            onlineManager.setOnline(false);
            await act(async () => { positive('x').click(); await settle(); });
            // The explicit request fails or returns now; React Query must not
            // retain an offline command and execute it on a later reconnect.
            expect(api.post).toHaveBeenCalledTimes(1);
            state.connectionGeneration += 1;
            await render();
            await act(async () => { positive('y').click(); await settle(); });
            expect(api.post).toHaveBeenCalledTimes(2);
            await act(async () => { pending[0]({ data: { ...manualReport, command_id: 'l3-old-owner', action_id: 'oem.x.move_steps' } }); await settle(); });
            expect(container.textContent).not.toContain('l3-old-owner');
            expect(positive('y').disabled).toBe(true);
            await act(async () => { positive('x').click(); onlineManager.setOnline(true); await settle(); });
            expect(api.post).toHaveBeenCalledTimes(2);
            await act(async () => { pending[1]({ data: { ...manualReport, command_id: 'l3-current-owner', action_id: 'oem.y.move_steps' } }); await settle(); });
            expect(positive('y').disabled).toBe(false);
        } finally { onlineManager.setOnline(true); await act(async () => root.unmount()); client.clear(); root = createRoot(container); }
    });

    it.each(['x', 'y'])('L3 reconciles uncertain %s by real GET through status failure and terminal failure, without a delayed replay', async axis => {
        nativeMetadataMode.mutations = true;
        nativeMetadataMode.receipts = true;
        const commandId = `l3-${axis}-uncertain`;
        const actionId = `oem.${axis}.move_steps`;
        vi.mocked(api.post).mockRejectedValue({ response: { status: 502, data: { detail: {
            error: 'post_dispatch_receipt_validation_failed', command_id: commandId,
            status_path: `/operator/v2/actions/receipts/${commandId}`,
            retry_guidance: 'do_not_resubmit_reconcile_by_command_id',
        } } } });
        let terminal = false;
        vi.mocked(api.get).mockImplementation(async url => {
            if (!String(url).includes(`/receipts/${commandId}`)) throw new Error(`Unexpected GET ${url}`);
            if (!terminal) throw new Error('receipt temporarily unavailable');
            return { data: { ...actualY5Detail, command_id: commandId, action_id: actionId } } as never;
        });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const render = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>); await settle(); }); };
        try {
            await render();
            await act(async () => { positive(axis).click(); await settle(); });
            expect(positive(axis).disabled).toBe(true);
            expect(container.textContent).toContain(commandId);
            state.statusError = true; state.connectionReachable = false;
            await render();
            const before = vi.mocked(api.get).mock.calls.length;
            terminal = true;
            await act(async () => { await client.refetchQueries({ queryKey: ['bioxp', 'operator-controls', 'v2', 'receipt'] }); await settle(); });
            expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(before);
            expect(container.textContent).toContain(`${actionId} · failed · ${commandId}`);
            state.statusError = false; state.connectionReachable = true;
            await render();
            expect(positive(axis).disabled).toBe(false);
            expect(api.post).toHaveBeenCalledTimes(1);
            state.connectionGeneration += 1;
            await render();
            expect(container.textContent).not.toContain(commandId);
            expect(api.post).toHaveBeenCalledTimes(1);
        } finally { await act(async () => root.unmount()); client.clear(); root = createRoot(container); }
    });
});

describe('mounted BioXP cockpit admission fan-out collapse (R-A1)', () => {
    it('represents deck evidence and interrupt stage states in the client type', () => {
        expect(completeDeckReceiptFixture.deck_movement.stages[0]).toMatchObject({
            order: 0,
            terminal_state: 'completed',
        });
        expect(completeDeckReceiptFixture.deck_movement.ambiguity_state).toBe('ambiguous');
        expect(completeDeckReceiptFixture.deck_movement.stages.map((stage) => stage.terminal_state)).toEqual([
            'completed', 'stopped', 'aborted',
        ]);
    });

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
        for (const axis of ['X', 'Y', 'Z']) {
            const axisPanel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis} Axis`)!;
            expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Go absolute')!.disabled).toBe(true);
            expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Home')!.disabled).toBe(true);
            expect([...axisPanel.querySelectorAll('button')].find(node => node.textContent === 'Stop')!.disabled).toBe(false);
        }
        expect(activate.disabled).toBe(true);
        expect(recover.disabled).toBe(true);

        catalogDashboard().ownership_generation = 2;
        catalogDashboard().latest_receipts = [{
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'other-generation-activation',
            action_id: 'meta.activate_motion',
            status: 'completed',
            terminal: true,
            ownership_generation: 2,
            accepted_at: Date.now() / 1000 + 1,
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

        catalogDashboard().ownership_generation = 1;
        catalogDashboard().latest_receipts = [{
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'lifecycle-command-activation',
            action_id: 'meta.activate_motion',
            status: 'dispatched',
            terminal: false,
            ownership_generation: 1,
            accepted_at: Date.now() / 1000 + 1,
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
            accepted_at: Date.now() / 1000 + 1,
            error: null,
        };
        catalogDashboard().latest_receipts = [
            {
                schema_version: 'bioxp.operator_action_receipt.v2',
                command_id: 'lifecycle-command-activation',
                action_id: 'meta.activate_motion',
                status: 'dispatched',
                terminal: false,
                ownership_generation: 1,
                accepted_at: Date.now() / 1000 + 1,
                error: null,
            },
            {
                schema_version: 'bioxp.operator_action_receipt.v2',
                command_id: 'lifecycle-command-activation',
                action_id: 'meta.activate_motion',
                status: 'completed',
                terminal: true,
                ownership_generation: 1,
                accepted_at: Date.now() / 1000 + 1,
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
            accepted_at: Date.now() / 1000 + 1,
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

    it.skipIf(!process.env.BMS_DECK_TEST_CATALOG)('deck harmonization selects all 26 through real polling, retains nonfirst intent on empty/error/expiry and fences replacement', async () => {
        const published = JSON.parse(readFileSync(process.env.BMS_DECK_TEST_CATALOG!, 'utf8'));
        const deck = published.actions.find((a: { action_id: string }) => a.action_id === 'oem.deck.move_to_location');
        expect(deck.destination_options).toHaveLength(26);
        // The strict-model export supplies the finite roster. The transport
        // controls freshness/failure only; selection must not use admission.
        const populated = structuredClone(state.v2Catalog.data!);
        const actions = populated.actions as Array<Record<string, unknown>>;
        actions.splice(actions.findIndex(a => a.action_id === 'oem.deck.move_to_location'), 1, {
            ...deck, enabled: false, disabled_reason: 'deck_reference_unavailable',
        });
        let response = populated;
        let fail = false;
        nativeMetadataMode.enabled = true;
        vi.useFakeTimers();
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
        vi.mocked(api.get).mockImplementation(async (url) => {
            expect(url).toBe('/api/bioxp/operator-controls/v2/catalog');
            if (fail) throw new Error('temporary catalog failure');
            return { data: structuredClone(response) };
        });
        const render = () => act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
        const advance = async (ms = 5001) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
        const panel = () => container.querySelector('[data-testid="oem-deck-movement"]')!;
        const select = () => panel().querySelector('select') as HTMLSelectElement;
        const move = () => [...panel().querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
        const choose = async (target: string) => act(async () => { select().value = target; select().dispatchEvent(new Event('change', { bubbles: true })); });
        try {
            await render(); await advance(1);
            expect(select().disabled).toBe(false);
            for (const target of deck.destination_options) {
                await choose(target.target);
                expect(select().value).toBe(target.target);
                expect(move().disabled).toBe(true);
            }
            await choose('LOC_OC');
            const camera = () => panel().querySelector('input[type="checkbox"]') as HTMLInputElement;
            await act(async () => camera().click());
            expect(camera().checked).toBe(true);
            response = structuredClone(populated);
            (response.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!.destination_options = [];
            await advance(); // actual successful transient-empty poll
            expect(select().value).toBe('LOC_OC'); expect(camera().checked).toBe(true);
            fail = true;
            await advance(); await advance(); await advance(); // retries + expired original authority
            expect(select().value).toBe('LOC_OC'); expect(select().disabled).toBe(false);
            expect(move().disabled).toBe(true);
            fail = false; response = structuredClone(populated);
            (response.dashboard as Record<string, unknown>).generated_at = Date.now() / 1000;
            await advance();
            expect(select().value).toBe('LOC_OC'); expect(camera().checked).toBe(true);
            expect(api.get.mock.calls.length).toBeGreaterThanOrEqual(5);
            // Model currently requires the complete roster. This UI-only
            // replacement control also proves future removal cannot substitute.
            response = structuredClone(response);
            const replacement = (response.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
            replacement.destination_options = (replacement.destination_options as Array<{target: string}>).filter(o => o.target !== 'LOC_OC');
            await advance(); expect(select().value).toBe(''); expect(move().disabled).toBe(true);
            state.connected = false; await render(); expect(select().disabled).toBe(true);
            expect(select().options).toHaveLength(1);
            state.connected = true; state.connectionGeneration = 2; fail = true;
            await render(); await advance(1); expect(select().disabled).toBe(true);
            expect(state.deckInvokeCalls).toHaveLength(0); expect(state.yInvokeCalls).toHaveLength(0);
            expect(api.post).not.toHaveBeenCalled();
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.enabled = false; vi.useRealTimers();
        }
    });

    it.each(['LOC_OC', 'LOC_PARK', 'LOC_TC_BARCODE'])('deck harmonization camera draft submits only compatible boolean for %s', async (target) => {
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        (action.destination_options as unknown[]).push({ target: 'LOC_PARK', label: 'Park', camera_offset_option: false, enabled: true }, { target: 'LOC_TC_BARCODE', label: 'TC barcode', camera_offset_option: false, enabled: true });
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        const select = panel.querySelector('select')!;
        const camera = panel.querySelector('input[type="checkbox"]') as HTMLInputElement;
        await act(async () => camera.click());
        expect(camera.checked).toBe(true);
        for (let poll = 0; poll < 3; poll++) await act(async () => root.render(<BioXpCockpit />));
        await act(async () => { select.value = target; select.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(camera.checked).toBe(target === 'LOC_OC');
        expect(camera.disabled).toBe(target !== 'LOC_OC');
        expect(state.deckInvokeCalls).toHaveLength(0);
        await act(async () => [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!.click());
        expect(state.deckInvokeCalls).toHaveLength(1);
        expect(state.deckInvokeCalls[0]).toMatchObject({ request: { inputs: { target, camera_offset: target === 'LOC_OC' } } });
    });

    it('deck harmonization real receipt GETs survive status failure without another POST and stop at generation change', async () => {
        nativeMetadataMode.receipts = true;
        vi.useFakeTimers();
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        let calls = 0;
        let terminal = false;
        vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
        vi.mocked(api.get).mockImplementation(async (url) => {
            expect(url).toContain('/api/bioxp/operator-controls/v2/receipts/deck-command-mounted-1');
            calls++;
            if (calls === 2) throw new Error('temporary receipt failure');
            return { data: { ...completeDeckReceiptFixture, command_id: 'deck-command-mounted-1', status: terminal ? 'completed' : 'dispatched', terminal } };
        });
        const render = () => act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
        const advance = async () => act(async () => { await vi.advanceTimersByTimeAsync(5001); });
        try {
            await render();
            const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
            const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
            await act(async () => move.click()); await advance();
            state.statusError = true; state.connectionReachable = false; await render();
            await advance(); terminal = true; await advance(); await advance();
            expect(calls).toBeGreaterThanOrEqual(3);
            expect(panel.textContent).toContain('Lifecyclecompleted');
            expect(move.disabled).toBe(true);
            expect(state.deckInvokeCalls).toHaveLength(1); expect(api.post).not.toHaveBeenCalled();
            state.connected = false; await render(); const prior = calls; await advance(); expect(calls).toBe(prior);
            state.connectionGeneration = 2; state.connected = true; await render(); await advance();
            expect(calls).toBe(prior); expect(panel.textContent).toContain('earlier connection');
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.receipts = false; vi.useRealTimers();
        }
    });

    it.each(['captured', 'failed-query', 'pending-query'])('L6 actual producer history keeps query identity separate from deck motion: %s', async (mode) => {
        const receipts = structuredClone(deckQueryHistory.latest_receipts);
        const query = receipts.find(row => row.action_id === 'oem.deck.collect_authority')!;
        // Explicit fault variants of an actual producer identity, not claims
        // that the captured completed query was pending or failed.
        if (mode === 'failed-query') Object.assign(query, {
            status: 'failed', terminal: true,
            error: { code: 'reconciliation_required', message: 'query observation failed', retryable: false },
        });
        if (mode === 'pending-query') Object.assign(query, { status: 'dispatched', terminal: false });
        catalogDashboard().latest_receipts = receipts;
        catalogDashboard().active_commands = mode === 'pending-query' ? [query] : [];
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
        expect(move.disabled).toBe(false);
        expect(panel.textContent).not.toContain('Existing deck command requires reconciliation');
        expect(state.receiptHookCalls.filter(call => call.commandId !== null)).toEqual([]);
        // Fresh catalog admission, not the query receipt, determines movement.
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        action.enabled = false; action.disabled_reason = 'canonical_deck_authority_unavailable:deck_authority_unobserved';
        await act(async () => root.render(<BioXpCockpit />));
        expect(move.disabled).toBe(true);
        expect(state.deckInvokeCalls).toHaveLength(0);
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(api.post).not.toHaveBeenCalled();
    });

    it.each(['queued', 'dispatched'])('L6 real Park identity reports %s as in progress, then accepts its actual terminal detail', async (phase) => {
        nativeMetadataMode.receipts = true;
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
        // Reconstruct the compact in-flight projection from the actual stored
        // transition and receipt identity. This is replay, not a captured poll.
        expect(actualParkReceipt.transitions.some(row => row.to_status === phase)).toBe(true);
        const compactKeys = Object.keys(deckQueryHistory.latest_receipts[0]);
        const pending = Object.fromEntries(Object.entries(actualParkReceipt).filter(([key]) => compactKeys.includes(key)));
        Object.assign(pending, { status: phase, terminal: false, finished_at: null, completion_class: null,
            state_version: phase === 'queued' ? 1 : 2 });
        catalogDashboard().active_commands = [pending];
        catalogDashboard().latest_receipts = structuredClone(deckQueryHistory.latest_receipts);
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        action.enabled = false; action.disabled_reason = 'canonical_deck_authority_unavailable:deck_authority_unobserved';
        let finish!: (value: { data: typeof actualParkReceipt }) => void;
        vi.mocked(api.get).mockImplementation(async (url) => {
            expect(url).toBe(`/api/bioxp/operator-controls/v2/receipts/${actualParkReceipt.command_id}`);
            return new Promise(resolve => { finish = resolve; });
        });
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
            const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
            expect(move.disabled).toBe(true);
            expect(move.title).toContain('Source-owned deck readiness is unavailable');
            expect(panel.textContent).not.toContain('Existing deck command requires reconciliation');
            expect(panel.textContent).toContain(actualParkReceipt.command_id);
            await act(async () => { finish({ data: structuredClone(actualParkReceipt) }); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(panel.textContent).toContain('Lifecyclecompleted');
            expect(panel.textContent).toContain('Controller completionverified');
            expect(panel.textContent).toContain('Physical observationnot observed');
            expect(move.disabled).toBe(true); // completion does not renew admission
            expect(move.title).not.toContain('in progress');
            catalogDashboard().active_commands = [];
            catalogDashboard().latest_receipts = [structuredClone(actualParkReceipt)];
            action.enabled = true; action.disabled_reason = null;
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            expect(move.disabled).toBe(false);
            expect(state.deckInvokeCalls).toHaveLength(0);
            expect(state.yInvokeCalls).toHaveLength(0);
            expect(api.post).not.toHaveBeenCalled();
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.receipts = false;
        }
    });

    it('L6 sparse recovery evidence stays explicit while detailed receipt GET is unavailable', async () => {
        nativeMetadataMode.receipts = true;
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
        const failed = structuredClone(deckQueryHistory.latest_receipts.find(row => row.action_id === 'oem.deck.move_to_location' && row.status === 'failed')!);
        failed.completion_class = 'recovery_required'; // explicit negative control
        catalogDashboard().latest_receipts = [failed];
        vi.mocked(api.get).mockRejectedValue(new Error('receipt GET unavailable'));
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
            const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
            const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
            expect(move.disabled).toBe(false);
            expect(String(move.title ?? '')).not.toContain('requires reconciliation');
            expect(panel.textContent).toContain(failed.command_id);
            expect(api.post).not.toHaveBeenCalled();
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.receipts = false;
        }
    });

    it('L6 real failed move identity, not newer query history, owns recovery reconciliation', async () => {
        const receipts = structuredClone(deckQueryHistory.latest_receipts);
        const failed = receipts.find(row => row.action_id === 'oem.deck.move_to_location' && row.status === 'failed')!;
        // Fault-inject a genuine recovery requirement on a captured motion
        // identity. Sparse terminal failure alone must not invent that gate.
        failed.completion_class = 'recovery_required';
        catalogDashboard().latest_receipts = receipts;
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
        expect(panel.textContent).not.toContain('Existing deck command requires reconciliation');
        expect(move.disabled).toBe(false);
        expect(state.receiptHookCalls).toContainEqual({ commandId: failed.command_id, generation: 1, enabled: true });
        expect(state.receiptHookCalls.some(call => receipts.some(row => row.action_id === 'oem.deck.collect_authority' && row.command_id === call.commandId))).toBe(false);
        expect(state.deckInvokeCalls).toHaveLength(0);
        expect(api.post).not.toHaveBeenCalled();
    });

    it('deck harmonization cold expired catalog permits explicit query refresh but no motion or automatic collection', async () => {
        const actions = state.v2Catalog.data!.actions as Array<Record<string, unknown>>;
        actions.push({ action_id: 'oem.deck.collect_authority', enabled: true, disabled_reason: null, interrupt: false,
            request_schema_version: 'bioxp.operator_action_request.v2', response_schema_version: 'bioxp.operator_action_receipt.v2' });
        (state.v2Catalog.data!.dashboard as Record<string, unknown>).generated_at = Date.now() / 1000 - 60;
        state.statusError = true; state.connectionReachable = false;
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        const select = panel.querySelector('select')!;
        const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
        const refresh = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Refresh deck readiness (no motion)')!;
        expect(select.options).toHaveLength(2); expect(select.disabled).toBe(false);
        expect(move.disabled).toBe(true); expect(refresh.disabled).toBe(false);
        expect(state.yInvokeCalls).toHaveLength(0); expect(state.deckInvokeCalls).toHaveLength(0);
        await act(async () => refresh.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: 'oem.deck.collect_authority', inputs: {}, expected_connection_generation: 1, expected_ownership_generation: 1 } });
        expect(move.disabled).toBe(true); expect(state.deckInvokeCalls).toHaveLength(0);
        const action = actions.find(a => a.action_id === 'oem.deck.collect_authority')!;
        action.enabled = false; action.disabled_reason = 'query owner unavailable';
        await act(async () => root.render(<BioXpCockpit />)); expect(refresh.disabled).toBe(true);
        state.connected = false; await act(async () => root.render(<BioXpCockpit />)); expect(refresh.disabled).toBe(true);
    });

    it('deck harmonization target-specific ordinary readiness does not authorize Park', async () => {
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        (action.destination_options as unknown[]).push({ target: 'LOC_PARK', label: 'Park', camera_offset_option: false, enabled: false, disabled_reason: 'canonical_deck_authority_unavailable:deck_semantic_state_not_authoritative:location_revision' });
        Object.assign(catalogDashboard().deck as object, { current_location: null, current_well: null, semantic_state_revision: 1 });
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        const select = panel.querySelector('select')!;
        const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
        expect(move.disabled).toBe(false);
        await act(async () => { select.value = 'LOC_PARK'; select.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(select.disabled).toBe(false); expect(move.disabled).toBe(true);
        await act(async () => { select.value = 'LOC_OC'; select.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(move.disabled).toBe(false); expect(state.deckInvokeCalls).toHaveLength(0);
    });

    it.each([
        ['deck_bootstrap_semantic_location_unavailable', 'established previous location and well'],
        ['deck_bootstrap_board_epochs_unavailable', 'Controller board ownership is not established'],
        ['deck_bootstrap_branch_state_unavailable', 'Required source branch state is unknown'],
        ['deck_bootstrap_latch_or_tip_state_unavailable', 'Required latch or tip state is unknown'],
        ['deck_gripper_observation_not_authoritative', 'Current gripper confirmation is unavailable'],
        ['deck_reference_not_authoritative:x', 'Required X reference is unavailable'],
        ['private exception password=do-not-show', 'Source-owned deck readiness is unavailable'],
        ['__proto__', 'Source-owned deck readiness is unavailable'],
    ])('deck harmonization renders finite prerequisite %s without exception prose', async (suffix, expected) => {
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        action.enabled = false; action.disabled_reason = `canonical_deck_authority_unavailable:${suffix}`;
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
        expect(panel.textContent).toContain(expected);
        expect(panel.textContent).not.toContain('password=');
        expect((panel.querySelector('select') as HTMLSelectElement).disabled).toBe(false);
        expect(state.deckInvokeCalls).toHaveLength(0);
    });

    it.skipIf(!process.env.BMS_DECK_DETAIL_EXPORT)('deck harmonization renders strict API Park no-op without fabricated controller or physical completion', async () => {
        const detail = JSON.parse(readFileSync(process.env.BMS_DECK_DETAIL_EXPORT!, 'utf8'));
        expect(detail.completion_class).toBe('source_noop');
        nativeMetadataMode.receipts = true;
        state.deckDeferred = true;
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        vi.mocked(api.get).mockReset();
        vi.mocked(api.get).mockImplementation(async (url) => {
            expect(url).toBe(`/api/bioxp/operator-controls/v2/receipts/${detail.command_id}`);
            return { data: structuredClone(detail) };
        });
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>).find(a => a.action_id === 'oem.deck.move_to_location')!;
        (action.destination_options as unknown[]).push({ target: 'LOC_PARK', label: 'Park', camera_offset_option: false, enabled: true });
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
            const select = panel.querySelector('select')!;
            await act(async () => { select.value = 'LOC_PARK'; select.dispatchEvent(new Event('change', { bubbles: true })); });
            await act(async () => [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!.click());
            await act(async () => { state.deckCallbacks?.onSuccess?.(detail); });
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(panel.textContent).toContain('Lifecyclecompleted');
            expect(panel.textContent).toContain('Controller completionnot verified');
            expect(panel.textContent).toContain('Semantic state commitcommitted');
            expect(panel.textContent).toContain('Physical observationnot observed');
            expect(panel.textContent).not.toContain('receipt unavailable / outcome uncertain');
            expect(state.deckInvokeCalls).toHaveLength(1);
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.receipts = false;
        }
    });

    it('renders finite deck movement and submits exactly one semantic enqueue', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        expect(panel).toBeTruthy();
        expect(panel.textContent).toContain('LOC_TC');
        expect(panel.textContent).toContain('2');
        expect(panel.textContent).not.toContain('a'.repeat(64));
        expect(panel.textContent).not.toContain('b'.repeat(64));
        expect(panel.textContent).toContain('Current location');
        expect(panel.textContent).toContain('Current well');
        for (const clutter of ['PositionTable revision', 'Catalog revision', 'Canonical key', 'Operator label']) {
            expect(panel.textContent).not.toContain(clutter);
        }
        expect(container.textContent).not.toContain('Connection generation');
        expect(container.textContent).not.toContain('Board lifecycle generation');
        const selector = panel.querySelector('select') as HTMLSelectElement;
        const destinations = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>)
            .find(action => action.action_id === 'oem.deck.move_to_location')!.destination_options as Array<{ target: string; label: string }>;
        expect([...selector.options].map(option => ({ target: option.value, label: option.textContent }))).toEqual(destinations.map(({ target, label }) => ({ target, label })));
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

    it('retains selected local receipt while showing newer canonical queue work', async () => {
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        state.deckReceipt.data = {
            command_id: 'deck-command-mounted-1', action_id: 'oem.deck.move_to_location',
            status: 'completed', terminal: true, sequence: 10, completion_class: 'completed', error: null,
        };
        catalogDashboard().latest_receipts = [state.deckReceipt.data];
        catalogDashboard().active_commands = [{
            command_id: 'deck-command-dashboard-2', action_id: 'oem.deck.move_to_location',
            status: 'dispatched', terminal: false, sequence: 11, completion_class: null, error: null,
        }];
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });

        expect(panel.textContent).toContain('deck-command-mounted-1');
        expect(panel.textContent).toContain('Lifecyclecompleted');
        expect(move.disabled).toBe(false);
        expect(state.receiptHookCalls).toContainEqual({ commandId: 'deck-command-mounted-1', generation: 1, enabled: true });
    });

    it.each([
        ['oem.deck._mov_execution', 'dispatched', false, null],
        ['oem.deck._finite_operation', 'ambiguous', true, 'recovery_required'],
    ])('polls canonical internal deck work; terminal recovery faults never gate motion: %s', async (actionId, status, terminal, completionClass) => {
        const internalReceipt = {
            command_id: 'canonical-internal-deck-command', action_id: actionId,
            status, terminal, sequence: 73, completion_class: completionClass, error: null,
        };
        catalogDashboard().active_commands = terminal ? [] : [internalReceipt];
        catalogDashboard().latest_receipts = terminal ? [internalReceipt] : [];

        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        expect(move.disabled).toBe(false);
        expect(panel.textContent).not.toContain('Existing deck command requires reconciliation; do not resubmit.');
        expect(panel.textContent).not.toContain(actionId);
        expect(state.receiptHookCalls).toContainEqual({ commandId: 'canonical-internal-deck-command', generation: 1, enabled: true });

        catalogDashboard().active_commands = [];
        catalogDashboard().latest_receipts = [{
            ...internalReceipt,
            status: 'completed', terminal: true, completion_class: 'completed',
        }];
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });

        expect(move.disabled).toBe(false);
        expect(panel.textContent).not.toContain(actionId);
        expect(state.receiptHookCalls.some(call => call.commandId === 'canonical-internal-deck-command')).toBe(true); // selected terminal detail remains observable; real hook stops its polling
    });

    it.each(process.env.BMS_RECOVERY_DETAIL_EXPORT ? ['contract', 'native'] : ['contract'])('warm recovery polls the actual decoder without replay: %s', async (source) => {
        const resolved = source === 'native'
            ? JSON.parse(readFileSync(process.env.BMS_RECOVERY_DETAIL_EXPORT!, 'utf8')) as BioXpOperatorReceiptDetailV2
            : { ...completeDeckReceiptFixture, status: 'ambiguous', completion_class: 'recovery_required',
                deck_movement: { ...completeDeckReceiptFixture.deck_movement, ambiguity_state: 'recovery_required', recovery_resolution: {
                    command_id: completeDeckReceiptFixture.command_id, decision_id: 'warm-home-decision', semantic_state_revision: 18, transition_sequence: 2,
                } } } as BioXpOperatorReceiptDetailV2;
        catalogDashboard().deck!.semantic_state_revision = resolved.deck_movement!.recovery_resolution!.semantic_state_revision - 1;
        const unresolved = structuredClone(resolved);
        delete unresolved.deck_movement!.recovery_resolution;
        catalogDashboard().latest_receipts = [unresolved];
        nativeMetadataMode.receipts = true;
        vi.useFakeTimers();
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        let payload = unresolved;
        let fail = false;
        let calls = 0;
        vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
        vi.mocked(api.get).mockImplementation(async url => {
            expect(url).toContain(encodeURIComponent(resolved.command_id)); calls++;
            if (fail) throw new Error('receipt unavailable');
            return { data: structuredClone(payload) };
        });
        const render = () => act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
        const advance = () => act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        const refreshAuthority = () => { catalogDashboard().generated_at = Date.now() / 1000; };
        try {
            await render(); await advance();
            const panel = container.querySelector('[data-testid="oem-deck-movement"]')!;
            const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
            expect(move.disabled).toBe(false); // an unresolved historical outcome never gates new movement
            payload = resolved;
            await advance(); refreshAuthority(); await render();
            expect(move.disabled).toBe(false); // resolution display awaits authority; admission does not
            catalogDashboard().deck!.semantic_state_revision = resolved.deck_movement!.recovery_resolution!.semantic_state_revision;
            refreshAuthority(); await render();
            expect(move.disabled).toBe(false);
            expect(panel.textContent).toContain('Historical outcome remains ambiguous');
            expect(panel.textContent).toContain(resolved.command_id);
            expect(calls).toBeGreaterThanOrEqual(2);
            fail = true; await advance(); refreshAuthority(); await render();
            expect(move.disabled).toBe(false); // receipt query failure surfaces evidence state, never gates movement
            fail = false; state.statusError = true; state.connectionReachable = false;
            await render(); await advance();
            expect(move.disabled).toBe(true);
            state.statusError = false; state.connectionReachable = true;
            refreshAuthority(); await render();
            expect(move.disabled, panel.textContent ?? '').toBe(false);
            expect(state.deckInvokeCalls).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
            state.connected = false; await render(); const before = calls; await advance(); expect(calls).toBe(before);
            state.connectionGeneration = 2; state.connected = true; catalogDashboard().latest_receipts = [];
            await render(); await advance();
            expect(calls).toBe(before);
            expect(panel.textContent).not.toContain(resolved.command_id);
            expect(state.deckInvokeCalls).toHaveLength(0);
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.receipts = false; vi.useRealTimers();
        }
    });

    it('decodes recovery resolution without rewriting ambiguous history', async () => {
        const real = await vi.importActual<typeof import('../../src/lib/bioxpClient')>('../../src/lib/bioxpClient');
        const receipt = { ...completeDeckReceiptFixture, status: 'ambiguous', completion_class: 'recovery_required',
            deck_movement: { ...completeDeckReceiptFixture.deck_movement, recovery_resolution: {
                command_id: completeDeckReceiptFixture.command_id, decision_id: 'home-decision', semantic_state_revision: 18, transition_sequence: 2,
            } } } as BioXpOperatorReceiptDetailV2;
        expect(real.decodeBioXpReceiptDetailV2(receipt, receipt.command_id)).toBe(receipt);
        expect(receipt.status).toBe('ambiguous');
        for (const mutation of [{ decision_id: '' }, { semantic_state_revision: true }, { transition_sequence: 0 }, { command_id: 'other' }, { extra: 1 }]) {
            const bad = structuredClone(receipt);
            Object.assign(bad.deck_movement!.recovery_resolution!, mutation);
            expect(() => real.decodeBioXpReceiptDetailV2(bad, receipt.command_id)).toThrow();
        }
        for (const mutation of [{ terminal: false }, { action_id: 'oem.y.move_steps' }, { status: 'completed' }]) {
            expect(() => real.decodeBioXpReceiptDetailV2({ ...receipt, ...mutation } as BioXpOperatorReceiptDetailV2, receipt.command_id)).toThrow();
        }
    });

    it('recovery decision releases only matched history under fresh authority without replay', async () => {
        const receipt = { ...completeDeckReceiptFixture, status: 'ambiguous', completion_class: 'recovery_required',
            deck_movement: { ...completeDeckReceiptFixture.deck_movement, ambiguity_state: 'recovery_required', recovery_resolution: null as unknown } };
        catalogDashboard().latest_receipts = [receipt];
        const render = async () => { await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); }); };
        await render();
        const panel = [...container.querySelectorAll('section')].find(node => node.textContent?.includes('Deck Movement'))!;
        const move = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Move to destination')!;
        expect(move.disabled).toBe(false); // unresolved history never gates new movement
        const resolution = { command_id: receipt.command_id, decision_id: 'home-decision', semantic_state_revision: 18, transition_sequence: 2 };
        receipt.deck_movement.recovery_resolution = resolution;
        await render();
        expect(move.disabled).toBe(false); // display awaits the recovery revision; admission no longer does
        catalogDashboard().deck!.semantic_state_revision = 18;
        await render();
        expect(move.disabled).toBe(false);
        expect(panel.textContent).toContain('Earlier move reconciled.');
        expect(panel.textContent).not.toContain('home-decision');
        expect(panel.textContent).toContain('Ambiguous outcomeambiguous');
        expect(state.deckInvokeCalls).toHaveLength(0);
        for (const bad of [{ ...resolution, command_id: 'other' }, { ...resolution, transition_sequence: 0 }, null]) {
            receipt.deck_movement.recovery_resolution = bad;
            await render();
            expect(move.disabled).toBe(false); // malformed history decodes to no resolution and cannot gate movement
            expect(panel.textContent).not.toContain('Earlier move reconciled.');
            expect(state.deckInvokeCalls).toHaveLength(0);
        }
        receipt.deck_movement.recovery_resolution = resolution;
        catalogDashboard().deck!.semantic_state_revision = 19;
        await render();
        expect(move.disabled).toBe(false);
        await act(async () => move.click());
        expect(state.deckInvokeCalls).toHaveLength(1); // explicit new user command only
        expect(move.disabled).toBe(false); // another deliberate intent need not wait for physical completion
        await act(async () => move.click());
        expect(state.deckInvokeCalls).toHaveLength(2);
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
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());

        const lifecycle = [...panel.querySelectorAll('dl > div')]
            .find((row) => row.querySelector('dt')?.textContent === 'Lifecycle');
        expect(lifecycle?.textContent).toContain(status);
        expect(panel.textContent).toContain('not pending');
    });

    it('disables deck movement on stale generation authority with an exact reason', async () => {
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        expect(move.disabled).toBe(true);
        expect(panel.textContent).toContain('Fresh v2 catalog or dashboard authority is unavailable.');
    });

    it.each([
        ['ownership', () => { catalogDashboard().ownership_generation = 2; }],
        ['position table', () => { catalogDashboard().deck!.position_table_revision = 'c'.repeat(64); }],
        ['destination catalog', () => { catalogDashboard().deck!.destination_catalog_revision = 'd'.repeat(64); }],
    ])('uses embedded %s authority without trusting a separately fetched dashboard', async (_label, mutate) => {
        mutate();
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        if (_label === 'ownership') {
            // Connection identity and a retired dashboard do not override the
            // ownership token supplied by the single current observation.
            expect(move.disabled).toBe(false);
            await act(async () => move.click());
            expect(state.deckInvokeCalls).toHaveLength(1);
            expect(state.deckInvokeCalls[0]).toMatchObject({ request: { expected_ownership_generation: 2 } });
        } else {
            expect(move.disabled).toBe(true);
            expect(panel.textContent).toContain('matching catalog and dashboard deck authority is unavailable');
            await act(async () => move.click());
            expect(state.deckInvokeCalls).toHaveLength(0);
        }
    });


    it('preserves explicit robot ownership denial for deck movement', async () => {
        catalogDashboard().ownership_generation = 2;
        Object.assign((state.v2Catalog.data!.actions as Array<Record<string, unknown>>)
            .find(action => action.action_id === 'oem.deck.move_to_location')!, {
            enabled: false, disabled_reason: 'Robot denied stale ownership generation.',
        });
        await act(async () => root.render(<BioXpCockpit />));
        const panel = [...container.querySelectorAll('section')].find(node => node.textContent?.includes('Deck Movement'))!;
        const move = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Move to destination')!;
        expect(move.disabled).toBe(true);
        expect(panel.textContent).toContain('Robot denied stale ownership generation.');
        await act(async () => move.click());
        expect(state.deckInvokeCalls).toHaveLength(0);
    });

    it('renders receipt unavailable and outcome uncertain instead of inventing queued state', async () => {
        state.deckReceipt.error = new Error('detail parse failed');
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        expect(panel.textContent).toContain('receipt unavailable / outcome uncertain');
        expect(panel.textContent).toContain('Do not resubmit');
        expect(panel.textContent).not.toContain('Lifecyclequeued');
    });

    it('keeps a recovered wrong-action receipt uncertain instead of confirming deck completion', async () => {
        state.deckDeferred = true;
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
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
        const panel = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
        const move = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Move to destination') as HTMLButtonElement;
        await act(async () => move.click());
        const oldCallbacks = state.deckCallbacks;
        state.connectionGeneration = 2;
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        await act(async () => oldCallbacks?.onSuccess?.({ command_id: 'deck-old-generation', status: 'queued', terminal: false }));
        expect(container.textContent).toContain('deck-old-generation');
        expect(container.textContent).toContain('earlier connection');
    });

    it('keeps Y and deck errors on their independent control surfaces', async () => {
        state.yInvokeError = { response: { status: 409, data: { detail: { error: 'y_conflict' } } } };
        state.deckInvokeError = { response: { status: 502, data: { detail: { error: 'deck_uncertain' } } } };
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        const deck = [...container.querySelectorAll('section')].find((node) => node.textContent?.includes('Deck Movement')) as HTMLElement;
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
        expect(state.v1DashboardEnabled).toBeNull();
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
        state.history.data.items = [historyItem(xReceipt('acknowledged'))];
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

    it.each(['0', '90000', '0.5', '', '9e4'])('preserves Z absolute draft %s across catalog polling and dispatches only exact integers', async (value) => {
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        await setZInput(1, value);
        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const input = article.querySelectorAll('input[type="number"]')[1] as HTMLInputElement;
        expect(article.textContent).toContain('Home reaches the upper limit (0)');
        expect(article.textContent).toContain('Current minimum:');
        const expected = value === '' ? NaN : Number(value);
        for (let poll = 0; poll < 3; poll++) {
            state.catalog.data = { ...state.catalog.data, actions: [...state.catalog.data.actions] };
            await act(async () => root.render(<BioXpCockpit />));
            expect(input.valueAsNumber).toBe(expected);
        }
        expect(state.yInvokeCalls).toHaveLength(0);
        const go = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        expect(go.disabled).toBe(!Number.isInteger(expected));
        await act(async () => go.click());
        expect(state.yInvokeCalls.map((call) => {
            const request = call.request as Record<string, unknown>;
            return { action_id: request.action_id, inputs: request.inputs };
        })).toEqual(Number.isInteger(expected) ? [{ action_id: 'oem.z.move_absolute', inputs: { position_steps: expected } }] : []);
    });

    it.each(['X Axis', 'Z Axis', 'Gripper'].flatMap(label => [0, 1].flatMap(index =>
        ['0', '0.5', '', '9e4'].map(value => ({ label, index, value })),
    )))('preserves shared numeric draft $label/$index/$value without coercion or motion', async ({ label, index, value }) => {
        await act(async () => root.render(<BioXpCockpit />));
        const article = [...container.querySelectorAll('article')].find(n => n.querySelector('h3')?.textContent === label)!;
        const input = article.querySelectorAll('input[type="number"]')[index] as HTMLInputElement;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        for (let poll = 0; poll < 3; poll++) {
            state.catalog.data = { ...state.catalog.data };
            await act(async () => root.render(<BioXpCockpit />));
            expect(input.valueAsNumber).toBe(value === '' ? NaN : Number(value));
        }
        if (value === '' || value === '0.5') {
            const action = [...article.querySelectorAll('button')].find(button => button.textContent === (index ? 'Go absolute' : 'Move +'))!;
            expect(action.disabled).toBe(true);
        }
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
    });

    it('renders source-owned Z targets and receipts across minimum, draft and generation changes', async () => {
        await act(async () => root.render(<BioXpCockpit />));
        const original = structuredClone(state.catalog.data.dashboard);
        for (const fixture of zTargetProducer) {
            await setZInput(1, String(fixture.requested));
            Object.assign(state.catalog.data.dashboard, {
                ownership_generation: 1,
                z_axis: { provider: fixture.provider },
            });
            // Producer-shaped receipt reaches the actual shared history card.
            state.history.data.items = [{ ...historyItem({ ...xReceipt('completed', 1),
                action_id: 'oem.z.move_absolute' } as never), z_move: fixture.z_move }];
            for (let poll = 0; poll < 3; poll++) {
                state.catalog.data = { ...state.catalog.data };
                await act(async () => root.render(<BioXpCockpit />));
                const text = container.querySelector('[data-testid="z-target-context"]')?.textContent ?? container.textContent;
                expect(text).toContain(`Selected target: ${fixture.provider.target_preview.effective_position_steps} steps`);
                expect(text).toContain(`Current minimum: ${fixture.minimum} steps`);
                expect(container.textContent).toContain(`Z requested: ${fixture.requested} · Applied target: ${fixture.z_move.effective_position_steps}`);
                expect(container.textContent).toContain(`Before: ${fixture.start} · After: unknown`);
            }
        }
        const go = () => [...[...container.querySelectorAll('article')].find(n => n.querySelector('h3')?.textContent === 'Z Axis')!.querySelectorAll('button')].find(button => button.textContent === 'Go absolute')!;
        // A late preview for a different draft must not be called selected.
        await setZInput(1, '0');
        expect(container.querySelector('[data-testid="z-target-context"]')!.textContent).toContain('Selected target: unavailable');
        Object.assign(state.catalog.data.dashboard, { ownership_generation: 99 });
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.querySelector('[data-testid="z-target-context"]')!.textContent).toContain('Current minimum: unavailable');
        state.catalog.data.dashboard = original;
        await act(async () => root.render(<BioXpCockpit />));
        expect(container.querySelector('[data-testid="z-target-context"]')!.textContent).toContain('Selected target: unavailable');
        expect(go().disabled).toBe(false); // missing preview never adds an admission gate
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
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
        const zClear = [...zArticle.querySelectorAll('button')].find((button) => button.textContent === 'Z Clear (automatic position)') as HTMLButtonElement;
        // The original fixture had no action identity: R3 does not permit a
        // blanket pending exemption. Establish actual read-only authority
        // before calling this request unrelated to normal motion submission.
        expect(zClear.disabled).toBe(true);
        state.invokeVariables = { actionId: 'clear-read-only-fixture' };
        state.catalog.data.actions.push({ action_id: 'clear-read-only-fixture', safety_class: 'read_only' });
        await act(async () => root.render(<BioXpCockpit />));
        expect(zClear.disabled).toBe(false);
    });

    // CCI btnMoveXTo_Click:2291 uses int.TryParse: fractional input must not
    // silently become a different integer command. Serial-206 effective catalog
    // bounds remain exactly 60..90263; this does not add a second admission gate.
    it.each([
        ['59', false], ['60', true], ['90263', true], ['90264', false],
        ['60.5', false], ['90263.5', false],
    ])('preserves exact X absolute input %s and rejects invalid targets without dispatch', async (value, admitted) => {
        await act(async () => { root.render(<BioXpCockpit />); await Promise.resolve(); });
        await setXAbsolute(value);
        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const input = article.querySelectorAll('input[type="number"]')[1] as HTMLInputElement;
        const go = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        expect(input.min).toBe('60');
        expect(input.max).toBe('90263');
        expect(input.value).toBe(value);
        expect(go.disabled).toBe(!admitted);
        await act(async () => go.click());
        expect(state.yInvokeCalls).toHaveLength(admitted ? 1 : 0);
        if (admitted) expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: 'oem.x.move_absolute', inputs: { position_steps: Number(value) } } });
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
        state.history.data.items = [historyItem(xReceipt('acknowledged'))];
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
        expect(queueStrip).toBeNull(); // legacy telemetry queue is not canonical command custody
        expect(container.querySelector('[data-testid="canonical-command-queue"]')).not.toBeNull();
        expect(state.admissionCalls).toBe(0);
    });

    it('leaves X queue admission to the robot when the cached queue projection is full', async () => {
        state.history.data.items = [historyItem(xReceipt('acknowledged'))];
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

    it('holds synchronous moves and cross-axis Home while a command is pending (installed CCI handlers)', async () => {
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

        expect(xMovePositive.disabled).toBe(true);
        expect(xHome.disabled).toBe(true);
        expect(zHome.disabled).toBe(true);
        expect(state.invokeCalls).toHaveLength(0);
    });

    it.each(['x', 'y', 'z'])('holds pending %s submission, then respects robot denial and expiry without blocking Stop', async (axis) => {
        state.invokePending = true;
        const action = (state.v2Catalog.data!.actions as Array<Record<string, unknown>>)
            .find(row => row.action_id === `oem.${axis}.move_steps`)!;
        await act(async () => root.render(<BioXpCockpit />));
        const panel = [...container.querySelectorAll('article')].find(node => node.querySelector('h3')?.textContent === `${axis.toUpperCase()} Axis`)!;
        const move = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Move +')!;
        // CCI::moveSteps waits inline for X/Y/Z (installed IL 37194–37499).
        // A pre-submit enabled snapshot cannot reserve another waiting HTTP request.
        expect(move.disabled).toBe(true);
        await act(async () => move.click());
        expect(state.yInvokeCalls).toHaveLength(0);
        state.invokePending = false;
    state.axisInvokePending = false;
    state.invokeVariables = undefined;
        await act(async () => root.render(<BioXpCockpit />));
        expect(move.disabled).toBe(false);
        await act(async () => move.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: `oem.${axis}.move_steps` } });
        Object.assign(action, { enabled: false, disabled_reason: 'Robot denied: recovery required.' });
        await act(async () => root.render(<BioXpCockpit />));
        expect(move.disabled).toBe(true);
        expect(move.title).toContain('Robot denied: recovery required.');
        await act(async () => move.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        Object.assign(action, { enabled: true, disabled_reason: null });
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
        await act(async () => root.render(<BioXpCockpit />));
        expect(move.disabled).toBe(true);
        await act(async () => move.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        const stop = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Stop')!;
        expect(stop.disabled).toBe(false);
        await act(async () => stop.click());
        expect(state.yInterruptCalls).toHaveLength(1);
        expect(state.yInterruptCalls[0]).toMatchObject({ actionId: `oem.${axis}.stop` });
    });

    it('does not submit a successive synchronous move until the prior HTTP call returns', async () => {
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

        expect(state.yInvokeCalls).toHaveLength(0);
        state.invokePending = false;
    state.axisInvokePending = false;
    state.invokeVariables = undefined;
        await act(async () => root.render(<BioXpCockpit />));
        await act(async () => movePositive.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({ request: { action_id: 'oem.x.move_steps' } });
    });

    it('re-enables X controls from the terminal receipt even when the dashboard snapshot lags (R-A3)', async () => {
        state.history.data.items = [historyItem(xReceipt('completed'))];
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

    it('renders retained live history with numeric timestamps and absent nested stage receipts', async () => {
        state.history.data.items = retainedHistory.receipts.map(historyItem);
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = [...container.querySelectorAll('details')]
            .find((node) => node.querySelector('summary')?.textContent === 'Recent Robot Actions') as HTMLElement;
        expect(section.querySelectorAll('article')).toHaveLength(retainedHistory.receipts.length);
        expect(section.textContent).toContain('Terminal proof unverified');
        expect(section.textContent).toContain('Retained legacy record — not current control authority.');
        state.history.isError = true;
        state.history.error = new Error('refresh failed');
        await act(async () => root.render(<BioXpCockpit />));
        expect(section.querySelectorAll('article')).toHaveLength(retainedHistory.receipts.length);
        expect(container.textContent).toContain('Robot action history unavailable');
        expect(container.textContent).not.toContain('No robot action receipts recorded.');
        expect(state.admissionCalls).toBe(0);
    });

    it('passes the selected depth to the history query and renders that many receipts (R-A5)', async () => {
        state.history.data.items = Array.from({ length: 30 }, (_, i) => historyItem(xReceipt('completed', i)));

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(state.historyCalls.at(-1)).toBe(8);

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
        const historySection = [...container.querySelectorAll('details')]
            .find((node) => node.querySelector('summary')?.textContent === 'Recent Robot Actions') as HTMLElement;
        const receiptArticles = [...historySection.querySelectorAll('article')]
            .filter((node) => node.textContent?.includes('oem.x.move_steps'));
        expect(receiptArticles.length).toBe(30);
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps X controls usable when the connection token differs from the robot ownership generation (regression)', async () => {
        state.connectionGeneration = 3189298922692611;
        state.history.data.items = [];

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
        expect(manualControls.querySelector('h2')?.textContent).toBe('Manual Controls');
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
        Object.assign(catalogDashboard().y_axis, {
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
        catalogDashboard().ownership_generation = 2;
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


    it.each(['interruptPending', 'lifecycleInvokePending', 'deckInvokePending'] as const)('holds Z Clear visibly during %s and restores it after submission', async (pending) => {
        await act(async () => root.render(<BioXpCockpit />));
        const clear = () => [...container.querySelectorAll('button')].find((button) => button.textContent === 'Z Clear (automatic position)')!;
        expect(clear().disabled).toBe(false);
        state[pending] = true;
        await act(async () => root.render(<BioXpCockpit />));
        expect(clear().disabled).toBe(true);
        expect(clear().title).toBe('A command is pending; wait for its receipt before another normal action.');
        const before = state.yInvokeCalls.length;
        await act(async () => clear().click());
        expect(state.yInvokeCalls).toHaveLength(before);
        state[pending] = false;
        await act(async () => root.render(<BioXpCockpit />));
        expect(clear().disabled).toBe(false);
        await act(async () => clear().click());
        expect(state.yInvokeCalls).toHaveLength(before + 1);
        expect(state.yInvokeCalls.at(-1)).toMatchObject({ request: { action_id: 'oem.z.clear', inputs: {} } });
    });

    it('keeps Z normal controls fail-closed with plain reasons while addressed Stop remains independent', async () => {
        await act(async () => root.render(<BioXpCockpit />));
        const panel = () => [...container.querySelectorAll('article')].find((element) => element.querySelector('h3')?.textContent === 'Z Axis')!;
        const normal = () => [...panel().querySelectorAll('button')].filter((button) => ['Move −', 'Move +', 'Home', 'Go absolute'].includes(button.textContent ?? ''));
        expect(normal()).toHaveLength(4);
        for (const button of normal()) expect(button.disabled).toBe(false);
        state.v2Catalog.error = new Error('catalog query failed');
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
        await act(async () => root.render(<BioXpCockpit />));
        for (const button of normal()) {
            expect(button.disabled).toBe(true);
            expect(button.title).toBe('Current robot control state is unavailable.');
            expect(button.title).not.toContain('Fresh v2 catalog');
        }
        const stop = [...panel().querySelectorAll('button')].find((button) => button.textContent === 'Stop')!;
        expect(stop.disabled).toBe(false);
    });

    it('fails normal Y closed when current robot control state is unavailable', async () => {
        state.v2Catalog.error = new Error('catalog query failed');
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
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
        state.v2Catalog.data!.dashboard.generated_at = Date.now() / 1000 - 20;
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
        catalogDashboard().y_axis.latest_compact_receipt = { command_id: 'cmd-y-detail' };
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
            schema_version: 'bioxp.operator_action_receipt.v2',
            command_id: 'interrupt-attempt-12345678',
            action_id: 'oem.y.stop',
            status: 'completed',
            terminal: true,
            physical_effect_verified: false,
            interrupt_evidence: {
                source_call_completed: true,
                source_return_ok: true,
                controller_stop_acknowledged: true,
                controller_terminal_state_verified: false,
                persistence_state: 'committed',
            },
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section.textContent).toContain('Y request: completed');
        expect(section.textContent).toContain('class=event_128');
        expect(section.textContent).toContain('terminal position=1100');
        expect(section.textContent).toContain('terminal speed=0');
        expect(section.textContent).toContain('Latest independent Y STOP receipt');
        expect(section.textContent).toContain('interrupt-attempt-12345678');
        const raw = [...section.querySelectorAll('pre')].map((node) => node.textContent).join('\n');
        expect(raw).toContain('"addressed_event_128": true');
        expect(raw).toContain('"status": 100');
    });

    it.each(['status-error', 'unreachable-observation'])('retains the passive camera session during %s without admitting motion', async (failure) => {
        await act(async () => root.render(<BioXpCockpit />));
        const camera = () => JSON.parse(container.querySelector('[data-testid="camera-session"]')!.textContent!);
        expect(camera()).toMatchObject({ connected: true, connectionGeneration: 1, mutationEnabled: true });
        if (failure === 'status-error') state.statusError = true;
        else state.connectionReachable = false;
        await act(async () => root.render(<BioXpCockpit />));
        expect(camera()).toMatchObject({ connected: true, connectionGeneration: 1, mutationEnabled: false });
        const xy = container.querySelector('[data-testid="serial206-xy-oem-panel"]')!;
        expect([...xy.querySelectorAll('button')].every(button => button.disabled)).toBe(true);
        if (failure === 'status-error') expect(container.textContent).toContain('Connection status refresh failed; checking again.');
        expect(state.xyCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
        state.statusError = false;
        state.connectionReachable = null;
        await act(async () => root.render(<BioXpCockpit />));
        expect(camera()).toMatchObject({ connected: true, connectionGeneration: 1, mutationEnabled: true });
    });

    it('fences the passive camera on real disconnect and binds its new connection generation', async () => {
        await act(async () => root.render(<BioXpCockpit />));
        const camera = () => JSON.parse(container.querySelector('[data-testid="camera-session"]')!.textContent!);
        state.connected = false;
        await act(async () => root.render(<BioXpCockpit />));
        expect(camera()).toMatchObject({ connected: false, connectionGeneration: null, mutationEnabled: false });
        state.connected = true;
        state.connectionGeneration = 2;
        await act(async () => root.render(<BioXpCockpit />));
        expect(camera()).toMatchObject({ connected: true, connectionGeneration: 2 });
        expect(state.xyCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
    });

    it('edits both combined targets directly, retains drafts across polls, and submits one exact XY request', async () => {
        await act(async () => root.render(<BioXpCockpit />));
        const panel = container.querySelector('[data-testid="serial206-xy-oem-panel"]') as HTMLElement;
        const x = panel.querySelector('[aria-label="Combined X target (steps)"]') as HTMLInputElement;
        const y = panel.querySelector('[aria-label="Combined Y target (steps)"]') as HTMLInputElement;
        expect(x).not.toBeNull();
        expect(y).not.toBeNull();
        expect(x.disabled || x.readOnly).toBe(false);
        expect(y.disabled || y.readOnly).toBe(false);
        const operatorLabels = [...container.querySelectorAll('h1, h2, h3, h4, label, button, [title]')]
            .map(node => `${node.textContent} ${node.getAttribute('title') ?? ''}`).join(' ');
        expect(operatorLabels).not.toMatch(/\bOEM\b/);
        const stop = [...container.querySelectorAll('button')].find(button => button.textContent === 'Stop X')!;
        const abort = [...container.querySelectorAll('button')].find(button => button.textContent === 'Software Abort (cancel waiters)')!;
        expect(stop.title).toContain('Immediate X stop');
        expect(abort.title).toContain('cancels waiters only; motors may continue');
        expect(container.textContent).toContain('This is not a physical emergency stop; physical stopping remains unverified.');
        const move = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Move X + Y together')!;
        const home = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Home X + Y')!;
        const edit = async (input: HTMLInputElement, value: string) => act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await edit(x, '');
        expect(x.value).toBe('');
        expect(move.disabled).toBe(true);
        expect(home.disabled).toBe(false);
        await edit(x, '12345');
        await edit(y, '');
        expect(y.value).toBe('');
        expect(move.disabled).toBe(true);
        await edit(y, '23456');
        state.v2Catalog.data = { ...state.v2Catalog.data };
        Object.assign(state.v2Catalog, { dataUpdatedAt: Date.now() });
        await act(async () => root.render(<BioXpCockpit />));
        expect(x.value).toBe('12345');
        expect(y.value).toBe('23456');
        expect(state.xyCalls).toHaveLength(0);
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
        expect(move.disabled).toBe(false);
        await act(async () => move.click());
        expect(state.xyCalls).toHaveLength(1);
        expect(state.xyCalls[0]).toMatchObject({ request: {
            action_id: 'oem.xy.move_absolute', inputs: { x: 12345, y: 23456 },
        } });
        expect(state.yInvokeCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
        expect(state.methodCalls).toHaveLength(0);
    });

    it.each([['X', ''], ['Y', ''], ['X', '1.5'], ['Y', '1.5'], ['X', '2147483648'], ['Y', '2147483648']])(
        'does not coerce or submit invalid combined %s target %j', async (axis, value) => {
            await act(async () => root.render(<BioXpCockpit />));
            const panel = container.querySelector('[data-testid="serial206-xy-oem-panel"]') as HTMLElement;
            const input = panel.querySelector(`[aria-label="Combined ${axis} target (steps)"]`) as HTMLInputElement;
            expect(input).not.toBeNull();
            await act(async () => {
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
                input.dispatchEvent(new Event('input', { bubbles: true }));
            });
            expect(input.value).toBe(value);
            const move = [...panel.querySelectorAll('button')].find(button => button.textContent === 'Move X + Y together')!;
            expect(move.disabled).toBe(true);
            await act(async () => move.click());
            expect(state.xyCalls).toHaveLength(0);
        },
    );

    it('submits recovered OEM moveXY and HomeXY through the canonical V2 action route', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const panel = container.querySelector('[data-testid="serial206-xy-oem-panel"]') as HTMLElement;
        expect(panel).not.toBeNull();
        expect(panel.textContent).toContain('Combined XY Capability');
        expect(panel.textContent).toContain('one combined command, not two independent axis commands');
        const buttons = [...panel.querySelectorAll('button')] as HTMLButtonElement[];
        const move = buttons.find((button) => button.textContent === 'Move X + Y together') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home X + Y') as HTMLButtonElement;
        expect(move.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        await act(async () => move.click());
        await act(async () => state.xyCallbacks?.onSuccess?.({
            ...manualReport, command_id: 'xy-route-test', action_id: 'oem.xy.move_absolute',
            status: 'completed', terminal: true,
        }));
        await act(async () => home.click());
        expect(state.methodCalls).toHaveLength(0);
        expect(state.xyCalls).toHaveLength(2);
        expect(state.xyCalls[0]).toMatchObject({ request: {
            action_id: 'oem.xy.move_absolute',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: { x: 60, y: 0 },
        } });
        expect(state.xyCalls[1]).toMatchObject({ request: {
            action_id: 'oem.xy.home',
            expected_connection_generation: 1,
            expected_ownership_generation: 1,
            expected_board_epoch_by_board: {},
            inputs: {},
        } });
    });
});


describe('Well pipetting cockpit integration', () => {
    it('uses catalog locationID rather than target ordinals and dispatches independently of projected availability', async () => {
        nativeMetadataMode.protocols = true; vi.stubGlobal('crypto', webcrypto);
        const action = state.v2Catalog.data.actions.find(a => a.action_id === 'oem.deck.move_to_location')!;
        Object.assign(action, { destination_options: [{ target: 'not-a-plate-ordinal', label: 'Catalog pipetting block', location_id: 4,
            enabled: false, disabled_reason: 'missing historical observation', aliases: [], branch_kind: 'ordinary', camera_offset_option: false, source_anchors: [] }] });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
        vi.mocked(api.post).mockReset().mockRejectedValue(new Error('offline robot refusal'));
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            const panel = container.querySelector('[aria-label="Well pipetting"]')!;
            expect(panel).not.toBeNull();
            for (const [name, value] of [['Block', '4'], ['Move Z position', '1']]) {
                await act(async () => {
                    const select = panel.querySelector(`[aria-label="${name}"]`) as HTMLSelectElement;
                    select.value = value; select.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }
            await act(async () => (panel.querySelector('[aria-label="Reference well D5"]') as HTMLButtonElement).click());
            const move = [...panel.querySelectorAll('button')].find(b => b.textContent === 'Move now')!;
            expect(move.disabled).toBe(false);
            await act(async () => { move.click(); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(api.post).toHaveBeenCalledTimes(1);
            const [path, request] = vi.mocked(api.post).mock.calls[0] as [string, any];
            expect(path).toBe('/api/bioxp/protocols/submit');
            expect(request.document.stages[0].actions[0]).toMatchObject({ kind: 'pipette_position',
                params: { operation: 'move', location_id: 4, well: 'D5', position_flag: 1 } });
            expect(panel.textContent).toContain('offline robot refusal');
            expect(move.disabled).toBe(false);
        } finally {
            await act(async () => root.render(null)); client.clear(); nativeMetadataMode.protocols = false; vi.unstubAllGlobals();
        }
    });
});

describe('OEM software Abort distinct from addressed Stops', () => {
    const render = () => act(async () => root.render(<BioXpCockpit />));
    const abort = () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Software Abort (cancel waiters)')!;
    const stops = () => ['X Axis', 'Y Axis', 'Z Axis', 'Gripper'].map(label => {
        const panel = [...container.querySelectorAll('article')].find(p => p.querySelector('h3')?.textContent === label)!;
        return [...panel.querySelectorAll('button')].find(b => b.textContent === 'Stop')!;
    });
    it('keeps addressed Stops independent of a real pending protocol mutation', async () => {
        nativeMetadataMode.protocols = true;
        vi.stubGlobal('crypto', webcrypto);
        state.catalog.data.actions.push({ ...xMoveAction(), action_id: 'component-stop', informational_path: '/motion/diagnostics/stop', safety_class: 'stop', enabled: true });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
        let reject!: (reason: unknown) => void;
        vi.mocked(api.post).mockReset().mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
            const transfer = container.querySelector('[aria-label="Plate and cover transfer"]')!;
            const move = [...transfer.querySelectorAll('button')].find(b => b.textContent === 'Pick up and move')!;
            expect(move.disabled).toBe(false);
            await act(async () => { move.click(); move.click(); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(api.post).toHaveBeenCalledTimes(1);
            expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/bioxp/protocols/submit');
            expect(move.disabled).toBe(true);
            for (const stop of stops()) {
                expect(stop.disabled).toBe(false);
                await act(async () => stop.click());
            }
            expect(state.yInterruptCalls.map(v => v.actionId)).toEqual(['oem.x.stop', 'oem.y.stop', 'oem.z.stop']);
            expect(state.componentStopCalls).toHaveLength(1);
            await act(async () => reject(new Error('offline response lost')));
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(move.disabled).toBe(false);
            expect(api.post).toHaveBeenCalledTimes(1);
        } finally {
            await act(async () => root.render(null));
            client.clear(); nativeMetadataMode.protocols = false; vi.unstubAllGlobals();
        }
    });
    it('sends exactly one software cancellation and no addressed fanout; independent XYZG Stops remain reachable while abort pending', async () => {
        state.catalog.data.actions.push({ ...xMoveAction(), action_id: 'component-stop', informational_path: '/motion/diagnostics/stop', safety_class: 'stop', enabled: true });
        await render();
        expect(container.textContent).not.toContain('Emergency Stop');
        expect(container.textContent).toContain('Motors may continue');
        await act(async () => abort().click());
        expect(state.yInterruptCalls).toEqual([expect.objectContaining({ actionId: 'oem.abort_all', request: expect.objectContaining({ expected_connection_generation: 1, reason: 'BMS operator requested OEM software Abort: cancel waiters only; motors may continue' }) })]);
        expect(state.invokeCalls).toHaveLength(0);
        expect(state.yInvokeCalls).toHaveLength(0);
        state.softwarePending = true;
        await render();
        expect(abort().disabled).toBe(true);
        await act(async () => abort().click());
        expect(state.yInterruptCalls).toHaveLength(1);
        for (const stop of stops()) {
            expect(stop.disabled).toBe(false);
            await act(async () => stop.click());
        }
        expect(state.yInterruptCalls.slice(1).map(v => v.actionId)).toEqual(['oem.x.stop', 'oem.y.stop', 'oem.z.stop']);
        expect(state.componentStopCalls).toHaveLength(1);
        expect(state.componentStopCalls[0]).toMatchObject({ actionId: 'component-stop', inputs: { axis: 'g' } });
    });
    it.each(['missing', 'disabled', 'disconnected', 'generation'] as const)('software Abort respects %s availability without physical dispatch', async mode => {
        if (mode === 'missing') state.v2Catalog.data.actions = state.v2Catalog.data.actions.filter(a => a.action_id !== 'oem.abort_all');
        if (mode === 'disabled') Object.assign(state.v2Catalog.data.actions.find(a => a.action_id === 'oem.abort_all')!, { enabled: false, disabled_reason: 'Software abort provider unavailable' });
        if (mode === 'disconnected') state.connected = false;
        if (mode === 'generation') state.connectionGeneration = 0;
        await render();
        expect(abort().disabled).toBe(true);
        if (mode === 'disabled') expect(abort().title).toContain('provider unavailable');
        await act(async () => abort().click());
        expect(state.yInterruptCalls).toHaveLength(0);
        expect(state.invokeCalls).toHaveLength(0);
    });
});
