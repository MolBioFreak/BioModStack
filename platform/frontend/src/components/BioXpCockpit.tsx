import { useEffect, useMemo, useRef, useState } from 'react';

import {
    bioXpErrorPresentation,
    bioXpErrorText,
    bioXpPostDispatchCommandIdentity,
    BIOXP_Y_ABSOLUTE_MAX_STEPS,
    BIOXP_Y_ABSOLUTE_MIN_STEPS,
    BIOXP_Y_RELATIVE_MAX_STEPS,
    useBioXpStatus,
    useConnectBioXp,
    useDisconnectBioXp,


    useBioXpOperatorActionHistory,
    useBioXpOperatorControlCatalog,
    useBioXpOperatorControlCatalogV2,
    useBioXpOperatorReceiptV2,
    useInterruptBioXpOperatorActionV1,
    useInvokeBioXpOperatorActionV2,
    useInvokeBioXpDeckActionV2,
    useInvokeBioXpOperatorAction,
    type BioXpOperatorActionV2Request,
    type BioXpOperatorLiveActionReceipt,
    type BioXpOperatorDashboardXAxis,
    type BioXpOperatorHistoryReceipt,
    type BioXpOperatorInputSpec,
    type BioXpOperatorLegacyReconciliationReceipt,
    type BioXpOperatorReceiptV2,
} from '../lib/bioxpClient';
import { bioXpReceiptTimestampText } from '../lib/bioxpReceiptTimestamp';
import { BioXpCameraPanel } from './BioXpCameraPanel';
import { BioXpOperatorControlTabs } from './BioXpOperatorControlTabs';
import { BioXpPipetteControlPanel } from './BioXpPipetteControlPanel';
import { BioXpQuickDashboard } from './BioXpQuickDashboard';
import { BioXpOperatorReports } from './BioXpOperatorReports';


type Axis = 'x' | 'z' | 'g' | 'door';
type Operation =
    | 'move-negative'
    | 'move-positive'
    | 'home'
    | 'commission-home'
    | 'close'
    | 'open'
    | 'open-wide';

interface Control {
    label: string;
    operation: Operation;
}

interface AxisControls {
    axis: Axis;
    label: string;
    controls: readonly Control[];
}

const AXES: readonly AxisControls[] = [
    {
        axis: 'x',
        label: 'X Axis',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'z',
        label: 'Z Axis',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'g',
        label: 'Gripper',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'commission-home' },
            { label: 'Move +', operation: 'move-positive' },
            { label: 'Open', operation: 'open' },
            { label: 'Close', operation: 'close' },
            { label: 'Open Wide', operation: 'open-wide' },
        ],
    },
    {
        axis: 'door',
        label: 'Thermal Door',
        controls: [
            { label: 'Home', operation: 'home' },
            { label: 'Open', operation: 'open' },
            { label: 'Close', operation: 'close' },
        ],
    },
];

function isIndexedHistoryReceipt(
    receipt: BioXpOperatorHistoryReceipt,
): receipt is BioXpOperatorLiveActionReceipt | BioXpOperatorLegacyReconciliationReceipt {
    return 'command_id' in receipt && 'action_id' in receipt && 'status' in receipt;
}

const actionClass = 'rounded bg-cyan-700 px-3 py-2 text-sm font-semibold hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-35';
let fallbackIdempotencySequence = 0;
const nextIdempotencyKey = (prefix: string): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    fallbackIdempotencySequence += 1;
    return `${prefix}-${fallbackIdempotencySequence}`;
};

const numericRecordEqual = (
    left: Record<string, number> | null | undefined,
    right: Record<string, number> | null | undefined,
): boolean => {
    if (left == null || right == null) return false;
    const leftEntries = Object.entries(left).sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey));
    const rightEntries = Object.entries(right).sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey));
    return leftEntries.length === rightEntries.length
        && leftEntries.every(([key, value], index) => key === rightEntries[index][0] && value === rightEntries[index][1]);
};

const integerMinimum = (input: BioXpOperatorInputSpec | undefined): number | undefined => {
    if (typeof input?.minimum === 'number') return Math.ceil(input.minimum);
    if (typeof input?.exclusive_minimum === 'number') return Math.floor(input.exclusive_minimum) + 1;
    return undefined;
};

const integerMaximum = (input: BioXpOperatorInputSpec | undefined): number | undefined => {
    if (typeof input?.maximum === 'number') return Math.floor(input.maximum);
    if (typeof input?.exclusive_maximum === 'number') return Math.ceil(input.exclusive_maximum) - 1;
    return undefined;
};

const integerInputError = (
    value: number,
    input: BioXpOperatorInputSpec | undefined,
    label: string,
): string | null => {
    const minimum = integerMinimum(input);
    const maximum = integerMaximum(input);
    if (!Number.isInteger(value)) return `${label} must be an integer.`;
    if ((minimum !== undefined && value < minimum) || (maximum !== undefined && value > maximum)) {
        if (minimum !== undefined && maximum !== undefined) {
            return `${label} must be an integer from ${minimum} through ${maximum}.`;
        }
        if (minimum !== undefined) return `${label} must be an integer greater than or equal to ${minimum}.`;
        return `${label} must be an integer less than or equal to ${maximum}.`;
    }
    return null;
};

const relativeMagnitudeMaximum = (input: BioXpOperatorInputSpec | undefined): number | undefined => {
    const minimum = integerMinimum(input);
    const maximum = integerMaximum(input);
    if (minimum === undefined || maximum === undefined) return undefined;
    return Math.max(Math.abs(minimum), Math.abs(maximum));
};

function isDispatchedOutcomeAmbiguous(error: unknown): boolean {
    const response = (error as { response?: { status?: unknown; data?: { detail?: unknown } } })?.response;
    const detail = response?.data?.detail;
    if (detail !== null && typeof detail === 'object' && !Array.isArray(detail)
        && (detail as Record<string, unknown>).error === 'post_dispatch_receipt_validation_failed') return true;
    return response?.status === 504
        && detail !== null
        && typeof detail === 'object'
        && !Array.isArray(detail)
        && (detail as Record<string, unknown>).error === 'bioxp_robot_timeout'
        && (detail as Record<string, unknown>).dispatch_state === 'outcome_ambiguous';
}

function YOperatorError({
    label,
    error,
    reconcileAmbiguousOutcome = false,
}: {
    label: string;
    error: unknown;
    reconcileAmbiguousOutcome?: boolean;
}) {
    if (error == null) return null;
    const presentation = bioXpErrorPresentation(error);
    const outcomeAmbiguous = reconcileAmbiguousOutcome && isDispatchedOutcomeAmbiguous(error);
    return (
        <div role="alert" className={`mt-2 rounded border p-2 text-xs ${outcomeAmbiguous ? 'border-amber-700/70 bg-amber-950/30 text-amber-100' : 'border-red-800/70 bg-red-950/30 text-red-200'}`}>
            <p className="font-semibold">{label} {outcomeAmbiguous ? 'result pending' : 'failed'} · {presentation.status == null ? 'HTTP status unavailable' : `HTTP ${presentation.status}`} · {presentation.summary}</p>
            {outcomeAmbiguous && <p className="mt-1">The robot may have accepted the command. Checking the current robot receipt. Do not retry.</p>}
            <details className="mt-1">
                <summary>Raw bounded robot/BMS response</summary>
                <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all">{presentation.rawJson}</pre>
            </details>
        </div>
    );
}

function InterruptOutcome({ label, receipt, error, pending, generation, connected }: {
    label: string;
    receipt: BioXpOperatorReceiptV2 | undefined;
    error: unknown;
    pending: boolean;
    generation: number;
    connected: boolean;
}) {
    const identity = bioXpPostDispatchCommandIdentity(error);
    const commandId = receipt?.command_id ?? identity?.commandId ?? null;
    const query = useBioXpOperatorReceiptV2(commandId, generation, connected && (identity !== null || receipt?.terminal === false));
    const current = query.data?.command_id === commandId ? query.data : receipt;
    if (!pending && !current && !error) return null;
    const evidence = current?.interrupt_evidence;
    const uncertain = pending || !current?.terminal || current?.status === 'ambiguous'
        || error != null || query.isError || evidence == null || evidence.persistence_state !== 'committed';
    const truth = (value: boolean | null | undefined) => value === true ? 'yes' : value === false ? 'no' : 'unknown';
    return <div role="status" className={`mt-2 rounded border p-2 text-xs ${uncertain ? 'border-amber-700 text-amber-200' : current?.status === 'failed' || evidence?.source_return_ok === false ? 'border-red-800 text-red-200' : 'border-slate-700 text-slate-200'}`}>
        <p className="font-semibold">{label} · {current?.status ?? (pending ? 'submitting' : 'outcome unknown')} · {commandId ?? 'command identity unavailable'}</p>
        <p>Source call completed: {truth(evidence?.source_call_completed)} · Source return OK: {truth(evidence?.source_return_ok)}</p>
        <p>Controller stop ACK: {truth(evidence?.controller_stop_acknowledged)} · Controller terminal state verified: {truth(evidence?.controller_terminal_state_verified)} · Physical effect unverified</p>
        <p>Receipt persistence: {evidence?.persistence_state ?? 'unknown'}</p>
        {uncertain && <p>Outcome or persistence unresolved. Do not resubmit this command; reconcile the retained command identity.</p>}
        {error != null && <p>{bioXpErrorText(error)}</p>}
        {query.error != null && <p>Receipt lookup: {bioXpErrorText(query.error)}</p>}
        <details><summary>Actual robot stop evidence, components and latch</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(current ?? bioXpErrorPresentation(error), null, 2)}</pre></details>
    </div>;
}

