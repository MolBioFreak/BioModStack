import { useEffect, useMemo, useRef, useState } from 'react';

import {
    bioXpDeckRecoveryResolution,
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
    type BioXpDeckDestinationV1,
    type BioXpDeckSubmission,

    type BioXpOperatorDashboardXAxis,

    type BioXpOperatorInputSpec,

    type BioXpOperatorReceiptV2,
} from '../lib/bioxpClient';

import { bioXpDeckReadinessText, bioXpReceiptFailureText, bioXpReceiptStatusText } from '../lib/bioxpEvidencePresentation';
import { BioXpCameraPanel } from './BioXpCameraPanel';
import { BioXpHistoryReceiptCard, BioXpHistoryPager, useBioXpHistoryPagination } from './BioXpHistoryReceiptCard';
import { BioXpOperatorControlTabs } from './BioXpOperatorControlTabs';
import { BioXpPipetteControlPanel } from './BioXpPipetteControlPanel';
import { BioXpQuickDashboard } from './BioXpQuickDashboard';
import { BioXpWorkflowControls } from './BioXpWorkflowControls';
import { BioXpTransferControls } from './BioXpTransferControls';
import { BioXpOperatorReports } from './BioXpOperatorReports';


function DeckSubmissionRow({ item, generation, active, onSelect, onTerminal }: {
    item: BioXpDeckSubmission; generation: number; active: boolean; onSelect: (id: string) => void;
    onTerminal: (key: string, receipt: BioXpOperatorReceiptV2) => void;
}) {
    const current = active && item.request.expected_connection_generation === generation;
    const commandId = item.receipt?.command_id ?? item.commandId;
    const query = useBioXpOperatorReceiptV2(commandId ?? null, item.request.expected_connection_generation, current);
    const receipt = query.data?.action_id === item.request.action_id ? query.data : item.receipt;
    useEffect(() => {
        if (current && !query.error && query.data?.terminal && query.data.action_id === item.request.action_id)
            onTerminal(item.request.idempotency_key, query.data);
    }, [current, query.data, query.error, item, onTerminal]);
    const label = query.data != null && query.data.action_id !== item.request.action_id ? 'receipt unavailable / outcome uncertain'
        : item.state === 'submitting' ? 'submitting / not yet accepted'
        : item.state === 'uncertain' ? 'admission uncertain / checking request; do not resubmit'
        : item.state === 'not_sent' ? 'not sent / connection changed'
        : item.state === 'rejected' ? 'not accepted' : `robot ${receipt?.status ?? 'accepted'}`;
    return <p data-request-key={item.request.idempotency_key}>
        {String(item.request.inputs.target)}{item.request.inputs.camera_offset === true ? ' + camera offset' : ''} · {label}
        {' · '}{item.request.idempotency_key}
        {commandId && <button type="button" disabled={!current} onClick={() => onSelect(commandId)}>
            {' · '}{commandId}
        </button>}
        {!current && ' · earlier connection'}
        {query.error != null && ' · receipt unavailable; checking again'}
        {item.error != null && ` · ${bioXpErrorText(item.error)}`}
        {bioXpReceiptFailureText(receipt)}
    </p>;
}

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


const actionClass = 'rounded bg-cyan-700 px-3 py-2 text-sm font-semibold hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-35';
let fallbackIdempotencySequence = 0;
const nextIdempotencyKey = (prefix: string): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    fallbackIdempotencySequence += 1;
    return `${prefix}-${fallbackIdempotencySequence}`;
};