export function BioXpCockpit() {
    const statusQuery = useBioXpStatus(true);
    const status = statusQuery.isError ? undefined : statusQuery.data;
    const connection = status?.connection;
    const active = connection?.active === true;
    const linkConnected = active && connection?.reachable !== false;
    const robotControlReady = linkConnected
        && connection?.runtime_ready === true;
    const configured = connection?.configured === true;
    const generation = connection?.generation ?? 0;
    const currentGenerationRef = useRef(generation);
    useEffect(() => {
        currentGenerationRef.current = generation;
    }, [generation]);
    const [historyLimit, setHistoryLimit] = useState<8 | 25 | 50 | 100>(25);
    const [reportsOpen, setReportsOpen] = useState(false);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [cameraOpen, setCameraOpen] = useState(false);
    const [pipettesOpen, setPipettesOpen] = useState(false);
    const catalogV2Query = useBioXpOperatorControlCatalogV2(generation, linkConnected);
    // One catalog snapshot owns admission and its embedded dashboard. Cache
    // receipt time never renews the upstream observation's freshness budget.
    const [authorityNow, setAuthorityNow] = useState(Date.now);
    useEffect(() => {
        const timer = window.setInterval(() => setAuthorityNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, []);
    const upstreamGeneratedAt = catalogV2Query.data?.dashboard.generated_at;
    const upstreamAgeMs = typeof upstreamGeneratedAt === 'number'
        ? Math.max(0, authorityNow - upstreamGeneratedAt * 1000) : Infinity;
    const localAgeMs = Math.max(0, authorityNow - catalogV2Query.dataUpdatedAt);
    const currentCatalogV2 = linkConnected && catalogV2Query.error == null && !catalogV2Query.isStale
        && localAgeMs < 15_000 && upstreamAgeMs < 15_000
        ? catalogV2Query.data : undefined;
    const currentDashboardV2 = currentCatalogV2?.dashboard;
    const currentTelemetry = currentDashboardV2?.telemetry ?? undefined;
    const [yCommandId, setYCommandId] = useState<string | null>(null);
    const [zHomeCommandId, setZHomeCommandId] = useState<string | null>(null);
    const [lifecycleCommandId, setLifecycleCommandId] = useState<string | null>(null);
    const [lifecycleActionId, setLifecycleActionId] = useState<'meta.activate_motion' | 'meta.recover_motion_non_homing' | null>(null);
    const [lifecycleOwnershipGeneration, setLifecycleOwnershipGeneration] = useState<number | null>(null);
    const [lifecycleDashboardBaselineAt, setLifecycleDashboardBaselineAt] = useState<number | null>(null);
    const [yPendingActionId, setYPendingActionId] = useState<string | null>(null);
    const [deckCommandId, setDeckCommandId] = useState<string | null>(null);
    const [yMutationGeneration, setYMutationGeneration] = useState<number | null>(null);
    const [zHomeMutationGeneration, setZHomeMutationGeneration] = useState<number | null>(null);
    const [lifecycleMutationGeneration, setLifecycleMutationGeneration] = useState<number | null>(null);
    const [deckMutationGeneration, setDeckMutationGeneration] = useState<number | null>(null);
    const lifecycleGenerationCurrent = lifecycleMutationGeneration === generation;
    const zHomeGenerationCurrent = zHomeMutationGeneration === generation;
    const currentZHomeCommandId = zHomeGenerationCurrent ? zHomeCommandId : null;
    const currentLifecycleCommandId = lifecycleGenerationCurrent ? lifecycleCommandId : null;
    const currentLifecycleActionId = lifecycleGenerationCurrent ? lifecycleActionId : null;
    const [deckTarget, setDeckTarget] = useState('');
    const [yStepInput, setYStepInput] = useState(1000);
    const [yTargetInput, setYTargetInput] = useState(0);
    const v2AuthorityCoherent = currentCatalogV2 !== undefined;
    const yAxisV2 = v2AuthorityCoherent ? currentDashboardV2?.y_axis : undefined;
    const yReceiptCommandId = yCommandId
        ?? yAxisV2?.active_command?.command_id
        ?? yAxisV2?.latest_compact_receipt?.command_id
        ?? null;
    const yReceiptQuery = useBioXpOperatorReceiptV2(yReceiptCommandId, generation, robotControlReady);
    const zHomeReceiptQuery = useBioXpOperatorReceiptV2(currentZHomeCommandId, generation, robotControlReady);
    const lifecycleReceiptQuery = useBioXpOperatorReceiptV2(currentLifecycleCommandId, generation, linkConnected);
    const deckReceiptQuery = useBioXpOperatorReceiptV2(deckCommandId, generation, robotControlReady);
    const invokeLifecycleActionMutation = useInvokeBioXpOperatorActionV2();
    const invokeYAction = useInvokeBioXpOperatorActionV2();
    const invokeDeckAction = useInvokeBioXpDeckActionV2();
    const interruptXStop = useInterruptBioXpOperatorActionV1();
    const interruptYStop = useInterruptBioXpOperatorActionV1();
    const interruptZStop = useInterruptBioXpOperatorActionV1();
    const interruptAggregateAbort = useInterruptBioXpOperatorActionV1();
    const invokeXYAction = useInvokeBioXpOperatorActionV2();
    const [xySubmission, setXYSubmission] = useState<{ generation: number; commandId: string; receipt: BioXpOperatorReceiptV2 | null } | null>(null);
    const currentXYSubmission = linkConnected && xySubmission?.generation === generation ? xySubmission : null;
    const xyReceiptQuery = useBioXpOperatorReceiptV2(currentXYSubmission?.commandId ?? null, generation, linkConnected);
    const xyReceipt = currentXYSubmission == null ? null
        : xyReceiptQuery.data?.command_id === currentXYSubmission.commandId ? xyReceiptQuery.data : currentXYSubmission.receipt;
    const xyOutcomeUnresolved = currentXYSubmission != null && (xyReceipt == null || !xyReceipt.terminal || xyReceipt.status === 'ambiguous' || xyReceiptQuery.isError);
    const xyPending = invokeXYAction.isPending || xyOutcomeUnresolved || isDispatchedOutcomeAmbiguous(invokeXYAction.error);
    const acceptXYSubmission = (receipt: BioXpOperatorReceiptV2) => {
        if (currentGenerationRef.current !== generation) return;
        setXYSubmission({ generation, commandId: receipt.command_id, receipt });
    };
    const retainXYUncertainty = (error: unknown) => {
        if (currentGenerationRef.current !== generation) return;
        const identity = bioXpPostDispatchCommandIdentity(error);
        if (identity) setXYSubmission({ generation, commandId: identity.commandId, receipt: null });
    };
    const historyQuery = useBioXpOperatorActionHistory(generation, linkConnected, historyLimit);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const operatorCatalog = useBioXpOperatorControlCatalog(
        generation,
        linkConnected,
        null,
    );
    const invokeOperatorAction = useInvokeBioXpOperatorAction();

    const resetInvokeOperatorAction = invokeOperatorAction.reset;
    const resetInvokeLifecycleAction = invokeLifecycleActionMutation.reset;
    const resetInvokeYAction = invokeYAction.reset;
    const resetInvokeDeckAction = invokeDeckAction.reset;
    const resetInterruptXStop = interruptXStop.reset;
    const resetInterruptYStop = interruptYStop.reset;
    const resetInterruptZStop = interruptZStop.reset;
    const resetInterruptAggregateAbort = interruptAggregateAbort.reset;
    const resetInvokeXYAction = invokeXYAction.reset;
    const [manualSteps, setManualSteps] = useState<Record<'x' | 'z' | 'g', number>>({
        x: 10000,
        z: 10000,
        g: 10000,
    });
    const [absoluteTargets, setAbsoluteTargets] = useState<Record<'x' | 'z' | 'g', number>>({
        x: 60,
        z: 65000,
        g: 0,
    });

    const catalog = !linkConnected || operatorCatalog.isError ? undefined : operatorCatalog.data;
    const dashboard = !robotControlReady || catalogV2Query.isError ? undefined : currentTelemetry;
    const ownershipGeneration = currentDashboardV2?.ownership_generation ?? 0;

    const ownership = connection?.ownership;
    const ownershipLabel = ownership
        ? `${ownership.transport ?? 'unknown'} / ${ownership.usb ?? 'unknown'} / ${ownership.router ?? 'unknown'}`
        : 'Unavailable';
    const motionControlsAvailable = dashboard === undefined || connection?.hardware_fresh !== true
        ? undefined
        : dashboard.motion.enabled === true;
    const motionLabel = motionControlsAvailable === true
        ? 'Available — exact recovered-OEM controls admitted'
        : motionControlsAvailable === false
            ? `Blocked${dashboard?.motion.reason ? ` — ${dashboard.motion.reason}` : ''}`
            : 'Updating';
    const recentCommands = useMemo(
        () => (!linkConnected || historyQuery.isError ? [] : (historyQuery.data?.receipts ?? [])).filter(isIndexedHistoryReceipt).slice(0, historyLimit),
        [historyQuery.data?.receipts, historyQuery.isError, linkConnected, historyLimit],
    );
    useEffect(() => {
        resetInvokeOperatorAction();
    }, [generation, linkConnected, resetInvokeOperatorAction]);
    useEffect(() => {
        setYCommandId(null);
        setZHomeCommandId(null);
        setLifecycleCommandId(null);
        setLifecycleActionId(null);
        setLifecycleOwnershipGeneration(null);
        setLifecycleDashboardBaselineAt(null);
        setYPendingActionId(null);
        setDeckCommandId(null);
        setYMutationGeneration(null);
        setZHomeMutationGeneration(null);
        setLifecycleMutationGeneration(null);
        setDeckMutationGeneration(null);
        resetInvokeYAction();
        resetInvokeLifecycleAction();
        resetInvokeDeckAction();
        resetInterruptXStop();
        resetInterruptYStop();
        resetInterruptZStop();
        resetInterruptAggregateAbort();
        resetInvokeXYAction();
        setXYSubmission(null);
    }, [generation, linkConnected, resetInterruptAggregateAbort, resetInterruptXStop, resetInterruptYStop, resetInterruptZStop, resetInvokeDeckAction, resetInvokeLifecycleAction, resetInvokeXYAction, resetInvokeYAction]);
    const interruptMutation = (actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all') => {
        if (actionId === 'oem.x.stop') return interruptXStop;
        if (actionId === 'oem.y.stop') return interruptYStop;
        if (actionId === 'oem.z.stop') return interruptZStop;
        return interruptAggregateAbort;
    };
    const interruptPending = (actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all') => interruptMutation(actionId).isPending;
    const interruptAnyPending = interruptXStop.isPending || interruptYStop.isPending || interruptZStop.isPending || interruptAggregateAbort.isPending;
    const busy = invokeOperatorAction.isPending || invokeLifecycleActionMutation.isPending || invokeYAction.isPending || invokeDeckAction.isPending || xyPending || interruptAnyPending;
    const latestOperatorReceipt = interruptAggregateAbort.data ?? interruptZStop.data ?? interruptYStop.data ?? interruptXStop.data ?? invokeDeckAction.data ?? invokeLifecycleActionMutation.data ?? invokeYAction.data ?? xyReceipt ?? invokeOperatorAction.data;
    const connectedLabel = active
        ? connection?.reachable === false ? 'Connection error' : 'Connected'
        : 'Disconnected';

    const operatorActionForPath = (path: string) => (catalog?.actions ?? []).find(
        (action) => action.kind === 'primitive' && action.informational_path === path,
    );

    const operatorActionById = (actionId: string) => (catalog?.actions ?? []).find(
        (action) => action.action_id === actionId,
    );
    const v2CatalogActionById = (actionId: string) => (currentCatalogV2?.actions ?? []).find(
        (action) => action.action_id === actionId,
    );
    const v2NormalActionById = (actionId: string) => v2AuthorityCoherent
        ? v2CatalogActionById(actionId)
            && (currentCatalogV2?.actions ?? []).find(
                (action) => action.action_id === actionId
                    && action.interrupt === false
                    && action.request_schema_version === 'bioxp.operator_action_request.v2'
                    && action.response_schema_version === 'bioxp.operator_action_receipt.v2',
            )
        : undefined;
    const deckAction = v2NormalActionById('oem.deck.move_to_location');
    const catalogDeck = currentCatalogV2?.dashboard.deck;
    const dashboardDeck = currentDashboardV2?.deck;
    const deckAuthorityCoherent = v2AuthorityCoherent
        && deckAction !== undefined
        && catalogDeck != null
        && dashboardDeck != null
        && currentCatalogV2?.dashboard.ownership_generation === currentDashboardV2?.ownership_generation
        && catalogDeck.ownership_generation === currentDashboardV2?.ownership_generation
        && dashboardDeck.ownership_generation === currentDashboardV2?.ownership_generation
        && deckAction.destination_catalog_revision === catalogDeck.destination_catalog_revision
        && catalogDeck.destination_catalog_revision === dashboardDeck.destination_catalog_revision
        && deckAction.position_table_revision === catalogDeck.position_table_revision
        && catalogDeck.position_table_revision === dashboardDeck.position_table_revision
        && numericRecordEqual(deckAction.expected_board_epoch_by_board, catalogDeck.expected_board_epoch_by_board)
        && numericRecordEqual(catalogDeck.expected_board_epoch_by_board, dashboardDeck.expected_board_epoch_by_board)
        && JSON.stringify(deckAction.destinations) === JSON.stringify(catalogDeck.destinations)
        && JSON.stringify(catalogDeck.destinations) === JSON.stringify(dashboardDeck.destinations);
    const deckDestinations = useMemo(() => deckAction?.destinations ?? [], [deckAction]);
    const selectedDeckDestination = deckDestinations.find((destination) => destination.key === deckTarget)
        ?? deckDestinations[0];
    useEffect(() => {
        if (deckDestinations.length === 0) {
            setDeckTarget('');
            return;
        }
        setDeckTarget((current) => deckDestinations.some((destination) => destination.key === current)
            ? current
            : deckDestinations[0].key);
    }, [deckDestinations]);
    const v2InterruptActionById = (actionId: string) => v2CatalogActionById(actionId)
        && (currentCatalogV2?.actions ?? []).find(
        (action) => action.action_id === actionId
            && action.interrupt === true
            && action.request_schema_version === 'bioxp.operator_interrupt_request.v1'
            && action.response_schema_version === 'bioxp.operator_action_receipt.v2',
    );
    const yActionDisabledReason = (actionId: string, fallback: string) => {
        const action = v2NormalActionById(actionId);
        if (action == null) return fallback;
        if (action.enabled !== true) return action.disabled_reason ?? fallback;
        if (actionId === 'oem.y.move_steps' && !yStepMagnitudeValid) {
            return `Step magnitude must be an integer from 0 through ${BIOXP_Y_RELATIVE_MAX_STEPS}.`;
        }
        if (actionId === 'oem.y.move_absolute' && !yTargetInputValid) {
            return `Absolute target must be an integer from ${BIOXP_Y_ABSOLUTE_MIN_STEPS} through ${BIOXP_Y_ABSOLUTE_MAX_STEPS}.`;
        }
        return 'Direct recovered-OEM command.';
    };
    const actionUnavailableReason = (actionId: string, fallback: string) => {
        const action = operatorActionById(actionId);
        return action?.disabled_reason
            ?? action?.provider_unavailable_reason
            ?? action?.unavailable_reason
            ?? fallback;
    };
    const xMoveAction = operatorActionById('oem.x.move_steps');
    const xMoveInput = xMoveAction?.inputs.find((input) => input.name === 'steps');
    const xAbsoluteAction = operatorActionById('oem.x.move_absolute');
    const xAbsoluteInput = xAbsoluteAction?.inputs.find((input) => input.name === 'position_steps');
    const xAbsoluteMinimum = integerMinimum(xAbsoluteInput);
    const xAbsoluteMaximum = integerMaximum(xAbsoluteInput);
    const xRelativeMaximum = relativeMagnitudeMaximum(xMoveInput);
    const zMoveCatalogAction = operatorActionById('oem.z.move_steps');
    const zMoveInput = zMoveCatalogAction?.inputs.find((input) => input.name === 'steps');
    const zRelativeMaximum = relativeMagnitudeMaximum(zMoveInput);
    const zAbsoluteCatalogAction = operatorActionById('oem.z.move_absolute');
    const zAbsoluteInput = zAbsoluteCatalogAction?.inputs.find((input) => input.name === 'position_steps');
    const zAbsoluteMinimum = integerMinimum(zAbsoluteInput);
    const zAbsoluteMaximum = integerMaximum(zAbsoluteInput);

    const v2ActionDisabledReason = (actionId: string): string | null => {
        if (!v2AuthorityCoherent) return 'Current robot control state is unavailable.';
        if (busy) return 'A command is pending; wait for its receipt before another normal action.';
        const action = v2NormalActionById(actionId);
        if (!action) return 'Robot action unavailable.';
        return action.enabled === true ? null : action.disabled_reason ?? 'Robot action unavailable.';
    };
    const xNegativeInputs = useMemo(() => ({ steps: -Math.abs(manualSteps.x) }), [manualSteps.x]);
    const xPositiveInputs = useMemo(() => ({ steps: Math.abs(manualSteps.x) }), [manualSteps.x]);
    const xAbsoluteInputs = useMemo(() => ({ position_steps: absoluteTargets.x }), [absoluteTargets.x]);
    const xHomeInputs = useMemo(() => ({}), []);
    const xNegativeDisabledReason = integerInputError(xNegativeInputs.steps, xMoveInput, 'Requested X steps')
        ?? v2ActionDisabledReason('oem.x.move_steps');
    const xPositiveDisabledReason = integerInputError(xPositiveInputs.steps, xMoveInput, 'Requested X steps')
        ?? v2ActionDisabledReason('oem.x.move_steps');
    const xAbsoluteDisabledReason = integerInputError(absoluteTargets.x, xAbsoluteInput, 'Requested X target')
        ?? v2ActionDisabledReason('oem.x.move_absolute');
    const xAbsoluteTargetInRange = integerInputError(absoluteTargets.x, xAbsoluteInput, 'Requested X target') === null;
    const xHomeDisabledReason = v2ActionDisabledReason('oem.x.manual_panel_home');
    const xNegativeEnabled = xNegativeDisabledReason === null;
    const xPositiveEnabled = xPositiveDisabledReason === null;
    const xAbsoluteEnabled = xAbsoluteDisabledReason === null;
    const xHomeEnabled = xHomeDisabledReason === null;
    const zNegativeDisabledReason = integerInputError(-Math.abs(manualSteps.z), zMoveInput, 'Requested Z steps')
        ?? v2ActionDisabledReason('oem.z.move_steps');
    const zPositiveDisabledReason = integerInputError(Math.abs(manualSteps.z), zMoveInput, 'Requested Z steps')
        ?? v2ActionDisabledReason('oem.z.move_steps');
    const zAbsoluteDisabledReason = integerInputError(absoluteTargets.z, zAbsoluteInput, 'Requested Z target')
        ?? v2ActionDisabledReason('oem.z.move_absolute');
    const zHomeDisabledReason = v2ActionDisabledReason('oem.z.manual_home');
    const zNegativeEnabled = zNegativeDisabledReason === null;
    const zPositiveEnabled = zPositiveDisabledReason === null;
    const zAbsoluteEnabled = zAbsoluteDisabledReason === null;
    const zHomeEnabled = zHomeDisabledReason === null;
    const xAxisDashboard: BioXpOperatorDashboardXAxis | undefined = dashboard?.x_axis;
    const xStatus = xAxisDashboard?.status;
    const xProvider = xAxisDashboard?.provider;
    const xLiveStatus = xProvider?.live_status;
    const xPosition = xStatus?.position_steps ?? xLiveStatus?.position_steps ?? 'unknown';
    const xReference = xStatus?.reference ?? xProvider?.lifecycle?.reference_state ?? xProvider?.reference_state ?? 'unknown';
    const xLifecycle = xProvider?.lifecycle?.state ?? xProvider?.state ?? 'unknown';
    const xAuthority = xProvider?.authority ?? xAxisDashboard?.authority ?? 'unknown';
    const xLeftSwitchState = xStatus?.left_switch_state ?? xLiveStatus?.left_switch_state ?? 'unknown';
    const xRightSwitchState = xStatus?.right_switch_state ?? xLiveStatus?.right_switch_state ?? 'unknown';
    const xLeftSwitchDisabled = xStatus?.left_switch_disabled ?? xLiveStatus?.left_switch_disabled ?? 'unknown';
    const xRightSwitchDisabled = xStatus?.right_switch_disabled ?? xLiveStatus?.right_switch_disabled ?? 'unknown';
    const xProfileVerified = xProvider?.profile?.verified ?? xLiveStatus?.profile_verified;
    const xSwitchMaskTuple = xProvider?.switch_masks?.observed ?? xLiveStatus?.switch_mask_tuple;
    const xMaxSpeed = xLiveStatus?.max_speed ?? 'unknown';
    const xMaxAcceleration = xLiveStatus?.max_acceleration ?? 'unknown';
    const xMaxCurrent = xLiveStatus?.max_current ?? 'unknown';
    const xStallGuard = xLiveStatus?.stall_guard ?? 'unknown';
    const xGeneration = xProvider?.current_generation ?? 'unknown';
    const xBoardGeneration = xProvider?.current_board_lifecycle_generation ?? 'unknown';
    const xBoardGenerationFresh = xProvider?.board_generation_fresh;
    const xLastFailure = xAxisDashboard?.last_failure ?? xProvider?.lifecycle?.last_failure;
    const xHistoryReceipt = historyQuery.data?.receipts?.find(
        (receipt) => isIndexedHistoryReceipt(receipt) && receipt.action_id.startsWith('oem.x.'),
    ) ?? null;
    const xReceipt = xHistoryReceipt
        ?? xAxisDashboard?.latest_receipt
        ?? xProvider?.lifecycle?.latest_receipt
        ?? null;

    const invokeAction = (
        actionId: string,
        inputs: Record<string, unknown>,
        mutation = invokeOperatorAction,
    ) => {
        mutation.mutate({ actionId, connectionGeneration: generation, ownershipGeneration, inputs });
    };

    const invokeLifecycleAction = (actionId: 'meta.activate_motion' | 'meta.recover_motion_non_homing') => {
        if (v2ActionDisabledReason(actionId) !== null) return;
        const envelope = v2NormalEnvelope();
        if (!envelope) return;
        const submittedGeneration = generation;
        const submittedOwnershipGeneration = envelope.expected_ownership_generation;
        setLifecycleMutationGeneration(submittedGeneration);
        setLifecycleActionId(actionId);
        setLifecycleCommandId(null);
        setLifecycleOwnershipGeneration(submittedOwnershipGeneration);
        setLifecycleDashboardBaselineAt(currentDashboardV2?.generated_at ?? null);
        invokeLifecycleActionMutation.mutate({ request: { ...envelope, action_id: actionId, inputs: {} } }, {
            onSuccess: (receipt) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                if (receipt.ownership_generation !== submittedOwnershipGeneration) return;
                setLifecycleCommandId(receipt.command_id);
            },
            onError: (error) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                const identity = bioXpPostDispatchCommandIdentity(error);
                if (identity !== null) setLifecycleCommandId(identity.commandId);
            },
        });
    };
    const claimTransport = () => invokeLifecycleAction('meta.activate_motion');
    const recoverMotionNonHoming = () => invokeLifecycleAction('meta.recover_motion_non_homing');

    const operatorPathForControl = (axis: Axis, operation: Operation): string | null => {
        if (operation === 'move-negative' || operation === 'move-positive') return '/motion/oem/manual/relative';
        if (operation === 'home' || operation === 'commission-home') return '/motion/oem/manual/home';
        if (axis === 'g') {
            return ({ open: '/motion/gripper/open', close: '/motion/gripper/close', 'open-wide': '/motion/gripper/open_wide' } as const)[operation as 'open' | 'close' | 'open-wide'] ?? null;
        }
        if (axis === 'door') {
            return ({ open: '/motion/thermal_door/open', close: '/motion/thermal_door/close' } as const)[operation as 'open' | 'close'] ?? null;
        }
        return null;
    };

    const invokeOperatorPath = (path: string, inputs: Record<string, unknown>) => {
        const action = operatorActionForPath(path);
        if (!action) return;
        invokeAction(action.action_id, inputs);
    };

    const runControl = (axis: Axis, operation: Operation) => {
        if (axis === 'x') {
            if (operation === 'move-negative') {
                if (!xNegativeEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.x.move_steps', inputs: xNegativeInputs });
            } else if (operation === 'move-positive') {
                if (!xPositiveEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.x.move_steps', inputs: xPositiveInputs });
            } else if (operation === 'home' || operation === 'commission-home') {
                if (!xHomeEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.x.manual_panel_home', inputs: xHomeInputs });
            }
            return;
        }
        if (axis === 'z') {
            if (operation === 'move-negative') {
                if (!zNegativeEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.z.move_steps', inputs: { steps: -Math.abs(manualSteps.z) } });
            } else if (operation === 'move-positive') {
                if (!zPositiveEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.z.move_steps', inputs: { steps: Math.abs(manualSteps.z) } });
            } else if (operation === 'home' || operation === 'commission-home') {
                if (!zHomeEnabled) return;
                const envelope = v2NormalEnvelope();
                if (envelope) submitV2({ ...envelope, action_id: 'oem.z.manual_home', inputs: {} });
            }
            return;
        }
        if (operation === 'move-negative' || operation === 'move-positive') {
            if (axis === 'door') return;
            const magnitude = Math.abs(manualSteps[axis]);
            invokeOperatorPath('/motion/oem/manual/relative', {
                axis,
                steps: operation === 'move-negative' ? -magnitude : magnitude,
            });
            return;
        }
        if (operation === 'home' || operation === 'commission-home') {
            invokeOperatorPath('/motion/oem/manual/home', { axis });
            return;
        }
        const path = axis === 'g'
            ? ({ open: '/motion/gripper/open', close: '/motion/gripper/close', 'open-wide': '/motion/gripper/open_wide' } as const)[operation as 'open' | 'close' | 'open-wide']
            : ({ open: '/motion/thermal_door/open', close: '/motion/thermal_door/close' } as const)[operation as 'open' | 'close'];
        if (path) invokeOperatorPath(path, {});
    };

    const runAbsolute = (axis: 'x' | 'z' | 'g') => {
        if (axis === 'x') {
            if (!xAbsoluteEnabled) return;
            const envelope = v2NormalEnvelope();
            if (envelope) submitV2({ ...envelope, action_id: 'oem.x.move_absolute', inputs: xAbsoluteInputs });
            return;
        }
        if (axis === 'z') {
            if (!zAbsoluteEnabled) return;
            const envelope = v2NormalEnvelope();
            if (envelope) submitV2({ ...envelope, action_id: 'oem.z.move_absolute', inputs: { position_steps: absoluteTargets.z } });
            return;
        }
        invokeOperatorPath('/motion/oem/manual/absolute', { axis, position_steps: absoluteTargets[axis] });
    };

    const invokeInterrupt = (
        actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all',
        reason: string,
    ) => {
        if (!linkConnected || generation <= 0) return;
        const idempotencyKey = nextIdempotencyKey('bioxp-stop');
        interruptMutation(actionId).mutate({
            actionId,
            request: {
                expected_connection_generation: generation,
                schema_version: 'bioxp.operator_interrupt_request.v1',
                idempotency_key: idempotencyKey,
                reason,
                observed_ownership_generation: currentDashboardV2?.ownership_generation ?? null,
                observed_board_epoch_by_board: {},
            },
        });
    };
    const stopAxis = (axis: Axis) => axis === 'x'
        ? invokeInterrupt('oem.x.stop', 'BMS operator requested recovered-OEM X STOP')
        : axis === 'z'
            ? invokeInterrupt('oem.z.stop', 'BMS operator requested recovered-OEM Z STOP')
            : invokeOperatorPath('/motion/diagnostics/stop', { axis });

    const abortXAggregate = () => invokeInterrupt('oem.abort_all', 'BMS operator requested recovered-OEM aggregate abort');

    const submitV2 = (request: BioXpOperatorActionV2Request) => {
        if (v2ActionDisabledReason(request.action_id) !== null) return;
        const submittedGeneration = generation;
        const tracksY = request.action_id.startsWith('oem.y.');
        const tracksZHome = request.action_id === 'oem.z.manual_home';
        setYMutationGeneration(submittedGeneration);
        if (tracksY) {
            setYCommandId(null);
            setYPendingActionId(request.action_id);
        }
        if (tracksZHome) {
            setZHomeMutationGeneration(submittedGeneration);
            setZHomeCommandId(null);
        }
        invokeYAction.mutate({ request }, {
            onSuccess: (receipt) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                if (tracksY) {
                    setYCommandId(receipt.command_id);
                    setYPendingActionId(null);
                }
                if (tracksZHome) setZHomeCommandId(receipt.command_id);
            },
            onError: (error) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                if (tracksY) setYPendingActionId(null);
                if (tracksZHome) {
                    const identity = bioXpPostDispatchCommandIdentity(error);
                    if (identity !== null) setZHomeCommandId(identity.commandId);
                }
            },
        });
    };
    const submitDeckV2 = (request: BioXpOperatorActionV2Request) => {
        if (!deckAuthorityCoherent || request.action_id !== 'oem.deck.move_to_location') return;
        const submittedGeneration = generation;
        setDeckMutationGeneration(submittedGeneration);
        setDeckCommandId(null);
        invokeDeckAction.mutate({ request }, {
            onSuccess: (receipt) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                setDeckCommandId(receipt.command_id);
            },
            onError: (error) => {
                if (currentGenerationRef.current !== submittedGeneration) return;
                const identity = bioXpPostDispatchCommandIdentity(error);
                if (identity !== null) setDeckCommandId(identity.commandId);
            },
        });
    };
    const v2NormalEnvelope = () => {
        if (!v2AuthorityCoherent || currentDashboardV2 == null) return null;
        const idempotencyKey = nextIdempotencyKey('bioxp-oem');
        return {
            expected_connection_generation: generation,
            schema_version: 'bioxp.operator_action_request.v2' as const,
            expected_ownership_generation: currentDashboardV2.ownership_generation,
            idempotency_key: idempotencyKey,
            expected_board_epoch_by_board: {},
        };
    };
    const deckDisabledReason = !v2AuthorityCoherent
        ? 'Fresh v2 catalog or dashboard authority is unavailable.'
        : !deckAuthorityCoherent
            ? 'Fresh matching catalog and dashboard deck authority is unavailable.'
        : deckAction == null
            ? 'Robot deck movement action is unavailable.'
            : deckAction.enabled !== true
                ? deckAction.disabled_reason ?? 'Robot deck movement action is unavailable.'
                : selectedDeckDestination == null
                    ? 'Robot destination catalog is empty.'
                    : null;
    const invokeDeckMove = () => {
        if (deckDisabledReason !== null || selectedDeckDestination == null || deckAction?.expected_board_epoch_by_board == null) return;
        const envelope = v2NormalEnvelope();
        if (!envelope) return;
        submitDeckV2({
            ...envelope,
            action_id: 'oem.deck.move_to_location',
            expected_board_epoch_by_board: deckAction.expected_board_epoch_by_board,
            inputs: { target: selectedDeckDestination.key, camera_offset: false },
        });
    };
    const invokeYMoveSteps = (steps: number) => {
        const envelope = v2NormalEnvelope();
        if (envelope) submitV2({ ...envelope, action_id: 'oem.y.move_steps', inputs: { steps } });
    };
    const invokeYMoveAbsolute = (target_steps: number) => {
        const envelope = v2NormalEnvelope();
        if (envelope) submitV2({ ...envelope, action_id: 'oem.y.move_absolute', inputs: { target_steps } });
    };
    const invokeYHome = (action_id: 'oem.y.manual_panel_home') => {
        const envelope = v2NormalEnvelope();
        if (envelope) submitV2({ ...envelope, action_id, inputs: {} });
    };
    const interruptY = () => {
        invokeInterrupt('oem.y.stop', 'BMS operator requested recovered-OEM addressed Y STOP');
    };
    const yStepMagnitudeValid = Number.isInteger(yStepInput)
        && yStepInput >= 0
        && yStepInput <= BIOXP_Y_RELATIVE_MAX_STEPS;
    const yTargetInputValid = Number.isInteger(yTargetInput)
        && yTargetInput >= BIOXP_Y_ABSOLUTE_MIN_STEPS
        && yTargetInput <= BIOXP_Y_ABSOLUTE_MAX_STEPS;
    const xyMoveDisabledReason = v2ActionDisabledReason('oem.xy.move_absolute')
        ?? (!xAbsoluteTargetInRange || !yTargetInputValid ? 'XY targets must be within the robot input bounds.' : null);
    const xyHomeDisabledReason = v2ActionDisabledReason('oem.xy.home');
    const xyMoveDisabled = xyPending || xyMoveDisabledReason !== null;
    const xyHomeDisabled = xyPending || xyHomeDisabledReason !== null;
    const invokeXYMove = () => {
        const envelope = v2NormalEnvelope();
        if (!envelope || xyMoveDisabled) return;
        invokeXYAction.mutate({ request: { ...envelope, action_id: 'oem.xy.move_absolute',
            inputs: { x: absoluteTargets.x, y: yTargetInput } } },
        { onSuccess: acceptXYSubmission, onError: retainXYUncertainty });
    };
    const invokeXYHome = () => {
        const envelope = v2NormalEnvelope();
        if (!envelope || xyHomeDisabled) return;
        invokeXYAction.mutate({ request: { ...envelope, action_id: 'oem.xy.home', inputs: {} } },
            { onSuccess: acceptXYSubmission, onError: retainXYUncertainty });
    };
    const yMutationDisabled = (actionId: string) =>
        !v2AuthorityCoherent
        || busy
        || v2NormalActionById(actionId)?.enabled !== true
        || (actionId === 'oem.y.move_steps' && !yStepMagnitudeValid)
        || (actionId === 'oem.y.move_absolute' && !yTargetInputValid);
    const yStopDisabled = !linkConnected || generation <= 0 || interruptPending('oem.y.stop');

    const currentYInvokeError = yMutationGeneration === generation ? invokeYAction.error : null;
    const currentLifecycleOwnershipGeneration = lifecycleMutationGeneration === generation
        ? lifecycleOwnershipGeneration
        : null;
    const lifecycleDashboardReceipt = lifecycleGenerationCurrent
        && currentLifecycleActionId !== null
        && currentLifecycleOwnershipGeneration !== null
        && lifecycleDashboardBaselineAt !== null
        && currentDashboardV2?.ownership_generation === currentLifecycleOwnershipGeneration
        ? (currentDashboardV2?.latest_receipts ?? [])
            .filter((receipt) => receipt.action_id === currentLifecycleActionId
                && receipt.ownership_generation === currentLifecycleOwnershipGeneration
                && receipt.accepted_at >= lifecycleDashboardBaselineAt)
            .reduce<BioXpOperatorReceiptV2 | undefined>(
                (latest, receipt) => {
                    if (latest === undefined || receipt.accepted_at > latest.accepted_at) return receipt;
                    if (receipt.accepted_at === latest.accepted_at && receipt.terminal && !latest.terminal) return receipt;
                    return latest;
                },
                undefined,
            )
        : undefined;
    const lifecycleReceipt = lifecycleGenerationCurrent
        && currentLifecycleActionId !== null
        && currentLifecycleOwnershipGeneration !== null
        && lifecycleDashboardBaselineAt !== null
        ? [lifecycleReceiptQuery.data, lifecycleDashboardReceipt, invokeLifecycleActionMutation.data]
            .filter((receipt): receipt is BioXpOperatorReceiptV2 => receipt !== undefined
                && receipt.action_id === currentLifecycleActionId
                && receipt.ownership_generation === currentLifecycleOwnershipGeneration
                && receipt.accepted_at >= lifecycleDashboardBaselineAt
                && (lifecycleCommandId === null || receipt.command_id === lifecycleCommandId))
            .reduce<BioXpOperatorReceiptV2 | undefined>((selected, receipt) => {
                if (selected === undefined || receipt.accepted_at > selected.accepted_at) return receipt;
                if (receipt.accepted_at === selected.accepted_at && receipt.terminal && !selected.terminal) return receipt;
                return selected;
            }, undefined)
        : undefined;
    const currentLifecycleInvokeError = lifecycleMutationGeneration === generation && lifecycleReceipt?.terminal !== true
        ? invokeLifecycleActionMutation.error
        : null;
    const lifecycleStatusRecoveryPending = isDispatchedOutcomeAmbiguous(currentLifecycleInvokeError)
        || (lifecycleReceipt !== undefined && lifecycleReceipt.terminal !== true);
    useEffect(() => {
        if (lifecycleDashboardReceipt !== undefined && lifecycleCommandId === null) {
            setLifecycleCommandId(lifecycleDashboardReceipt.command_id);
        }
    }, [lifecycleCommandId, lifecycleDashboardReceipt]);
    const lifecycleFailureDetail = lifecycleReceipt?.error?.detail;
    const zHomeReceipt = zHomeGenerationCurrent
        && zHomeReceiptQuery.data?.action_id === 'oem.z.manual_home'
        ? zHomeReceiptQuery.data
        : undefined;
    const zHomeFailureDetail = zHomeReceipt?.error?.detail;
    const currentDeckInvokeError = deckMutationGeneration === generation ? invokeDeckAction.error : null;
    const deckReceiptActionMismatch = deckReceiptQuery.data != null
        && deckReceiptQuery.data.action_id !== 'oem.deck.move_to_location';
    const deckReceipt = deckReceiptActionMismatch ? undefined : deckReceiptQuery.data;
    const deckReceiptUnavailable = deckCommandId !== null && deckReceipt == null;
    const deckPending = deckReceipt?.terminal === false;
    const deckAmbiguous = deckReceiptActionMismatch || deckReceipt?.status === 'ambiguous';
    const deckRecoveryRequired = deckReceiptActionMismatch
        || deckAmbiguous
        || deckReceipt?.error?.code === 'reconciliation_required'
        || deckReceipt?.completion_class === 'recovery_required';
    const truthLabel = (value: boolean | null | undefined, positive: string, negative: string) => value === true
        ? positive
        : value === false
            ? negative
            : 'unknown';
    const lifecycleAggregateError = isDispatchedOutcomeAmbiguous(currentLifecycleInvokeError)
        ? null
        : currentLifecycleInvokeError;
    const error = currentDeckInvokeError ?? lifecycleAggregateError ?? currentYInvokeError ?? invokeXYAction.error ?? interruptXStop.error ?? interruptYStop.error ?? interruptZStop.error ?? interruptAggregateAbort.error ?? invokeOperatorAction.error ?? connect.error ?? disconnect.error;

    return (
        <div className="space-y-4 p-4 text-slate-100 md:p-6">
            <header>
                <h1 className="text-2xl font-bold">BioXP 3200</h1>
                <p className="mt-1 text-sm text-slate-400">Direct OEM operator controls</p>
            </header>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Connection & Robot State</h2>
                        <p className={`text-sm ${active ? 'text-emerald-300' : 'text-slate-400'}`}>
                            {connectedLabel}
                        </p>
                        {connection?.last_error && <p className="mt-1 break-words text-sm text-red-300">{connection.last_error}</p>}
                    </div>
                    <div className="flex gap-2">
                        <button
                            type="button"
                            disabled={!configured || linkConnected || connect.isPending || disconnect.isPending}
                            onClick={() => connect.mutate(undefined)}
                            className={actionClass}
                        >{linkConnected ? 'BMS Link Connected' : active ? 'Reconnect BMS Link' : 'Connect BMS Link'}</button>
                        <button
                            type="button"
                            disabled={!active || connect.isPending || disconnect.isPending}
                            onClick={() => disconnect.mutate(undefined)}
                            className="rounded bg-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                        >Disconnect</button>
                    </div>
                </div>
                <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Transport / USB / Router</dt>
                        <dd className="mt-1 break-words font-mono text-slate-100">{ownershipLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Motion</dt>
                        <dd className={`mt-1 break-words ${motionControlsAvailable === false ? 'text-amber-200' : 'text-slate-100'}`}>{motionLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Connection generation</dt>
                        <dd className="mt-1 font-mono text-slate-100">{generation || '—'}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Last robot observation</dt>
                        <dd className="mt-1 text-slate-100">{connection?.observed_at ? new Date(connection.observed_at).toLocaleString() : 'Unavailable'}</dd>
                    </div>
                </dl>
            </section>

            <BioXpQuickDashboard
                connected={linkConnected}
                data={dashboard}
                isLoading={catalogV2Query.isLoading}
                error={catalogV2Query.error}
                motionControlsAvailable={motionControlsAvailable}
            />

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" open={reportsOpen} onToggle={(event) => setReportsOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Operator reports</summary>
                {reportsOpen && <div className="mt-4"><BioXpOperatorReports generation={generation} connected={linkConnected} /></div>}
            </details>

            <section className="rounded-xl border border-amber-700/60 bg-amber-950/20 p-4">
                <h2 className="text-lg font-semibold">Controller Activation & Recovery</h2>
                <p className="mt-1 text-sm text-slate-400">Robot-owned serial-206 activation and typed non-homing recovery. BMS does not maintain a second command registry or receipt ledger.</p>
                <div className="mt-3 flex flex-wrap gap-3">
                    <button
                        type="button"
                        disabled={!linkConnected || v2ActionDisabledReason('meta.activate_motion') !== null || busy || lifecycleStatusRecoveryPending}
                        title={v2ActionDisabledReason('meta.activate_motion') ?? 'Robot-owned OEM activation'}
                        onClick={claimTransport}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Activate 24 V / Prepare Motion</button>
                    <button
                        type="button"
                        disabled={!linkConnected || v2ActionDisabledReason('meta.recover_motion_non_homing') !== null || busy || lifecycleStatusRecoveryPending}
                        title={v2ActionDisabledReason('meta.recover_motion_non_homing') ?? 'Robot-authoritative non-homing recovery'}
                        onClick={recoverMotionNonHoming}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Non-homing Recovery</button>
                </div>
                {v2ActionDisabledReason('meta.activate_motion') !== null && (
                    <p className="mt-2 text-sm text-amber-100">
                        Activate: {v2ActionDisabledReason('meta.activate_motion')}
                    </p>
                )}
                {(currentLifecycleActionId !== null || lifecycleReceipt !== undefined || currentLifecycleInvokeError !== null) && (
                    <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
                        <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Action</dt><dd className="font-mono">{currentLifecycleActionId ?? '—'}</dd></div>
                        <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Command ID</dt><dd className="break-all font-mono">{currentLifecycleCommandId ?? lifecycleReceipt?.command_id ?? '—'}</dd></div>
                        <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Lifecycle</dt><dd className="font-mono">{lifecycleReceipt?.status ?? (invokeLifecycleActionMutation.isPending ? 'submitting' : 'unavailable')}</dd></div>
                    </dl>
                )}
                {lifecycleFailureDetail && (
                    <div role="alert" className="mt-3 rounded border border-red-700/70 bg-red-950/30 p-3 text-sm text-red-100">
                        <p className="font-semibold">{lifecycleFailureDetail.failure}</p>
                        <p>Provider failure: <span className="font-mono">{lifecycleFailureDetail.provider_failure}</span></p>
                        <p>{`Axis ${lifecycleFailureDetail.axis} · Board ${lifecycleFailureDetail.board} · Motor ${lifecycleFailureDetail.motor} · Source return ${lifecycleFailureDetail.source_return_code}`}</p>
                        <p>{`Controller acknowledged: ${lifecycleFailureDetail.controller_acknowledged ? 'yes' : 'no'}`}</p>
                        <p>{`Terminal state verified: ${lifecycleFailureDetail.controller_terminal_state_verified ? 'yes' : 'no'}`}</p>
                        <p>{`Physical effect verified: ${lifecycleFailureDetail.physical_effect_verified ? 'yes' : 'no'}`}</p>
                        <p>{`Lifecycle: ${lifecycleFailureDetail.lifecycle_state} · Reference: ${lifecycleFailureDetail.reference_state}`}</p>
                    </div>
                )}
                <YOperatorError label="Activation / recovery" error={currentLifecycleInvokeError} reconcileAmbiguousOutcome />
                <YOperatorError label="Activation / recovery receipt" error={lifecycleReceiptQuery.error} />
            </section>

            <section data-testid="oem-deck-movement" className="rounded-xl border border-teal-700/60 bg-teal-950/20 p-4">
                <h2 className="text-lg font-semibold">OEM Deck Movement</h2>
                <p className="mt-1 text-sm text-slate-300">Finite robot-owned destinations and robot-selected source semantics.</p>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <label className="text-sm text-slate-300">
                        Robot destination
                        <select
                            value={selectedDeckDestination?.key ?? ''}
                            disabled={deckDestinations.length === 0 || invokeDeckAction.isPending}
                            onChange={(event) => setDeckTarget(event.target.value)}
                            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-slate-100"
                        >
                            {deckDestinations.map((destination) => (
                                <option key={destination.key} value={destination.key}>{destination.label} · {destination.key}</option>
                            ))}
                        </select>
                    </label>
                    <div className="rounded bg-slate-950/60 p-3 text-sm">
                        <p>Canonical key: <span className="font-mono">{selectedDeckDestination?.key ?? '—'}</span></p>
                        <p>Operator label: {selectedDeckDestination?.label ?? '—'}</p>
                    </div>
                </div>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Current semantic location</dt><dd className="font-mono">{currentDashboardV2?.deck?.current_location ?? '—'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Current semantic well</dt><dd className="font-mono">{currentDashboardV2?.deck?.current_well ?? '—'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">PositionTable revision</dt><dd className="font-mono">{deckAction?.position_table_revision ?? currentDashboardV2?.deck?.position_table_revision ?? '—'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Catalog revision</dt><dd className="font-mono">{deckAction?.destination_catalog_revision ?? currentDashboardV2?.deck?.destination_catalog_revision ?? '—'}</dd></div>
                </dl>
                <p className={`mt-3 text-sm ${deckDisabledReason ? 'text-amber-200' : 'text-emerald-300'}`}>
                    {deckDisabledReason ?? 'Robot action enabled for the selected finite destination.'}
                </p>
                <button
                    type="button"
                    disabled={deckDisabledReason !== null || invokeDeckAction.isPending}
                    title={deckDisabledReason ?? 'Submit one durable robot command'}
                    onClick={invokeDeckMove}
                    className="mt-3 rounded bg-teal-700 px-4 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-35"
                >Move to destination</button>
                {deckReceiptUnavailable && (
                    <p role="status" className="mt-3 rounded border border-amber-700 bg-amber-950/30 p-2 text-sm text-amber-100">
                        receipt unavailable / outcome uncertain. Do not resubmit. Reconcile by command ID until a terminal receipt is available.
                    </p>
                )}
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Command ID</dt><dd className="font-mono">{deckCommandId ?? '—'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Lifecycle</dt><dd className="font-mono">{deckReceipt?.status ?? (deckReceiptUnavailable ? 'unavailable' : '—')}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Pending</dt><dd>{deckPending ? 'pending' : deckReceipt ? 'not pending' : 'unknown'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Receipt availability</dt><dd>{deckReceiptUnavailable ? 'unavailable' : deckReceipt ? 'available' : 'not requested'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Ambiguous outcome</dt><dd>{deckAmbiguous ? 'ambiguous' : deckReceipt ? 'not ambiguous' : 'unknown'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Recovery required</dt><dd>{deckRecoveryRequired ? 'required' : deckReceipt ? 'not required' : 'unknown'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Robot-selected source branch</dt><dd>{deckReceipt?.deck_movement?.source_branch ?? 'unknown'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Controller completion</dt><dd>{truthLabel(deckReceipt?.deck_movement?.controller_completion_verified, 'verified', 'not verified')}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Semantic state commit</dt><dd>{truthLabel(deckReceipt?.deck_movement?.semantic_state_committed, 'committed', 'not committed')}</dd></div>
                    <div className="rounded border border-amber-800/60 bg-amber-950/20 p-2"><dt className="text-slate-400">Physical observation</dt><dd>{truthLabel(deckReceipt?.deck_movement?.physical_observation_verified, 'observed', 'not observed')}</dd></div>
                    <div className="rounded border border-amber-800/60 bg-amber-950/20 p-2"><dt className="text-slate-400">Physical effect receipt</dt><dd>{deckReceipt ? (deckReceipt.physical_effect_verified ? 'verified' : 'not verified') : 'unknown'}</dd></div>
                </dl>
                <YOperatorError label="Deck enqueue" error={currentDeckInvokeError} />
                <YOperatorError label="Deck receipt" error={deckReceiptQuery.error} />
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <h2 className="text-lg font-semibold">Exact OEM Manual Controls</h2>
                <p className="mt-1 text-sm text-slate-400">Relative moves use the literal OEM <code>moveSteps(axis, steps)</code> route. Home/Open/Close use the axis-specific OEM mechanisms.</p>
                {operatorCatalog.isError && (
                    <p className="mt-1 break-words text-sm text-red-300">Robot manual-control catalog unavailable: {bioXpErrorText(operatorCatalog.error)}</p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/machine_config')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/machine_config', {})}
                        className={actionClass}
                    >Show OEM axis/config tables</button>
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/position_table')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/position_table', {})}
                        className={actionClass}
                    >Show OEM position table</button>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    <article data-testid="serial206-y-authority-panel" style={{ order: 2 }} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <h3 className="font-semibold">Y Axis</h3>
                            </div>
                            <button type="button" disabled={yStopDisabled} title="Addressed Y STOP remains independent of normal command submission and treats observed generations as evidence only." onClick={interruptY} className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35">Stop</button>
                        </div>
                        <div className="mt-3 grid gap-2">
                            <label className="block text-xs text-slate-300">Relative move steps<input type="number" min={0} max={BIOXP_Y_RELATIVE_MAX_STEPS} value={yStepInput} onChange={(event) => setYStepInput(Number(event.target.value))} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" /></label>
                            <div className="flex flex-wrap gap-1" aria-label="Y step presets">
                                {[1000, 5000, 10000, 25000].map((steps) => (
                                    <button key={steps} type="button" onClick={() => setYStepInput(steps)} className={`rounded px-2 py-1 text-xs ${yStepInput === steps ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>{steps.toLocaleString()}</button>
                                ))}
                            </div>
                            <label className="block text-xs text-slate-300">OEM absolute target (steps)<input type="number" min={BIOXP_Y_ABSOLUTE_MIN_STEPS} max={BIOXP_Y_ABSOLUTE_MAX_STEPS} value={yTargetInput} onChange={(event) => setYTargetInput(Number(event.target.value))} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" /></label>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" disabled={yMutationDisabled('oem.y.move_steps')} title={yActionDisabledReason('oem.y.move_steps', 'Y relative move unavailable.')} onClick={() => invokeYMoveSteps(-Math.abs(yStepInput))} className={actionClass}>Move −</button>
                            <button type="button" disabled={yMutationDisabled('oem.y.manual_panel_home')} title={yActionDisabledReason('oem.y.manual_panel_home', 'Y manual-panel home unavailable.')} onClick={() => invokeYHome('oem.y.manual_panel_home')} className={actionClass}>Home</button>
                            <button type="button" disabled={yMutationDisabled('oem.y.move_steps')} title={yActionDisabledReason('oem.y.move_steps', 'Y relative move unavailable.')} onClick={() => invokeYMoveSteps(Math.abs(yStepInput))} className={actionClass}>Move +</button>
                            <button type="button" disabled={yMutationDisabled('oem.y.move_absolute')} title={yActionDisabledReason('oem.y.move_absolute', 'Y absolute move unavailable.')} onClick={() => invokeYMoveAbsolute(yTargetInput)} className={actionClass}>Go absolute</button>
                        </div>
                        <details className="mt-3 rounded border border-slate-800 bg-slate-950/40 p-2 text-xs">
                            <summary className="cursor-pointer font-semibold text-slate-200">Axis status and evidence</summary>
                            <p className="mt-2 text-slate-300">Robot-owned Serial-206 Y authority. Controller completion and physical observation stay separate.</p>
                            <div className="mt-2 text-slate-400">Board epoch: <span className="font-mono text-slate-100">{yAxisV2?.active_board_epoch ?? '—'}</span> · Lifecycle: <span className="font-mono text-slate-100">{yAxisV2?.lifecycle_state ?? '—'}</span></div>
                        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Position</dt><dd className="font-mono">{yAxisV2?.position_steps ?? '—'}</dd><dd className={yAxisV2?.position_reply_valid ? 'text-emerald-300' : 'text-amber-200'}>{yAxisV2 ? `${yAxisV2.position_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.position_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Reference</dt><dd className="font-mono">{yAxisV2?.reference_state ?? '—'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Speed</dt><dd className="font-mono">{yAxisV2?.speed_steps_s ?? '—'}</dd><dd className={yAxisV2?.speed_reply_valid ? 'text-emerald-300' : 'text-amber-200'}>{yAxisV2 ? `${yAxisV2.speed_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.speed_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Home switch</dt><dd className="font-mono">{yAxisV2?.left_switch_raw ?? '—'}</dd><dd className={yAxisV2?.left_switch_reply_valid ? 'text-emerald-300' : 'text-amber-200'}>{yAxisV2 ? `${yAxisV2.left_switch_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.left_switch_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Profile</dt><dd className={yAxisV2?.profile_readback_valid ? 'text-emerald-300' : 'text-amber-200'}>{yAxisV2 ? yAxisV2.profile_readback_valid ? 'Valid' : `Invalid${yAxisV2.profile_mismatches.length > 0 ? ` · ${yAxisV2.profile_mismatches.join('; ')}` : ''}` : '—'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Updated</dt><dd className="font-mono">{yAxisV2 ? new Date(yAxisV2.updated_at * 1000).toISOString() : '—'}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Physical proof</dt><dd className="font-mono text-amber-200">{yAxisV2?.physical_position_verified ? 'observed' : 'not observed'}</dd></div>
                        </dl>
                        </details>
                        <YOperatorError label="Y enqueue" error={currentYInvokeError} />
                        <YOperatorError label="Y STOP" error={interruptYStop.error} />
                        {yPendingActionId && !yCommandId && <p role="status" className="mt-2 text-xs text-cyan-200">Submitting <span className="font-mono">{yPendingActionId}</span>; awaiting durable robot command ID.</p>}
                        {yReceiptCommandId && <p className="mt-2 text-xs text-slate-300">Command <span className="font-mono">{yReceiptCommandId}</span>: <span className="font-mono">{yReceiptQuery.data?.status ?? 'queued'}</span>{yReceiptQuery.data?.completion_class === 'issued_pending' ? ' · awaiting robot completion' : ''}</p>}
                        {yReceiptQuery.data && (
                            <div className="mt-3 grid gap-2 text-xs lg:grid-cols-2">
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Requested</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.requested_values, null, 2)}</pre></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Effective</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.effective_values, null, 2)}</pre></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Observed, terminal position/speed, discrepancy</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.observed_values, null, 2)}</pre></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Completion</strong><p className="mt-1">class={yReceiptQuery.data.completion_class ?? 'not reported'} · terminal position={String(yReceiptQuery.data.observed_values.terminal_position_steps ?? 'not reported')} · terminal speed={String(yReceiptQuery.data.observed_values.terminal_speed_steps_s ?? 'not reported')} · discrepancy={String(yReceiptQuery.data.observed_values.discrepancy_steps ?? 'not reported')}</p></div>
                                <div className="rounded border border-amber-800/60 bg-amber-950/20 p-2"><strong>Independent physical observation</strong><p className="mt-1">physical_effect_verified={String(yReceiptQuery.data.physical_effect_verified)}</p></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Controller completion evidence</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.controller_evidence, null, 2)}</pre></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2"><strong>Raw return layers</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.raw_return_layers, null, 2)}</pre></div>
                                <div className="rounded border border-slate-800 bg-slate-950/60 p-2 lg:col-span-2"><strong>Transport artifacts</strong><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(yReceiptQuery.data.transport_artifacts, null, 2)}</pre></div>
                            </div>
                        )}
                        {interruptYStop.data && <details className="mt-2 text-xs"><summary>Latest independent Y STOP receipt</summary><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(interruptYStop.data, null, 2)}</pre></details>}
                        {yReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-red-300">Y receipt unavailable: {bioXpErrorText(yReceiptQuery.error)}</p>}
                    </article>
                    <article data-testid="serial206-xy-oem-panel" style={{ order: 1 }} className="rounded-lg border border-cyan-700/60 bg-cyan-950/20 p-3 lg:col-span-2">
                        <h3 className="font-semibold">Combined XY Capability</h3>
                        <p className="mt-1 text-xs text-slate-300">Submits one backend OEM <code>moveXY</code> transaction so X and Y execute the robot-owned combined move. Use this for named XY destinations such as tip waste rather than issuing two independent axis commands.</p>
                        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">X target</dt><dd className="font-mono">{absoluteTargets.x}</dd></div>
                            <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Y target</dt><dd className="font-mono">{yTargetInput}</dd></div>
                        </dl>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" disabled={xyMoveDisabled} onClick={invokeXYMove} className={actionClass}>Move X + Y together</button>
                            <button type="button" disabled={xyHomeDisabled} onClick={invokeXYHome} className={actionClass}>Home X + Y</button>
                        </div>
                        <YOperatorError label="XY command" error={invokeXYAction.error} />
                        {xyMoveDisabledReason && <p className="mt-1 text-xs text-amber-200">XY move: {xyMoveDisabledReason}</p>}
                        {xyHomeDisabledReason && <p className="mt-1 text-xs text-amber-200">XY home: {xyHomeDisabledReason}</p>}
                        {xyPending && <p role="status" className="mt-2 text-sm text-amber-200">XY command pending · {xyReceipt?.status ?? 'submitting'} · Do not retry.</p>}
                        {xyReceipt && !xyPending && <p role="status" className="mt-2 text-sm">XY command {xyReceipt.status}{xyReceipt.status === 'ambiguous' ? '; outcome unknown; do not resubmit' : ''}</p>}
                        {currentXYSubmission && xyReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-amber-200">XY command status unavailable: {bioXpErrorText(xyReceiptQuery.error)}. Do not retry until the outcome is reconciled.</p>}
                        {xyReceipt && <details className="mt-2 text-xs"><summary>Latest XY command receipt</summary><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(xyReceipt, null, 2)}</pre></details>}
                    </article>
                    {AXES.map(({ axis, label, controls }) => (
                        <article key={axis} style={{ order: axis === 'x' ? 3 : axis === 'z' ? 4 : axis === 'g' ? 5 : 6 }} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="font-semibold">{label}</h3>
                                <div className="flex gap-2">
                                    <button
                                        type="button"
                                        disabled={!linkConnected || ((axis === 'x' || axis === 'z')
                                            ? generation <= 0 || (axis === 'x' ? interruptPending('oem.x.stop') : interruptPending('oem.z.stop'))
                                            : operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true || invokeOperatorAction.isPending)}
                                        title={axis === 'x'
                                            ? actionUnavailableReason('oem.x.stop', 'Immediate OEM X motor stop')
                                            : axis === 'z'
                                                ? actionUnavailableReason('oem.z.stop', 'Immediate OEM Z motor stop')
                                                : 'Immediate OEM motor stop for this component'}
                                        onClick={() => stopAxis(axis)}
                                        className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35"
                                    >Stop</button>
                                    {axis === 'x' && (
                                        <button
                                            type="button"
                                            disabled={!linkConnected || generation <= 0 || interruptPending('oem.abort_all')}
                                            title={v2InterruptActionById('oem.abort_all')?.disabled_reason ?? 'Robot-owned aggregate component stops plus abort latch; physical effect unverified'}
                                            onClick={abortXAggregate}
                                            className="rounded bg-red-950 px-3 py-1.5 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35"
                                        >Aggregate Abort (all OEM boards)</button>
                                    )}

                                </div>
                            </div>
                            {axis !== 'door' && (
                                <div className="mt-3 grid gap-2">
                                    <label className="block text-xs text-slate-300">
                                        Relative move steps
                                        <input
                                            type="number"
                                            min={1}
                                            max={axis === 'x' ? xRelativeMaximum : axis === 'z' ? zRelativeMaximum : 160000}
                                            step={1}
                                            value={manualSteps[axis]}
                                            onChange={(event) => {
                                                const parsed = Number.parseInt(event.target.value || '1', 10);
                                                const boundedMaximum = axis === 'x' ? xRelativeMaximum : axis === 'z' ? zRelativeMaximum : 160000;
                                                const magnitude = Number.isFinite(parsed) ? Math.max(1, Math.abs(parsed)) : 1;
                                                const bounded = boundedMaximum === undefined ? magnitude : Math.min(boundedMaximum, magnitude);
                                                setManualSteps((current) => ({ ...current, [axis]: bounded }));
                                            }}
                                            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                        />
                                    </label>
                                    <div className="flex flex-wrap gap-1" aria-label={`${label} step presets`}>
                                        {[1000, 5000, 10000, 25000].map((steps) => (
                                            <button
                                                key={steps}
                                                type="button"
                                                onClick={() => setManualSteps((current) => ({ ...current, [axis]: steps }))}
                                                className={`rounded px-2 py-1 text-xs ${manualSteps[axis] === steps ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                                            >{steps.toLocaleString()}</button>
                                        ))}
                                    </div>
                                    <label className="block text-xs text-slate-300">
                                        OEM absolute target (steps)
                                        <div className="mt-1 flex gap-2">
                                            <input
                                                type="number"
                                                min={axis === 'x' ? xAbsoluteMinimum : axis === 'z' ? zAbsoluteMinimum : undefined}
                                                max={axis === 'x' ? xAbsoluteMaximum : axis === 'z' ? zAbsoluteMaximum : undefined}
                                                step={1}
                                                value={absoluteTargets[axis]}
                                                onChange={(event) => {
                                                    const parsed = Number.parseInt(event.target.value || '0', 10);
                                                    setAbsoluteTargets((current) => ({
                                                        ...current,
                                                        [axis]: Number.isFinite(parsed) ? parsed : 0,
                                                    }));
                                                }}
                                                className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                            />
                                            <button
                                                type="button"
                                                disabled={!linkConnected || (axis === 'x' ? !xAbsoluteEnabled : axis === 'z' ? !zAbsoluteEnabled : operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true)}
                                                title={axis === 'x' ? xAbsoluteDisabledReason ?? 'Robot-owned exact OEM X absolute move' : axis === 'z' ? zAbsoluteDisabledReason ?? 'Robot-owned exact OEM absolute move' : undefined}
                                                onClick={() => runAbsolute(axis)}
                                                className={actionClass}
                                            >Go absolute</button>
                                        </div>
                                        </label>
                                        {axis === 'x' && (
                                        <details className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs text-sky-100">
                                            <summary className="cursor-pointer font-semibold text-slate-200">Axis status and evidence</summary>
                                            <h4 className="mt-2 font-semibold text-sky-50">X OEM authority</h4>
                                            <p className="mt-1"><strong>Position:</strong> {xPosition} · <strong>Software reference state (not physical proof):</strong> {xReference}</p>
                                            <p className="mt-1"><strong>Lifecycle:</strong> {xLifecycle} · <strong>Authority:</strong> {xAuthority}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {xLeftSwitchState} / {xRightSwitchState} · <strong>GAP13/12 disabled:</strong> {String(xLeftSwitchDisabled)} / {String(xRightSwitchDisabled)}</p>
                                            <p className="mt-1"><strong>Configured GAP4/5/6/205:</strong> {xMaxSpeed} / {xMaxAcceleration} / {xMaxCurrent} / {xStallGuard}</p>
                                            <p className="mt-1"><strong>Catalog absolute bounds:</strong> {xAbsoluteMinimum ?? 'unbounded'}..{xAbsoluteMaximum ?? 'unbounded'} · <strong>Catalog relative magnitude:</strong> {xRelativeMaximum ?? 'unbounded'}</p>
                                            <p className="mt-1"><strong>Connection generation:</strong> {xGeneration} · <strong>Board lifecycle generation:</strong> {xBoardGeneration} · <strong>Fresh:</strong> {xBoardGenerationFresh === true ? 'yes' : xBoardGenerationFresh === false ? 'no' : 'unknown'}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(xSwitchMaskTuple?.['12'] ?? 'unknown')} / {String(xSwitchMaskTuple?.['13'] ?? 'unknown')}. Recovered OEM X initialization writes neither register. Profile {xProfileVerified === true ? 'verified' : xProfileVerified === false ? 'not verified' : 'unknown'}.</p>
                                            <p className="mt-1 text-sky-200/80">Controller/software reference is reported exactly as published by the robot provider; it is not independent evidence of the physical X location.</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button type="button" className="rounded bg-red-800 px-3 py-2 text-sm font-semibold hover:bg-red-700 disabled:opacity-35" disabled={!linkConnected || generation <= 0 || interruptPending('oem.x.stop')} title="Immediate OEM X stop" onClick={() => stopAxis('x')}>Stop X</button>
                                                <button type="button" className="rounded bg-red-950 px-3 py-2 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35" disabled={!linkConnected || generation <= 0 || interruptPending('oem.abort_all')} title="Aggregate OEM abort" onClick={abortXAggregate}>Aggregate Abort (all OEM boards)</button>
                                            </div>
                                            {xLastFailure != null && <details className="mt-2"><summary className="cursor-pointer text-red-200">Last X failure</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(xLastFailure, null, 2)}</pre></details>}
                                            {xReceipt != null && <details className="mt-2"><summary className="cursor-pointer">Latest X authority receipt</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-sky-200/80">{JSON.stringify(xReceipt, null, 2)}</pre></details>}
                                        </details>
                                        )}
                                        {axis === 'z' && (
                                        <details className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs text-cyan-100">
                                            <summary className="cursor-pointer font-semibold text-slate-200">Axis status and evidence</summary>
                                            <p className="mt-2"><strong>Dynamic OEM pseudo-home floor:</strong> OEM moveZ applies the robot-owned PSUDO_Z_HOME as a dynamic minimum target. A request below the current value is replaced with that value before dispatch. Z does not automatically return to pseudo-home after every movement.</p>
                                            <p className="mt-1"><strong>Clear and Home:</strong> Z Clear returns to the selected pseudo-home. Manual Home follows the OEM homing sequence and establishes controller coordinate 0.</p>
                                            <p className="mt-1"><strong>Position:</strong> {dashboard?.z_axis.status?.position_steps ?? 'unknown'} · <strong>Reference:</strong> {dashboard?.z_axis.status?.reference ?? 'unknown'} · <strong>Authority state:</strong> {dashboard?.z_axis.provider.state ?? 'unknown'}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {dashboard?.z_axis.status?.left_switch_state ?? 'unknown'} / {dashboard?.z_axis.status?.right_switch_state ?? 'unknown'} · <strong>GAP13/12 disabled:</strong> {String(dashboard?.z_axis.status?.left_switch_disabled ?? 'unknown')} / {String(dashboard?.z_axis.status?.right_switch_disabled ?? 'unknown')}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(dashboard?.z_axis.provider.switch_mask_tuple?.['12'] ?? 'unknown')} / {String(dashboard?.z_axis.provider.switch_mask_tuple?.['13'] ?? 'unknown')}. Recovered OEM Z initialization writes neither register.</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button
                                                    type="button"
                                                    className={actionClass}
                                                    disabled={!linkConnected || v2NormalActionById('oem.z.clear')?.enabled !== true}
                                                    title={v2NormalActionById('oem.z.clear')?.disabled_reason ?? 'Move to the robot-owned clear position selected from tip and gantry state'}
                                                    onClick={() => {
                                                        const envelope = v2NormalEnvelope();
                                                        if (envelope) submitV2({ ...envelope, action_id: 'oem.z.clear', inputs: {} });
                                                    }}
                                                >Z Clear (automatic OEM position)</button>
                                            </div>
                                            {dashboard?.z_axis.last_failure != null && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(dashboard.z_axis.last_failure, null, 2)}</pre>}
                                        </details>
                                    )}
                                </div>
                            )}
                            {Object.entries(dashboard?.successive_move_queue ?? {})
                                .filter(([, queue]) => queue.state !== 'idle' || queue.depth > 0)
                                .length > 0 && (
                                <div className="mt-2 rounded border border-amber-800/60 bg-amber-950/20 p-2 text-xs text-amber-100" data-testid="successive-move-queue">
                                    <p className="font-semibold">Robot successive-move queue</p>
                                    {Object.entries(dashboard?.successive_move_queue ?? {})
                                        .filter(([, queue]) => queue.state !== 'idle' || queue.depth > 0)
                                        .map(([queueAxis, queue]) => (
                                            <p key={queueAxis} className="mt-1">
                                                <strong>{queueAxis.toUpperCase()}:</strong> {queue.state} · {queue.depth} queued{queue.head_action_id ? ` · head ${queue.head_action_id}` : ''}{queue.active_command_id ? ` · active ${queue.active_command_id}` : ''}
                                            </p>
                                        ))}
                                </div>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                {controls.map(({ label: controlLabel, operation }) => {
                                    const path = operatorPathForControl(axis, operation);
                                    const xActionId = axis === 'x'
                                        ? operation === 'home' || operation === 'commission-home'
                                            ? 'oem.x.manual_panel_home'
                                            : operation === 'move-negative' || operation === 'move-positive'
                                                ? 'oem.x.move_steps'
                                                : null
                                        : null;
                                    const zActionId = axis === 'z'
                                        ? operation === 'home' || operation === 'commission-home'
                                            ? 'oem.z.manual_home'
                                            : operation === 'move-negative' || operation === 'move-positive'
                                                ? 'oem.z.move_steps'
                                                : null
                                        : null;
                                    const legacyAction = xActionId
                                        ? operatorActionById(xActionId)
                                        : !zActionId && path
                                            ? operatorActionForPath(path)
                                            : null;
                                    const zAction = zActionId ? v2NormalActionById(zActionId) : null;
                                    const action = zActionId ? zAction : legacyAction;
                                    const isXNegative = xActionId === 'oem.x.move_steps' && operation === 'move-negative';
                                    const isXPositive = xActionId === 'oem.x.move_steps' && operation === 'move-positive';
                                    const isXHome = xActionId === 'oem.x.manual_panel_home';
                                    const isZNegative = zActionId === 'oem.z.move_steps' && operation === 'move-negative';
                                    const isZPositive = zActionId === 'oem.z.move_steps' && operation === 'move-positive';
                                    const isZHome = zActionId === 'oem.z.manual_home';
                                    const admissionEnabled = isXNegative
                                        ? xNegativeEnabled
                                        : isXPositive
                                            ? xPositiveEnabled
                                            : isXHome
                                                ? xHomeEnabled
                                                : isZNegative
                                                    ? zNegativeEnabled
                                                    : isZPositive
                                                        ? zPositiveEnabled
                                                        : isZHome
                                                            ? zHomeEnabled
                                                            : action?.enabled === true;
                                    const enabled = admissionEnabled;
                                    const unavailableReason = isXNegative
                                        ? xNegativeDisabledReason ?? 'Robot verifies this exact X move at dispatch.'
                                        : isXPositive
                                            ? xPositiveDisabledReason ?? 'Robot verifies this exact X move at dispatch.'
                                            : isXHome
                                                ? xHomeDisabledReason ?? 'Robot verifies this exact X Home at dispatch.'
                                                : isZNegative
                                                    ? zNegativeDisabledReason ?? 'Robot verifies this exact Z move at dispatch.'
                                                    : isZPositive
                                                        ? zPositiveDisabledReason ?? 'Robot verifies this exact Z move at dispatch.'
                                                        : isZHome
                                                            ? zHomeDisabledReason ?? 'Robot verifies this exact Z Home at dispatch.'
                                                            : action?.disabled_reason ?? 'Robot action unavailable.';
                                    return (
                                        <button
                                            key={operation}
                                            type="button"
                                            disabled={!linkConnected || operatorCatalog.isLoading || !enabled}
                                            title={enabled ? 'Robot-owned exact OEM action' : unavailableReason}
                                            onClick={() => runControl(axis, operation)}
                                            className={actionClass}
                                        >{controlLabel}</button>
                                    );
                                })}
                            </div>
                            {axis === 'z' && zHomeFailureDetail && (
                                <div role="alert" className="mt-3 rounded border border-red-700/70 bg-red-950/30 p-3 text-sm text-red-100">
                                    <p className="font-semibold">{zHomeFailureDetail.failure}</p>
                                    <p>Provider failure: <span className="font-mono">{zHomeFailureDetail.provider_failure}</span></p>
                                    <p>{`Axis ${zHomeFailureDetail.axis} · Board ${zHomeFailureDetail.board} · Motor ${zHomeFailureDetail.motor} · Source return ${zHomeFailureDetail.source_return_code}`}</p>
                                    <p>{`Controller acknowledged: ${zHomeFailureDetail.controller_acknowledged ? 'yes' : 'no'}`}</p>
                                    <p>{`Terminal state verified: ${zHomeFailureDetail.controller_terminal_state_verified ? 'yes' : 'no'}`}</p>
                                    <p>{`Physical effect verified: ${zHomeFailureDetail.physical_effect_verified ? 'yes' : 'no'}`}</p>
                                    <p>{`Lifecycle: ${zHomeFailureDetail.lifecycle_state} · Reference: ${zHomeFailureDetail.reference_state}`}</p>
                                </div>
                            )}
                        </article>
                    ))}
                </div>
                <details className="mt-4 rounded border border-slate-800 bg-slate-950/60 p-3" open={pipettesOpen} onToggle={(event) => setPipettesOpen(event.currentTarget.open)}>
                    <summary className="cursor-pointer text-sm font-semibold">Pipette controls</summary>
                    {pipettesOpen && <BioXpPipetteControlPanel
                        generation={generation}
                        connected={robotControlReady && operatorCatalog.data !== undefined}
                        pipettes={operatorCatalog.data?.dashboard.pipettes}
                        freshness={operatorCatalog.data?.dashboard.snapshot.freshness}
                        actions={catalog?.actions}
                        catalogLoading={operatorCatalog.isLoading}
                        invokePending={invokeOperatorAction.isPending}
                        invokeAction={(actionId, inputs) => invokeAction(actionId, inputs)}
                    />}
                </details>
                {invokeOperatorAction.error && (
                    <p role="alert" className="mt-3 whitespace-pre-wrap break-words text-sm text-red-300">{bioXpErrorText(invokeOperatorAction.error)}</p>
                )}
                {(invokeOperatorAction.isPending || invokeLifecycleActionMutation.isPending || invokeYAction.isPending || invokeDeckAction.isPending || interruptAnyPending) && (
                    <p role="status" className="mt-3 rounded border border-cyan-800 bg-cyan-950/30 p-2 text-sm text-cyan-100">Command accepted by BMS; waiting for the robot-owned terminal receipt. Stop and Abort remain available.</p>
                )}
                {linkConnected && catalog && !historyQuery.isError && latestOperatorReceipt && (
                    <details className="mt-3 rounded border border-slate-800 bg-slate-900/60 p-3" open>
                        <summary className="cursor-pointer text-sm font-semibold">Latest exact-OEM action receipt</summary>
                        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs text-slate-300">{JSON.stringify(latestOperatorReceipt, null, 2)}</pre>
                    </details>
                )}
            </section>

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Advanced Full Command Catalog</summary>
                {advancedOpen && <><p className="mt-1 text-sm text-slate-400">All primitive, service, recovery, and diagnostic routes. Kept collapsed so handler state and exact manual controls remain primary.</p><div className="mt-4"><BioXpOperatorControlTabs generation={generation} connected={robotControlReady} /></div></>}
            </details>

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" open={cameraOpen} onToggle={(event) => setCameraOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Camera</summary>
                {cameraOpen && <div className="mt-4"><BioXpCameraPanel connected={linkConnected} connectionGeneration={linkConnected ? generation : null} mutationEnabled={status?.mutation_access?.enabled === true} /></div>}
            </details>

            <section className="rounded-xl border border-red-800/70 bg-red-950/30 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-red-200">Aggregate Emergency Stop</h2>
                        <p className="max-w-3xl text-sm text-red-200/70">
                            Requests robot-owned stops across present motion components and the abort latch. Source completion, controller ACK, and terminal readback are separate evidence; physical stopping remains unverified.
                        </p>
                    </div>
                    <button
                        type="button"
                        disabled={!linkConnected || generation <= 0 || interruptAggregateAbort.isPending}
                        title="Independent robot-owned aggregate stop; physical effect requires observation"
                        onClick={abortXAggregate}
                        className="rounded bg-red-700 px-5 py-3 font-bold disabled:cursor-not-allowed disabled:opacity-35"
                    >Emergency Stop</button>
                </div>
                <InterruptOutcome label="Aggregate emergency stop" receipt={interruptAggregateAbort.data} error={interruptAggregateAbort.error} pending={interruptAggregateAbort.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="X STOP" receipt={interruptXStop.data} error={interruptXStop.error} pending={interruptXStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Y STOP" receipt={interruptYStop.data} error={interruptYStop.error} pending={interruptYStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Z STOP" receipt={interruptZStop.data} error={interruptZStop.error} pending={interruptZStop.isPending} generation={generation} connected={linkConnected} />
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">Recent Robot Actions</h2>
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                        Entries
                        <select
                            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
                            value={historyLimit}
                            onChange={(event) => setHistoryLimit(Number(event.target.value) as 8 | 25 | 50 | 100)}
                            aria-label="Recent robot actions depth"
                        >
                            <option value={8}>8</option>
                            <option value={25}>25</option>
                            <option value={50}>50</option>
                            <option value={100}>100</option>
                        </select>
                    </label>
                </div>
                {recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">No robot action receipts recorded.</p>
                ) : (
                    <div className="mt-3 space-y-2">
                        {recentCommands.map((record) => (
                            <article key={record.command_id} className="rounded border border-slate-800 bg-slate-900/60 p-3 text-sm">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <strong className="font-mono text-slate-100">{record.action_id}</strong>
                                    <span className={record.status === 'outcome_unknown' ? 'text-amber-300' : record.status === 'failed' || record.status === 'blocked' ? 'text-red-300' : 'text-slate-300'}>
                                        {record.status.replaceAll('_', ' ')}{record.status === 'outcome_unknown' ? '; do not resubmit; reconciliation required' : ''} · {bioXpReceiptTimestampText(record.finished_at)}
                                    </span>
                                </div>
                                <p className="mt-1 whitespace-pre-wrap break-words text-slate-200">{'error' in record ? (record.error ?? record.machine_assessment) : 'unverified legacy reconciliation record'}</p>
                                <p className="mt-1 text-xs text-slate-400">
                                    {'remote_acknowledged' in record && record.remote_acknowledged ? 'Robot HTTP acknowledged' : 'Robot HTTP unverified'} · {'controller_acknowledged' in record && record.controller_acknowledged ? 'Controller ACK' : 'Controller ACK unverified'} · {'controller_terminal_state_verified' in record && record.controller_terminal_state_verified ? 'Terminal proof verified' : 'Terminal proof unverified'} · {'physical_effect_verified' in record && record.physical_effect_verified ? 'Physical effect verified' : 'Physical effect unverified'}
                                </p>
                                {(record.response != null || record.stage_receipts.length > 0) && <details className="mt-2"><summary className="cursor-pointer text-xs text-slate-400">Nested robot evidence</summary><pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify({ response: record.response, stage_receipts: record.stage_receipts }, null, 2)}</pre></details>}
                            </article>
                        ))}
                    </div>
                )}
                {historyQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">Robot action history unavailable: {bioXpErrorText(historyQuery.error)}</p>}
            </section>

            {error && <p role="alert" className="rounded border border-red-800 p-3 text-sm text-red-300">{bioXpErrorText(error)}</p>}
            {statusQuery.isError && <p role="alert" className="text-sm text-red-300">BioXP status unavailable.</p>}
        </div>
    );
}