const CANONICAL_DECK_ACTION_IDS = new Set([
    'oem.deck.move_to_location',
    'oem.deck._mov_execution',
    'oem.deck._finite_operation',
]);


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
                <summary>Bounded robot/BMS error preview (selected fields)</summary>
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
    const status = statusQuery.data;
    const connection = status?.connection;
    const active = connection?.active === true;
    const displayConnected = active && connection?.reachable !== false;
    const linkConnected = !statusQuery.isError && displayConnected;
    const robotControlReady = linkConnected
        && connection?.runtime_ready === true;
    const configured = connection?.configured === true;
    const generation = connection?.generation ?? 0;
    const currentGenerationRef = useRef(generation);
    // React mutation state updates on the next render. Reserve submission now,
    // so multiple clicks in one event batch cannot become waiting HTTP calls.
    const normalSubmissionRef = useRef<object | null>(null);
    const reserveNormalSubmission = () => {
        if (normalSubmissionRef.current !== null) return null;
        const token = {};
        normalSubmissionRef.current = token;
        return () => {
            if (normalSubmissionRef.current !== token) return false;
            normalSubmissionRef.current = null;
            return true;
        };
    };
    useEffect(() => {
        currentGenerationRef.current = generation;
    }, [generation]);
    const [historyLimit, setHistoryLimit] = useState<8 | 25 | 50 | 100>(8);
    const [reportsOpen, setReportsOpen] = useState(false);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [cameraOpen, setCameraOpen] = useState(true);
    const [pipettesOpen, setPipettesOpen] = useState(false);
    const [absoluteTargets, setAbsoluteTargets] = useState<Record<'x' | 'z' | 'g', number>>({ x: 60, z: 65000, g: 0 });
    const catalogV2Query = useBioXpOperatorControlCatalogV2(generation, active);
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
    const currentCatalogV2 = linkConnected && !catalogV2Query.isError
        && localAgeMs < 15_000 && upstreamAgeMs < 15_000
        ? catalogV2Query.data : undefined;
    const currentDashboardV2 = currentCatalogV2?.dashboard;
    const currentTelemetry = currentDashboardV2?.telemetry ?? undefined;
    // Presentation retains the observation in this connection's query key.
    // Admission still requires currentCatalogV2 and its unchanged expiry.
    const displayDashboardV2 = displayConnected ? catalogV2Query.data?.dashboard : undefined;
    const displayTelemetry = displayDashboardV2?.telemetry ?? undefined;
    const showingLastKnown = displayTelemetry != null && currentTelemetry == null;
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
    const [transferBusy, setTransferBusy] = useState(false);
    const [deckCameraOffset, setDeckCameraOffset] = useState(false);
    const [deckSelectionCatalog, setDeckSelectionCatalog] = useState<{
        generation: number; options: BioXpDeckDestinationV1[];
    } | null>(null);
    const [yStepInput, setYStepInput] = useState(1000);
    const [yTargetInput, setYTargetInput] = useState(0);
    const v2AuthorityCoherent = currentCatalogV2 !== undefined;
    const yAxisV2 = v2AuthorityCoherent ? currentDashboardV2?.y_axis : undefined;
    const yReceiptCommandId = yCommandId
        ?? yAxisV2?.active_command?.command_id
        ?? yAxisV2?.latest_compact_receipt?.command_id
        ?? null;
    const yReceiptQuery = useBioXpOperatorReceiptV2(yReceiptCommandId, generation, active);
    const zHomeReceiptQuery = useBioXpOperatorReceiptV2(currentZHomeCommandId, generation, active);
    const lifecycleReceiptQuery = useBioXpOperatorReceiptV2(currentLifecycleCommandId, generation, linkConnected);
    const [reconciledDeckPredecessor, setReconciledDeckPredecessor] = useState<{ commandId: string; generation: number } | null>(null);
    const dashboardDeckReceipt = [
        ...(currentDashboardV2?.active_commands ?? []),
        ...(currentDashboardV2?.latest_receipts ?? []),
    ]
        .filter((receipt) => !(reconciledDeckPredecessor?.generation === generation
                && reconciledDeckPredecessor.commandId === receipt.command_id && receipt.terminal)
            && CANONICAL_DECK_ACTION_IDS.has(receipt.action_id)
            && (receipt.terminal === false
                || receipt.status === 'ambiguous'
                || receipt.completion_class === 'recovery_required'
                || receipt.error?.code === 'reconciliation_required'))
        .sort((left, right) => Number(right.status === 'ambiguous') - Number(left.status === 'ambiguous'))[0];
    const effectiveDeckCommandId = active
        ? (deckMutationGeneration === generation ? deckCommandId : null) ?? dashboardDeckReceipt?.command_id ?? null
        : null;
    const deckReceiptQuery = useBioXpOperatorReceiptV2(effectiveDeckCommandId, generation, active);
    const invokeLifecycleActionMutation = useInvokeBioXpOperatorActionV2();
    const invokeYAction = useInvokeBioXpOperatorActionV2();
    const [axisSubmission, setAxisSubmission] = useState<{ generation: number; commandId: string; actionId: string; receipt: BioXpOperatorReceiptV2 | null } | null>(null);
    const currentAxisSubmission = active && axisSubmission?.generation === generation ? axisSubmission : null;
    const axisReceiptQuery = useBioXpOperatorReceiptV2(currentAxisSubmission?.commandId ?? null, generation, active);
    const axisReceipt = currentAxisSubmission == null ? null
        : axisReceiptQuery.data?.command_id === currentAxisSubmission.commandId
            && axisReceiptQuery.data.action_id === currentAxisSubmission.actionId ? axisReceiptQuery.data : currentAxisSubmission.receipt;
    const axisOutcomeUnresolved = currentAxisSubmission != null
        && (axisReceipt == null || !axisReceipt.terminal || axisReceipt.status === 'ambiguous');
    const axisAmbiguousError = isDispatchedOutcomeAmbiguous(invokeYAction.error)
        && !(axisReceipt?.terminal && axisReceipt.status !== 'ambiguous');
    const invokeDeckAction = useInvokeBioXpDeckActionV2(generation, active);
    const interruptXStop = useInterruptBioXpOperatorActionV1();
    const interruptYStop = useInterruptBioXpOperatorActionV1();
    const interruptZStop = useInterruptBioXpOperatorActionV1();
    const interruptAggregateAbort = useInterruptBioXpOperatorActionV1();
    const invokeXYAction = useInvokeBioXpOperatorActionV2();
    const [xySubmission, setXYSubmission] = useState<{ generation: number; commandId: string; receipt: BioXpOperatorReceiptV2 | null } | null>(null);
    const currentXYSubmission = active && xySubmission?.generation === generation ? xySubmission : null;
    const xyReceiptQuery = useBioXpOperatorReceiptV2(currentXYSubmission?.commandId ?? null, generation, active);
    const xyReceipt = currentXYSubmission == null ? null
        : xyReceiptQuery.data?.command_id === currentXYSubmission.commandId ? xyReceiptQuery.data : currentXYSubmission.receipt;
    const xyOutcomeUnresolved = currentXYSubmission != null && (xyReceipt == null || !xyReceipt.terminal || xyReceipt.status === 'ambiguous');
    const currentXYInvokeError = isDispatchedOutcomeAmbiguous(invokeXYAction.error)
        && xyReceipt?.terminal && xyReceipt.status !== 'ambiguous' ? null : invokeXYAction.error;
    const xyPending = invokeXYAction.isPending || xyOutcomeUnresolved || isDispatchedOutcomeAmbiguous(currentXYInvokeError);
    const acceptXYSubmission = (receipt: BioXpOperatorReceiptV2) => {
        if (currentGenerationRef.current !== generation) return;
        setXYSubmission({ generation, commandId: receipt.command_id, receipt });
    };
    const retainXYUncertainty = (error: unknown) => {
        if (currentGenerationRef.current !== generation) return;
        const identity = bioXpPostDispatchCommandIdentity(error);
        if (identity) setXYSubmission({ generation, commandId: identity.commandId, receipt: null });
    };
    const historyPagination = useBioXpHistoryPagination(linkConnected ? generation : 0, historyLimit);
    const historyQuery = useBioXpOperatorActionHistory(generation, linkConnected, historyLimit, historyPagination.cursor);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const operatorCatalog = useBioXpOperatorControlCatalog(
        generation,
        linkConnected,
        null,
        Number.isInteger(absoluteTargets.z) ? absoluteTargets.z : undefined,
    );
    const invokeOperatorAction = useInvokeBioXpOperatorAction();
    const componentStop = useInvokeBioXpOperatorAction('stop');

    const resetInvokeOperatorAction = invokeOperatorAction.reset;
    const resetComponentStop = componentStop.reset;
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

    const catalog = !linkConnected || operatorCatalog.isError ? undefined : operatorCatalog.data;
    const dashboard = displayTelemetry;
    const zTargetProvider = catalog?.dashboard.ownership_generation === currentDashboardV2?.ownership_generation
        && currentCatalogV2 != null ? catalog?.dashboard.z_axis?.provider : undefined;
    const zTargetPreview = zTargetProvider?.target_preview?.requested_position_steps === absoluteTargets.z
        ? zTargetProvider.target_preview : undefined;
    const ownershipGeneration = currentDashboardV2?.ownership_generation ?? 0;

    const ownership = connection?.ownership;
    const ownershipLabel = ownership
        ? `${ownership.transport ?? 'unknown'} / ${ownership.usb ?? 'unknown'} / ${ownership.router ?? 'unknown'}`
        : 'Unavailable';
    const motionControlsAvailable = currentTelemetry === undefined || connection?.hardware_fresh !== true
        ? undefined
        : typeof currentTelemetry.motion.enabled === 'boolean' ? currentTelemetry.motion.enabled : undefined;
    const telemetryUnavailableReason = !linkConnected ? 'Connect to view robot state.'
        : !robotControlReady ? 'Robot runtime is not ready; telemetry is unavailable.'
            : catalogV2Query.error != null ? `Robot state request failed: ${bioXpErrorText(catalogV2Query.error)}`
                : catalogV2Query.data?.dashboard.telemetry == null && catalogV2Query.data != null
                    ? 'Robot did not report telemetry; motion availability is unknown.'
                    : catalogV2Query.isLoading ? 'Loading robot state; motion availability is unknown.'
                        : currentCatalogV2 == null ? 'Robot state is missing or stale; waiting for a fresh observation.'
                            : connection?.hardware_fresh !== true ? 'Hardware observation is stale or not reported; motion availability is unknown.'
                                : null;
    const motionLabel = motionControlsAvailable === true
        ? 'Available — robot controls ready'
        : motionControlsAvailable === false
            ? `Blocked${dashboard?.motion.reason ? ` — ${dashboard.motion.reason}` : ''}`
            : `Unknown — ${telemetryUnavailableReason ?? 'Telemetry is unavailable.'}`;
    const recentCommands = useMemo(
        () => (!displayConnected ? [] : (historyQuery.data?.items ?? [])).slice(0, historyLimit),
        [historyQuery.data?.items, displayConnected, historyLimit],
    );
    useEffect(() => {
        resetInvokeOperatorAction();
        resetComponentStop();
    }, [generation, active, resetInvokeOperatorAction, resetComponentStop]);
    useEffect(() => {
        normalSubmissionRef.current = null;
        setAxisSubmission(null);
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
    }, [generation, active, resetInterruptAggregateAbort, resetInterruptXStop, resetInterruptYStop, resetInterruptZStop, resetInvokeDeckAction, resetInvokeLifecycleAction, resetInvokeXYAction, resetInvokeYAction]);
    useEffect(() => {
        if (!active || deckCommandId != null) return;
        const first = invokeDeckAction.submissions.find(item => item.request.expected_connection_generation === generation && (item.receipt != null || item.commandId != null));
        const identity = first?.receipt?.command_id ?? first?.commandId ?? dashboardDeckReceipt?.command_id;
        if (identity) { setDeckMutationGeneration(generation); setDeckCommandId(identity); }
    }, [active, invokeDeckAction.submissions, generation, deckCommandId, dashboardDeckReceipt]);
    const interruptMutation = (actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all') => {
        if (actionId === 'oem.x.stop') return interruptXStop;
        if (actionId === 'oem.y.stop') return interruptYStop;
        if (actionId === 'oem.z.stop') return interruptZStop;
        return interruptAggregateAbort;
    };
    const interruptPending = (actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all') => interruptMutation(actionId).isPending;
    const interruptAnyPending = interruptXStop.isPending || interruptYStop.isPending || interruptZStop.isPending || interruptAggregateAbort.isPending || componentStop.isPending;
    const busy = axisOutcomeUnresolved || axisAmbiguousError || invokeOperatorAction.isPending || invokeLifecycleActionMutation.isPending || invokeYAction.isPending || invokeDeckAction.isPending || xyPending || interruptAnyPending || componentStop.isPending;
    const latestOperatorReceipt = interruptAggregateAbort.data ?? interruptZStop.data ?? interruptYStop.data ?? interruptXStop.data ?? invokeDeckAction.data ?? invokeLifecycleActionMutation.data ?? invokeYAction.data ?? xyReceipt ?? invokeOperatorAction.data;
    const latestReceiptQuery = useBioXpOperatorReceiptV2(latestOperatorReceipt?.command_id ?? null, generation, linkConnected);
    const displayedLatestReceipt = latestReceiptQuery.data?.command_id === latestOperatorReceipt?.command_id
        ? latestReceiptQuery.data : latestOperatorReceipt;
    const latestReceiptFailure = bioXpReceiptFailureText(displayedLatestReceipt);
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
    const dashboardDeck = currentDashboardV2?.deck;
    const deckAuthorityCoherent = v2AuthorityCoherent
        && deckAction !== undefined
        && dashboardDeck != null
        && deckAction.destination_catalog_revision === dashboardDeck.destination_catalog_revision
        && deckAction.position_table_revision === dashboardDeck.position_table_revision;
    // Retained options are intent only, never retained motion authority. Empty
    // prerequisite projections do not mean the finite robot catalog was deleted.
    const selectionAction = catalogV2Query.data?.actions.find((action) => action.action_id === 'oem.deck.move_to_location');
    const deckDestinations = active && deckSelectionCatalog?.generation === generation
        ? deckSelectionCatalog.options : [];
    const selectedDeckDestination = deckDestinations.find((destination) => destination.target === deckTarget);
    const currentDeckDestination = deckAction?.destination_options?.find((destination) => destination.target === deckTarget);
    useEffect(() => {
        if (!active) {
            setDeckSelectionCatalog(null);
            setDeckTarget('');
            setDeckCameraOffset(false);
            return;
        }
        if (deckSelectionCatalog?.generation !== generation) {
            setDeckSelectionCatalog(null);
            setDeckTarget('');
            setDeckCameraOffset(false);
        }
        const options = selectionAction?.destination_options;
        if (catalogV2Query.isError || !options?.length) return;
        const firstCatalog = deckSelectionCatalog?.generation !== generation;
        setDeckSelectionCatalog({ generation, options });
        setDeckTarget((current) => firstCatalog ? options[0].target
            : options.some((destination) => destination.target === current) ? current : '');
        // A replacement which removes the draft requires explicit reselection.
    }, [active, generation, selectionAction, catalogV2Query.isError]);
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
        return 'Direct robot command.';
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

    const v2ActionDisabledReason = (actionId: string): string | null => {
        const queryOnlyRefresh = actionId === 'oem.deck.collect_authority';
        if (!active || (!queryOnlyRefresh && !linkConnected)) return 'Connect to control the robot.';
        if (!queryOnlyRefresh && !v2AuthorityCoherent) return 'Current robot control state is unavailable.';
        // Installed CCI handlers: X absolute and XYZ relative/Home wait inline;
        // only manual Y absolute is explicitly nonwaiting (ui-inventory UI-01/02).
        // Hold HTTP submission and genuinely unresolved receipts, not historical
        // dashboard busy flags or a source-return terminal Y receipt.
        // A cached enabled row is not reserved admission or an OEM submission queue.
        // Keep independent Stop buttons outside this normal-action check.
        const pendingReadOnly = operatorActionById(invokeOperatorAction.variables?.actionId ?? '')?.safety_class === 'read_only';
        const conflictingSubmission = transferBusy || normalSubmissionRef.current !== null || axisOutcomeUnresolved || axisAmbiguousError || interruptAnyPending || componentStop.isPending || xyPending || invokeLifecycleActionMutation.isPending || lifecycleStatusRecoveryPending || lifecycleReceipt?.status === 'ambiguous'
            || invokeDeckAction.isPending || invokeYAction.isPending
            || (invokeOperatorAction.isPending && !pendingReadOnly);
        if (conflictingSubmission) return 'A command is pending; wait for its receipt before another normal action.';
        // An explicitly requested query can refresh expired observations. Its
        // same-generation published identity is not permission for motion.
        const action = queryOnlyRefresh ? catalogV2Query.data?.actions.find(row =>
            row.action_id === actionId && row.interrupt === false
            && row.request_schema_version === 'bioxp.operator_action_request.v2'
            && row.response_schema_version === 'bioxp.operator_action_receipt.v2') : v2NormalActionById(actionId);
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
    const xAxisDashboard: BioXpOperatorDashboardXAxis | undefined = dashboard?.x_axis ?? undefined;
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
    const xLastFailure = xAxisDashboard?.last_failure ?? xProvider?.lifecycle?.last_failure;
    const xHistoryReceipt = historyPagination.cursor === null ? historyQuery.data?.items?.find(
        (receipt) => receipt.action_id.startsWith('oem.x.'),
    ) ?? null : null;
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
            if (integerInputError(magnitude, operatorActionForPath('/motion/oem/manual/relative')?.inputs.find(input => input.name === 'steps'), 'Requested steps')) return;
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
        if (integerInputError(absoluteTargets[axis], operatorActionForPath('/motion/oem/manual/absolute')?.inputs.find(input => input.name === 'position_steps'), 'Requested target')) return;
        invokeOperatorPath('/motion/oem/manual/absolute', { axis, position_steps: absoluteTargets[axis] });
    };

    const invokeInterrupt = (
        actionId: 'oem.x.stop' | 'oem.y.stop' | 'oem.z.stop' | 'oem.abort_all',
        reason: string,
    ) => {
        if (!linkConnected || generation <= 0 || interruptPending(actionId)) return;
        if (actionId === 'oem.abort_all' && v2InterruptActionById(actionId)?.enabled !== true) return;
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
            : (() => {
                const action = operatorActionForPath('/motion/diagnostics/stop');
                if (!linkConnected || generation <= 0 || componentStop.isPending || action?.enabled !== true || action.safety_class !== 'stop') return;
                invokeAction(action.action_id, { axis }, componentStop);
            })();

    const abortXAggregate = () => invokeInterrupt('oem.abort_all', 'BMS operator requested OEM software Abort: cancel waiters only; motors may continue');

    const submitV2 = (request: BioXpOperatorActionV2Request) => {
        if (v2ActionDisabledReason(request.action_id) !== null) return;
        const releaseSubmission = reserveNormalSubmission();
        if (!releaseSubmission) return;
        setAxisSubmission(null);
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
                if (!releaseSubmission() || currentGenerationRef.current !== submittedGeneration) return;
                setAxisSubmission({ generation: submittedGeneration, commandId: receipt.command_id, actionId: request.action_id, receipt });
                if (tracksY) {
                    setYCommandId(receipt.command_id);
                    setYPendingActionId(null);
                }
                if (tracksZHome) setZHomeCommandId(receipt.command_id);
            },
            onError: (error) => {
                if (!releaseSubmission() || currentGenerationRef.current !== submittedGeneration) return;
                const identity = bioXpPostDispatchCommandIdentity(error);
                if (identity !== null) {
                    setAxisSubmission({ generation: submittedGeneration, commandId: identity.commandId, actionId: request.action_id, receipt: null });
                    if (tracksY) setYCommandId(identity.commandId);
                }
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
        setDeckMutationGeneration(generation);
        invokeDeckAction.submit(request);
    };
    const v2NormalEnvelope = (actionId?: 'oem.deck.collect_authority') => {
        const authority = actionId === 'oem.deck.collect_authority' && active
            ? catalogV2Query.data?.dashboard : currentDashboardV2;
        if (authority == null || (actionId == null && !v2AuthorityCoherent)) return null;
        const idempotencyKey = nextIdempotencyKey('bioxp-oem');
        return {
            expected_connection_generation: generation,
            schema_version: 'bioxp.operator_action_request.v2' as const,
            expected_ownership_generation: authority.ownership_generation,
            idempotency_key: idempotencyKey,
            expected_board_epoch_by_board: {},
        };
    };
    const deckReceiptActionMismatch = deckReceiptQuery.data != null
        && !CANONICAL_DECK_ACTION_IDS.has(deckReceiptQuery.data.action_id);
    const deckReceipt = deckReceiptActionMismatch || deckReceiptQuery.data?.command_id !== effectiveDeckCommandId
        ? undefined : deckReceiptQuery.data;
    const deckReceiptUnavailable = effectiveDeckCommandId !== null && deckReceipt == null;
    const deckPending = deckReceipt?.terminal === false;
    const deckAmbiguous = deckReceiptActionMismatch || deckReceipt?.status === 'ambiguous';
    let deckResolution = null;
    let deckResolutionInvalid = false;
    try { deckResolution = bioXpDeckRecoveryResolution(deckReceipt); } catch { deckResolutionInvalid = true; }
    const deckRecoveryResolved = deckResolution !== null && !deckReceiptQuery.error
        && dashboardDeck != null && dashboardDeck.semantic_state_revision >= deckResolution.semantic_state_revision;
    const deckRecoveryRequired = deckResolutionInvalid || deckReceiptActionMismatch || (!deckRecoveryResolved && (
        deckAmbiguous
        || deckReceipt?.error?.code === 'reconciliation_required'
        || deckReceipt?.completion_class === 'recovery_required'));

    // Deck movement is gated only by live, fresh authority: catalog/dashboard
    // coherence, the robot's own action admission, destination authority, and
    // the robot-reported deck semantic state. Retained ambiguous or
    // recovery-required receipts are history: they stay observable (receipt
    // polling, recovery panel) but never disable a new movement. The robot's
    // admission re-evaluates current state on every submission, so a stale
    // record cannot wedge the deck lane.
    const deckDisabledReason = (transferBusy ? 'A compound command is live or unresolved; wait for its receipt.' : !v2AuthorityCoherent
        ? 'Fresh v2 catalog or dashboard authority is unavailable.'
        : !deckAuthorityCoherent
            ? 'Fresh matching catalog and dashboard deck authority is unavailable.'
        : deckAction == null
            ? 'Robot deck movement action is unavailable.'
            : deckAction.enabled !== true
                ? deckAction.disabled_reason ?? 'Robot deck movement action is unavailable.'
                : dashboardDeck?.ambiguity_state !== 'none'
                    ? `Robot deck ambiguity: ${dashboardDeck?.ambiguity_state ?? 'unknown'}.`
                    : selectedDeckDestination == null
                        ? 'Robot destination catalog is empty.'
                        : currentDeckDestination?.enabled !== true
                            ? currentDeckDestination?.disabled_reason ?? 'Fresh selected destination authority is unavailable.'
                            : null);
    const invokeDeckMove = () => {
        if (deckDisabledReason !== null || selectedDeckDestination == null || deckAction?.expected_board_epoch_by_board == null) return;
        const envelope = v2NormalEnvelope();
        if (!envelope) return;
        // Only an explicit new user action may supersede this reconciled
        // predecessor. Its ambiguous receipt remains unchanged in history.
        if (deckRecoveryResolved && effectiveDeckCommandId !== null) {
            setReconciledDeckPredecessor({ commandId: effectiveDeckCommandId, generation });
        }
        submitDeckV2({
            ...envelope,
            action_id: 'oem.deck.move_to_location',
            expected_board_epoch_by_board: deckAction.expected_board_epoch_by_board,
            inputs: { target: selectedDeckDestination.target, camera_offset: currentDeckDestination?.camera_offset_option === true && deckCameraOffset },
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
        const releaseSubmission = reserveNormalSubmission();
        if (!releaseSubmission) return;
        setXYSubmission(null);
        invokeXYAction.mutate({ request: { ...envelope, action_id: 'oem.xy.move_absolute',
            inputs: { x: absoluteTargets.x, y: yTargetInput } } },
        { onSuccess: receipt => { if (releaseSubmission()) acceptXYSubmission(receipt); },
            onError: error => { if (releaseSubmission()) retainXYUncertainty(error); } });
    };
    const invokeXYHome = () => {
        const envelope = v2NormalEnvelope();
        if (!envelope || xyHomeDisabled) return;
        const releaseSubmission = reserveNormalSubmission();
        if (!releaseSubmission) return;
        setXYSubmission(null);
        invokeXYAction.mutate({ request: { ...envelope, action_id: 'oem.xy.home', inputs: {} } },
            { onSuccess: receipt => { if (releaseSubmission()) acceptXYSubmission(receipt); },
                onError: error => { if (releaseSubmission()) retainXYUncertainty(error); } });
    };
    const yMutationDisabled = (actionId: string) =>
        v2ActionDisabledReason(actionId) !== null
        || (actionId === 'oem.y.move_steps' && !yStepMagnitudeValid)
        || (actionId === 'oem.y.move_absolute' && !yTargetInputValid);
    const yStopDisabled = !linkConnected || generation <= 0 || interruptPending('oem.y.stop');

    const currentYInvokeError = yMutationGeneration === generation
        && !(isDispatchedOutcomeAmbiguous(invokeYAction.error) && axisReceipt?.terminal && axisReceipt.status !== 'ambiguous')
        ? invokeYAction.error : null;
    const submittedAxis = invokeYAction.variables?.request.action_id.split('.')[1];
    const axisErrorLabel = submittedAxis === 'x' || submittedAxis === 'y' || submittedAxis === 'z'
        ? `${submittedAxis.toUpperCase()} command` : null;
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
    const currentDeckInvokeError = invokeDeckAction.submissions.find(item => item.request.expected_connection_generation === generation && item.state === 'uncertain')?.error ?? null;

    const truthLabel = (value: boolean | null | undefined, positive: string, negative: string) => value === true
        ? positive
        : value === false
            ? negative
            : 'unknown';
    const lifecycleAggregateError = isDispatchedOutcomeAmbiguous(currentLifecycleInvokeError)
        ? null
        : currentLifecycleInvokeError;
    const error = currentDeckInvokeError ?? lifecycleAggregateError ?? currentYInvokeError ?? currentXYInvokeError ?? interruptXStop.error ?? interruptYStop.error ?? interruptZStop.error ?? interruptAggregateAbort.error ?? invokeOperatorAction.error ?? connect.error ?? disconnect.error;

    return (
        <div className="space-y-4 p-4 text-slate-100 md:p-6">
            <header>
                <h1 className="text-2xl font-bold">BioXP 3200</h1>
                <p className="mt-1 text-sm text-slate-400">Operator controls</p>
            </header>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Connection & Robot State</h2>
                        <p className={`text-sm ${active ? 'text-emerald-300' : 'text-slate-400'}`}>
                            {connectedLabel}
                        </p>
                        {statusQuery.isError && <p role="status" className="mt-1 text-sm text-amber-200">Connection status refresh failed; checking again. Motion controls are unavailable until status recovers.</p>}
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
                <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Transport / USB / Router</dt>
                        <dd className="mt-1 break-words font-mono text-slate-100">{ownershipLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Motion</dt>
                        <dd className={`mt-1 break-words ${motionControlsAvailable === false ? 'text-amber-200' : 'text-slate-100'}`}>{motionLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Last robot observation</dt>
                        <dd className="mt-1 text-slate-100">{connection?.observed_at ? new Date(connection.observed_at).toLocaleString() : 'Unavailable'}</dd>
                    </div>
                </dl>
            </section>

            <details onToggle={event => { if (event.currentTarget.open) setWorkflowOpen(true); }}>
                <summary className="cursor-pointer text-lg font-semibold">Prepared workflows</summary>
                {workflowOpen && <BioXpWorkflowControls key={generation} generation={generation} connected={active}
                    controlsEnabled={robotControlReady} />}
            </details>

            <BioXpQuickDashboard
                connected={displayConnected}
                data={displayTelemetry}
                isLoading={catalogV2Query.isLoading}
                error={statusQuery.error ?? catalogV2Query.error}
                motionControlsAvailable={motionControlsAvailable}
                unavailableReason={telemetryUnavailableReason}
                stale={showingLastKnown}
            />

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" open={reportsOpen} onToggle={(event) => setReportsOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Operator reports</summary>
                {reportsOpen && <div className="mt-4"><BioXpOperatorReports generation={generation} connected={linkConnected} /></div>}
            </details>

            <section className="rounded-xl border border-amber-700/60 bg-amber-950/20 p-4">
                <h2 className="text-lg font-semibold">Controller Activation & Recovery</h2>
                <p className="mt-1 text-sm text-slate-400">Prepare the controller for motion, or recover it without homing.</p>
                <div className="mt-3 flex flex-wrap gap-3">
                    <button
                        type="button"
                        disabled={!linkConnected || v2ActionDisabledReason('meta.activate_motion') !== null || busy || lifecycleStatusRecoveryPending}
                        title={v2ActionDisabledReason('meta.activate_motion') ?? 'Activate the robot controller'}
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
                {v2AuthorityCoherent && v2ActionDisabledReason('meta.activate_motion') !== null && (
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
                <h2 className="text-lg font-semibold">Deck Movement</h2>
                <p className="mt-1 text-sm text-slate-300">Travel only: moves the tool to a destination. It does not pick up or transfer a plate or cover.</p>
                <div className="mt-3 grid gap-3">
                    <label className="text-sm text-slate-300">
                        Robot destination
                        <select
                            value={selectedDeckDestination?.target ?? ''}
                            disabled={!active || deckDestinations.length === 0}
                            onChange={(event) => setDeckTarget(event.target.value)}
                            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-slate-100"
                        >
                            {selectedDeckDestination == null && <option value="">Choose a destination</option>}
                            {deckDestinations.map((destination) => (
                                <option key={destination.target} value={destination.target}>{destination.label}</option>
                            ))}
                        </select>
                    </label>
                </div>
                <label className="mt-3 block text-sm text-slate-300">
                    <input type="checkbox" checked={selectedDeckDestination?.camera_offset_option === true && deckCameraOffset}
                        disabled={selectedDeckDestination?.camera_offset_option !== true}
                        onChange={(event) => setDeckCameraOffset(event.target.checked)} />
                    {' '}Add camera offset
                </label>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Current location</dt><dd className="font-mono">{currentDashboardV2?.deck?.current_location ?? '—'}</dd></div>
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Current well</dt><dd className="font-mono">{currentDashboardV2?.deck?.current_well ?? '—'}</dd></div>
                </dl>
                <p className={`mt-3 text-sm ${deckDisabledReason ? 'text-amber-200' : 'text-emerald-300'}`}>
                    {deckDisabledReason ? bioXpDeckReadinessText(deckDisabledReason) : 'Ready to move to the selected destination.'}
                </p>
                <button
                    type="button"
                    disabled={deckDisabledReason !== null}
                    title={deckDisabledReason ? bioXpDeckReadinessText(deckDisabledReason) : 'Move to the selected destination'}
                    onClick={invokeDeckMove}
                    className="mt-3 rounded bg-teal-700 px-4 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-35"
                >Move to destination</button>
                <button
                    type="button"
                    disabled={v2ActionDisabledReason('oem.deck.collect_authority') !== null}
                    title={v2ActionDisabledReason('oem.deck.collect_authority') ?? 'Read current axes and latch; no activation, homing or movement.'}
                    onClick={() => {
                        const envelope = v2NormalEnvelope('oem.deck.collect_authority');
                        if (envelope) submitV2({ ...envelope, action_id: 'oem.deck.collect_authority', inputs: {} });
                    }}
                    className="ml-3 mt-3 rounded bg-slate-700 px-4 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-35"
                >Refresh deck readiness (no motion)</button>
                {invokeYAction.variables?.request.action_id === 'oem.deck.collect_authority' && <YOperatorError label="Deck readiness" error={invokeYAction.error} />}
                {deckReceiptUnavailable && (
                    <p role="status" className="mt-3 rounded border border-amber-700 bg-amber-950/30 p-2 text-sm text-amber-100">
                        receipt unavailable / outcome uncertain. Do not resubmit. Reconcile by command ID until a terminal receipt is available.
                    </p>
                )}
                <div data-testid="canonical-command-queue" className="mt-3 text-xs">
                    <p>Robot command queue: {currentDashboardV2?.command_queue == null ? 'unknown' : `${currentDashboardV2.command_queue.items.length} pending`}</p>
                    {currentDashboardV2?.command_queue != null && <details><summary>Pending command details</summary>
                        {currentDashboardV2.command_queue.items.map(item => <p key={item.command_id}>
                            #{item.sequence} · {item.command_id} · {item.status}
                        </p>)}
                    </details>}
                </div>
                <details data-testid="deck-submissions" className="mt-3 space-y-1 text-xs">
                    <summary>Local submission details ({invokeDeckAction.submissions.length})</summary>
                    {invokeDeckAction.submissions.map(item => <DeckSubmissionRow key={item.request.idempotency_key}
                        item={item} generation={generation} active={active} onTerminal={invokeDeckAction.retire}
                        onSelect={commandId => { setDeckMutationGeneration(generation); setDeckCommandId(commandId); }} />)}
                    <p>Settled receipts remain in command history.</p>
                </details>
                <details className="mt-3 text-xs">
                <summary className="cursor-pointer text-slate-400">Deck command details</summary>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded bg-slate-950/60 p-2"><dt className="text-slate-400">Command ID</dt><dd className="font-mono">{effectiveDeckCommandId ?? '—'}</dd></div>
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
                </details>
                <YOperatorError label="Deck enqueue" error={currentDeckInvokeError} />
                {deckResolution && <p className="text-sm text-slate-300">Earlier move reconciled. Historical outcome remains {deckReceipt?.status}; this does not retry the command. {deckRecoveryResolved ? 'New movement still requires fresh robot authority.' : 'Awaiting current robot authority at or after the recovery revision.'}</p>}
                <YOperatorError label="Deck receipt" error={deckReceiptQuery.error} />
                <BioXpTransferControls key={`${generation}:${active}`} generation={generation} connected={linkConnected}
                    controlsEnabled={robotControlReady && v2AuthorityCoherent}
                    commandBusy={busy || deckPending || (currentDashboardV2?.active_commands ?? []).some(command => !command.terminal)}
                    onBusy={setTransferBusy} />
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
                <div className="min-w-0">
                <h2 className="text-lg font-semibold">Manual Controls</h2>
                <p className="mt-1 text-sm text-slate-400">Relative moves use a number of steps. Home, Open and Close use the selected axis controls.</p>
                {axisErrorLabel && submittedAxis !== 'y' && <YOperatorError label={axisErrorLabel} error={currentYInvokeError} reconcileAmbiguousOutcome />}
                {currentAxisSubmission && <p role="status" className="mt-2 text-sm text-slate-300">
                    {currentAxisSubmission.actionId} · {axisReceipt?.status ?? 'receipt unavailable / outcome uncertain'} · {currentAxisSubmission.commandId}
                    {axisOutcomeUnresolved && ' · Do not resubmit; checking the command receipt.'}
                    {bioXpReceiptFailureText(axisReceipt)}
                </p>}
                {currentAxisSubmission && <YOperatorError label="Manual command receipt" error={axisReceiptQuery.error} />}
                {operatorCatalog.isError && (
                    <p className="mt-1 break-words text-sm text-red-300">Robot manual-control catalog unavailable: {bioXpErrorText(operatorCatalog.error)}</p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/machine_config')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/machine_config', {})}
                        className={actionClass}
                    >Show axis settings</button>
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/position_table')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/position_table', {})}
                        className={actionClass}
                    >Show position table</button>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    <article data-testid="serial206-y-authority-panel" style={{ order: 2 }} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <h3 className="font-semibold">Y Axis</h3>
                                <p className="mt-1 text-xs text-slate-400">Y absolute requests return before motion stops.</p>
                            </div>
                            <button type="button" disabled={yStopDisabled} title="Stop the Y motor independently of normal command submission." onClick={interruptY} className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35">Stop</button>
                        </div>
                        <div className="mt-3 grid gap-2">
                            <label className="block text-xs text-slate-300">Relative move steps<input type="number" min={0} max={BIOXP_Y_RELATIVE_MAX_STEPS} value={yStepInput} onChange={(event) => setYStepInput(Number(event.target.value))} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" /></label>
                            <div className="flex flex-wrap gap-1" aria-label="Y step presets">
                                {[1000, 5000, 10000, 25000].map((steps) => (
                                    <button key={steps} type="button" onClick={() => setYStepInput(steps)} className={`rounded px-2 py-1 text-xs ${yStepInput === steps ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>{steps.toLocaleString()}</button>
                                ))}
                            </div>
                            <label className="block text-xs text-slate-300">Absolute target (steps)<input type="number" min={BIOXP_Y_ABSOLUTE_MIN_STEPS} max={BIOXP_Y_ABSOLUTE_MAX_STEPS} value={Number.isFinite(yTargetInput) ? yTargetInput : ''} onChange={(event) => setYTargetInput(event.target.valueAsNumber)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" /></label>
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
                        {submittedAxis === 'y' && <YOperatorError label="Y enqueue" error={currentYInvokeError} reconcileAmbiguousOutcome />}
                        <YOperatorError label="Y STOP" error={interruptYStop.error} />
                        {yPendingActionId && !yCommandId && <p role="status" className="mt-2 text-xs text-cyan-200">Submitting <span className="font-mono">{yPendingActionId}</span>; awaiting durable robot command ID.</p>}
                        {yReceiptCommandId && <p className="mt-2 text-xs text-slate-300">Y request: <span className="font-mono">{yReceiptQuery.data?.status ?? 'receipt unavailable / outcome uncertain'}</span>{yReceiptQuery.data?.completion_class === 'issued_pending' ? ' · awaiting robot completion' : ''}</p>}
                        {yReceiptQuery.data && bioXpReceiptFailureText(yReceiptQuery.data) && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpReceiptFailureText(yReceiptQuery.data)}</p>}
                        {yReceiptQuery.data && (
                            <details className="mt-2 text-xs">
                                <summary className="cursor-pointer text-slate-400">Y command details{yReceiptQuery.data.physical_effect_verified ? ' · physically observed' : ' · physical arrival not verified'}</summary>
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
                            </details>
                        )}
                        {interruptYStop.data && <details className="mt-2 text-xs"><summary>Latest independent Y STOP receipt</summary><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(interruptYStop.data, null, 2)}</pre></details>}
                        {yReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-red-300">Y receipt unavailable: {bioXpErrorText(yReceiptQuery.error)}</p>}
                    </article>
                    <article data-testid="serial206-xy-oem-panel" style={{ order: 1 }} className="rounded-lg border border-cyan-700/60 bg-cyan-950/20 p-3 lg:col-span-2">
                        <h3 className="font-semibold">Combined XY Capability</h3>
                        <p className="mt-1 text-xs text-slate-300">Moves X and Y together in one combined command, not two independent axis commands. For named destinations such as tip waste, use Deck Movement.</p>
                        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                            <label className="rounded bg-slate-950/60 p-2 text-slate-300">
                                X target (steps)
                                <input aria-label="Combined X target (steps)" type="number" step={1}
                                    min={xAbsoluteMinimum} max={xAbsoluteMaximum}
                                    value={Number.isFinite(absoluteTargets.x) ? absoluteTargets.x : ''}
                                    onChange={(event) => { const x = event.target.valueAsNumber; setAbsoluteTargets((current) => ({ ...current, x })); }}
                                    className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" />
                            </label>
                            <label className="rounded bg-slate-950/60 p-2 text-slate-300">
                                Y target (steps)
                                <input aria-label="Combined Y target (steps)" type="number" step={1}
                                    min={BIOXP_Y_ABSOLUTE_MIN_STEPS} max={BIOXP_Y_ABSOLUTE_MAX_STEPS}
                                    value={Number.isFinite(yTargetInput) ? yTargetInput : ''}
                                    onChange={(event) => setYTargetInput(event.target.valueAsNumber)}
                                    className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm" />
                            </label>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" disabled={xyMoveDisabled} onClick={invokeXYMove} className={actionClass}>Move X + Y together</button>
                            <button type="button" disabled={xyHomeDisabled} onClick={invokeXYHome} className={actionClass}>Home X + Y</button>
                        </div>
                        <YOperatorError label="XY command" error={currentXYInvokeError} reconcileAmbiguousOutcome />
                        {xyMoveDisabledReason && <p className="mt-1 text-xs text-amber-200">XY move: {xyMoveDisabledReason}</p>}
                        {xyHomeDisabledReason && <p className="mt-1 text-xs text-amber-200">XY home: {xyHomeDisabledReason}</p>}
                        {xyPending && <p role="status" className="mt-2 text-sm text-amber-200">XY command pending · {xyReceipt?.status ?? 'submitting'} · Do not retry.</p>}
                        {xyReceipt && !xyPending && <p role="status" className="mt-2 text-sm">{bioXpReceiptStatusText(xyReceipt, `XY command ${xyReceipt.status}`)}{xyReceipt.status === 'ambiguous' ? '; outcome unknown; do not resubmit' : ''}</p>}
                        {xyReceipt && bioXpReceiptFailureText(xyReceipt) && <p role="status" className="mt-2 text-sm text-amber-200">{bioXpReceiptFailureText(xyReceipt)}</p>}
                        {currentXYSubmission && xyReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-amber-200">XY command status unavailable: {bioXpErrorText(xyReceiptQuery.error)}. {xyOutcomeUnresolved ? 'Do not retry until the outcome is reconciled.' : 'The received terminal outcome is retained.'}</p>}
                        {xyReceipt && <details className="mt-2 text-xs"><summary>Latest XY command receipt</summary><pre className="mt-1 overflow-auto whitespace-pre-wrap">{JSON.stringify(xyReceipt, null, 2)}</pre></details>}
                    </article>
                    {linkConnected && componentStop.data && <details data-testid="component-stop-receipt"><summary>Independent component Stop receipt</summary><pre>{JSON.stringify(componentStop.data, null, 2)}</pre></details>}
                    {linkConnected && componentStop.error && <p role="alert">Component Stop: {bioXpErrorText(componentStop.error)}</p>}
                    {AXES.map(({ axis, label, controls }) => (
                        <article key={axis} style={{ order: axis === 'x' ? 3 : axis === 'z' ? 4 : axis === 'g' ? 5 : 6 }} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="font-semibold">{label}</h3>
                                <div className="flex gap-2">
                                    <button
                                        type="button"
                                        disabled={!linkConnected || ((axis === 'x' || axis === 'z')
                                            ? generation <= 0 || (axis === 'x' ? interruptPending('oem.x.stop') : interruptPending('oem.z.stop'))
                                            : generation <= 0 || operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true || operatorActionForPath('/motion/diagnostics/stop')?.safety_class !== 'stop' || componentStop.isPending)}
                                        title={axis === 'x'
                                            ? actionUnavailableReason('oem.x.stop', 'Immediate X motor stop')
                                            : axis === 'z'
                                                ? actionUnavailableReason('oem.z.stop', 'Immediate Z motor stop')
                                                : 'Immediate motor stop for this component'}
                                        onClick={() => stopAxis(axis)}
                                        className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35"
                                    >Stop</button>
                                    {axis === 'x' && (
                                        <button
                                            type="button"
                                            disabled={!linkConnected || generation <= 0 || interruptPending('oem.abort_all') || v2InterruptActionById('oem.abort_all')?.enabled !== true}
                                            title={v2InterruptActionById('oem.abort_all')?.disabled_reason ?? 'Software Abort cancels waiters only; motors may continue. Use addressed Stops for motors.'}
                                            onClick={abortXAggregate}
                                            className="rounded bg-red-950 px-3 py-1.5 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35"
                                        >Software Abort (cancel waiters)</button>
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
                                            value={Number.isFinite(manualSteps[axis]) ? manualSteps[axis] : ''}
                                            onChange={(event) => {
                                                const parsed = event.target.valueAsNumber;
                                                setManualSteps((current) => ({ ...current, [axis]: parsed }));
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
                                        Absolute target (steps)
                                        <div className="mt-1 flex gap-2">
                                            <input
                                                type="number"
                                                min={axis === 'x' ? xAbsoluteMinimum : axis === 'z' ? zAbsoluteMinimum : undefined}
                                                max={axis === 'x' ? xAbsoluteMaximum : axis === 'z' ? zAbsoluteMaximum : undefined}
                                                step={1}
                                                value={Number.isFinite(absoluteTargets[axis]) ? absoluteTargets[axis] : ''}
                                                onChange={(event) => {
                                                    const parsed = event.target.valueAsNumber;
                                                    setAbsoluteTargets((current) => ({ ...current, [axis]: parsed }));
                                                }}
                                                className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                            />
                                            <button
                                                type="button"
                                                disabled={!linkConnected || (axis === 'x' ? !xAbsoluteEnabled : axis === 'z' ? !zAbsoluteEnabled : operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true || integerInputError(absoluteTargets[axis], operatorActionForPath('/motion/oem/manual/absolute')?.inputs.find(input => input.name === 'position_steps'), 'Requested target') !== null)}
                                                title={axis === 'x' ? xAbsoluteDisabledReason ?? 'Move X to the absolute target' : axis === 'z' ? zAbsoluteDisabledReason ?? 'Move to the absolute target' : undefined}
                                                onClick={() => runAbsolute(axis)}
                                                className={actionClass}
                                            >Go absolute</button>
                                        </div>
                                        </label>
                                        {axis === 'x' && (
                                        <details className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs text-sky-100">
                                            <summary className="cursor-pointer font-semibold text-slate-200">Axis status and evidence</summary>
                                            <h4 className="mt-2 font-semibold text-sky-50">X readiness</h4>
                                            <p className="mt-1"><strong>Position:</strong> {xPosition} · <strong>Software reference state (not physical proof):</strong> {xReference}</p>
                                            <p className="mt-1"><strong>Lifecycle:</strong> {xLifecycle} · <strong>Authority:</strong> {xAuthority}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {xLeftSwitchState} / {xRightSwitchState} · <strong>GAP13/12 disabled:</strong> {String(xLeftSwitchDisabled)} / {String(xRightSwitchDisabled)}</p>
                                            <p className="mt-1"><strong>Configured GAP4/5/6/205:</strong> {xMaxSpeed} / {xMaxAcceleration} / {xMaxCurrent} / {xStallGuard}</p>
                                            <p className="mt-1"><strong>Catalog absolute bounds:</strong> {xAbsoluteMinimum ?? 'unbounded'}..{xAbsoluteMaximum ?? 'unbounded'} · <strong>Catalog relative magnitude:</strong> {xRelativeMaximum ?? 'unbounded'}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(xSwitchMaskTuple?.['12'] ?? 'unknown')} / {String(xSwitchMaskTuple?.['13'] ?? 'unknown')}. X initialization writes neither register. Profile {xProfileVerified === true ? 'verified' : xProfileVerified === false ? 'not verified' : 'unknown'}.</p>
                                            <p className="mt-1 text-sky-200/80">Controller/software reference is reported exactly as published by the robot provider; it is not independent evidence of the physical X location.</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button type="button" className="rounded bg-red-800 px-3 py-2 text-sm font-semibold hover:bg-red-700 disabled:opacity-35" disabled={!linkConnected || generation <= 0 || interruptPending('oem.x.stop')} title="Immediate X stop" onClick={() => stopAxis('x')}>Stop X</button>
                                                <button type="button" className="rounded bg-red-950 px-3 py-2 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35" disabled={!linkConnected || generation <= 0 || interruptPending('oem.abort_all') || v2InterruptActionById('oem.abort_all')?.enabled !== true} title="Software Abort cancels waiters only; motors may continue" onClick={abortXAggregate}>Software Abort (cancel waiters)</button>
                                            </div>
                                            {xLastFailure != null && <details className="mt-2"><summary className="cursor-pointer text-red-200">Last X failure</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(xLastFailure, null, 2)}</pre></details>}
                                            {xReceipt != null && <details className="mt-2"><summary className="cursor-pointer">Latest X authority receipt</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-sky-200/80">{JSON.stringify(xReceipt, null, 2)}</pre></details>}
                                        </details>
                                        )}
                                        {axis === 'z' && (
                                        <p data-testid="z-target-context" className="text-xs text-cyan-100">
                                            Selected target: {zTargetPreview?.effective_position_steps ?? 'unavailable'} steps · Current minimum: {zTargetProvider?.current_minimum_steps ?? 'unavailable'} steps.
                                            {' '}Based on the latest robot context; the command receipt records the applied target. Home reaches the upper limit (0).
                                        </p>
                                        )}
                                        {axis === 'z' && (
                                        <details className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs text-cyan-100">
                                            <summary className="cursor-pointer font-semibold text-slate-200">Axis status and evidence</summary>
                                            <p className="mt-2"><strong>Dynamic pseudo-home floor:</strong> Z movement uses the robot’s current pseudo-home as a dynamic minimum target. A request below the current value is replaced with that value before dispatch. Z does not automatically return to pseudo-home after every movement.</p>
                                            <p className="mt-1"><strong>Clear and Home:</strong> Z Clear returns to the selected pseudo-home. Manual Home follows the homing sequence and establishes controller coordinate 0.</p>
                                            <p className="mt-1"><strong>Position:</strong> {dashboard?.z_axis?.status?.position_steps ?? 'unknown'} · <strong>Reference:</strong> {dashboard?.z_axis?.status?.reference ?? 'unknown'} · <strong>Authority state:</strong> {dashboard?.z_axis?.provider.state ?? 'unknown'}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {dashboard?.z_axis?.status?.left_switch_state ?? 'unknown'} / {dashboard?.z_axis?.status?.right_switch_state ?? 'unknown'} · <strong>GAP13/12 disabled:</strong> {String(dashboard?.z_axis?.status?.left_switch_disabled ?? 'unknown')} / {String(dashboard?.z_axis?.status?.right_switch_disabled ?? 'unknown')}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(dashboard?.z_axis?.provider.switch_mask_tuple?.['12'] ?? 'unknown')} / {String(dashboard?.z_axis?.provider.switch_mask_tuple?.['13'] ?? 'unknown')}. Z initialization writes neither register.</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button
                                                    type="button"
                                                    className={actionClass}
                                                    disabled={!linkConnected || v2ActionDisabledReason('oem.z.clear') !== null}
                                                    title={v2ActionDisabledReason('oem.z.clear') ?? 'Move to the robot-owned clear position selected from tip and gantry state'}
                                                    onClick={() => {
                                                        const envelope = v2NormalEnvelope();
                                                        if (envelope) submitV2({ ...envelope, action_id: 'oem.z.clear', inputs: {} });
                                                    }}
                                                >Z Clear (automatic position)</button>
                                            </div>
                                            {dashboard?.z_axis?.last_failure != null && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(dashboard.z_axis.last_failure, null, 2)}</pre>}
                                        </details>
                                    )}
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
                                    const enabled = admissionEnabled && !(axis === 'g' && (operation === 'move-negative' || operation === 'move-positive') && integerInputError(manualSteps.g, legacyAction?.inputs.find(input => input.name === 'steps'), 'Requested steps') !== null);
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
                                            title={enabled ? 'Robot control' : unavailableReason}
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
                        pipettes={operatorCatalog.data?.dashboard.pipettes ?? undefined}
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
                {linkConnected && latestReceiptFailure && <p role="alert" className="mt-2 text-sm text-red-300">{latestReceiptFailure}</p>}
                {linkConnected && displayedLatestReceipt && (
                    <details className="mt-3 rounded border border-slate-800 bg-slate-900/60 p-3">
                        <summary className="cursor-pointer text-sm font-semibold">Action receipt details · {bioXpReceiptStatusText(displayedLatestReceipt, displayedLatestReceipt.status)}</summary>
                        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs text-slate-300">{JSON.stringify(displayedLatestReceipt, null, 2)}</pre>
                    </details>
                )}
                </div>
                <details className="order-first w-full max-w-xs self-start rounded-lg border border-slate-800 bg-slate-950/70 p-2 xl:sticky xl:top-4 xl:order-last" open={cameraOpen} onToggle={(event) => setCameraOpen(event.currentTarget.open)}>
                    <summary className="cursor-pointer text-sm font-semibold">Camera</summary>
                    {cameraOpen && <div className="mt-2"><BioXpCameraPanel connected={active} connectionGeneration={active ? generation : null} mutationEnabled={linkConnected && status?.mutation_access?.enabled === true} /></div>}
                </details>
                </div>
            </section>

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Advanced Full Command Catalog</summary>
                {advancedOpen && <><p className="mt-1 text-sm text-slate-400">Additional service, recovery and diagnostic controls.</p><div className="mt-4"><BioXpOperatorControlTabs generation={generation} connected={robotControlReady} /></div></>}
            </details>

            <section className="rounded-xl border border-red-800/70 bg-red-950/30 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-red-200">Software Abort</h2>
                        <p className="max-w-3xl text-sm text-red-200/70">
                            Cancels software waiters, not motor motion. Motors may continue. Use the separate addressed X, Y, Z and Gripper Stops for motor stop requests. This is not a physical emergency stop; physical stopping remains unverified. Source completion, controller ACK, and terminal readback are separate evidence.
                        </p>
                    </div>
                    <button
                        type="button"
                        disabled={!linkConnected || generation <= 0 || interruptAggregateAbort.isPending || v2InterruptActionById('oem.abort_all')?.enabled !== true}
                        title={v2InterruptActionById('oem.abort_all')?.disabled_reason ?? 'Cancels waiters only; motors may continue'}
                        onClick={abortXAggregate}
                        className="rounded bg-red-700 px-5 py-3 font-bold disabled:cursor-not-allowed disabled:opacity-35"
                    >Software Abort (cancel waiters)</button>
                </div>
                <InterruptOutcome label="Software Abort (motors may continue)" receipt={interruptAggregateAbort.data} error={interruptAggregateAbort.error} pending={interruptAggregateAbort.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="X STOP" receipt={interruptXStop.data} error={interruptXStop.error} pending={interruptXStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Y STOP" receipt={interruptYStop.data} error={interruptYStop.error} pending={interruptYStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Z STOP" receipt={interruptZStop.data} error={interruptZStop.error} pending={interruptZStop.isPending} generation={generation} connected={linkConnected} />
            </section>

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                <summary className="cursor-pointer font-semibold">Recent Robot Actions</summary>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
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
                {!displayConnected ? (
                    <p className="mt-2 text-sm text-slate-400">Connect to load robot action receipts.</p>
                ) : historyQuery.isLoading && recentCommands.length === 0 ? (
                    <p role="status" className="mt-2 text-sm text-slate-400">Loading robot action receipts…</p>
                ) : historyQuery.isError && recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">No last-known receipts available; refresh failed.</p>
                ) : historyQuery.data == null ? (
                    <p className="mt-2 text-sm text-slate-400">Robot action receipts have not been loaded.</p>
                ) : recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">{historyPagination.cursor ? 'No robot action receipts recorded on this page.' : 'No robot action receipts recorded.'}</p>
                ) : (
                    <div className="mt-3 space-y-2">
                        {recentCommands.map((record) => (
                            <BioXpHistoryReceiptCard key={`${generation}:${record.command_id}`} receipt={record} generation={generation} connected={linkConnected} />
                        ))}
                    </div>
                )}
                <BioXpHistoryPager pagination={historyPagination} nextCursor={historyQuery.data?.next_cursor ?? null} disabled={!displayConnected || historyQuery.isFetching || historyQuery.isError} />
                {historyQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">Robot action history unavailable: {bioXpErrorText(historyQuery.error)}</p>}
            </details>

            {error && <p role="alert" className="rounded border border-red-800 p-3 text-sm text-red-300">{bioXpErrorText(error)}</p>}
            {statusQuery.isError && <p role="alert" className="text-sm text-red-300">BioXP status unavailable.</p>}
        </div>
    );
}
