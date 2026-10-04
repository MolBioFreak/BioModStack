import { useEffect, useMemo, useRef, useState } from 'react';
import { useBioXpDocumentVisible } from './BioXpObservationVisibility';
import { BioXpServiceRestart } from './BioXpServiceRestart';

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
    useBioXpOperatorReceiptV2,
    useBioXpOperatorReceiptDetailV2,
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
import { BioXpCalibrationSettings } from './BioXpCalibrationSettings';
import { BioXpPipetteSettings } from './BioXpPipetteSettings';
import { BioXpWellPipettingPanel } from './BioXpWellPipettingPanel';
import { BioXpWorkflowEditor } from './BioXpWorkflowEditor';
import { BioXpLiveDeck } from './BioXpLiveDeck';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { BioXpAxisRow, BioXpAxisTelemetry, BioXpStepSlider } from './BioXpRobotPresentation';
import { BioXpStatusStrip } from './BioXpStatusStrip';
import './BioXpRobotControls.css';
import { BioXpWorkflowControls } from './BioXpWorkflowControls';
import { BioXpMovementFailureEvidence } from './BioXpMovementFailureEvidence';
import { BioXpTransferControls } from './BioXpTransferControls';
import { BioXpOperatorReports } from './BioXpOperatorReports';


function DeckSubmissionRow({ item, generation, active, onSelect, onTerminal }: {
    item: BioXpDeckSubmission; generation: number; active: boolean; onSelect: (id: string) => void;
    onTerminal: (key: string, receipt: BioXpOperatorReceiptV2) => void;
}) {
    const documentVisible = useBioXpDocumentVisible();
    const current = active && item.request.expected_connection_generation === generation;
    const commandId = item.receipt?.command_id ?? item.commandId;
    const query = useBioXpOperatorReceiptV2(commandId ?? null, item.request.expected_connection_generation, current && documentVisible && item.receipt?.terminal !== true);
    const receipt = query.data?.action_id === item.request.action_id ? query.data : item.receipt;
    useEffect(() => {
        if (current && !query.error && query.data?.terminal && query.data.action_id === item.request.action_id)
            onTerminal(item.request.idempotency_key, query.data);
    }, [current, query.data, query.error, item, onTerminal]);
    const label = query.data != null && query.data.action_id !== item.request.action_id ? 'receipt unavailable / outcome uncertain'
        : item.state === 'submitting' ? 'submitting / not yet accepted'
        : item.state === 'uncertain' ? 'admission uncertain / waiting for robot update; do not resubmit'
        : item.state === 'not_sent' ? 'not sent / connection changed'
        : item.state === 'rejected' ? 'not accepted' : `robot ${receipt?.status ?? 'accepted'}`;
    const captured = item.request;
    const destinationLabel = captured.action_id === 'oem.deck.move_to_well'
        ? `${deckStations.find(station => station.locationId === captured.inputs.location_id)?.label ?? String(captured.inputs.location_id)} · ${String(captured.inputs.well)}`
        : `${captured.inputs.target}${captured.inputs.camera_offset === true ? ' + camera offset' : ''}`;
    return <p data-request-key={item.request.idempotency_key}>
        {destinationLabel} · {label}
        {' · '}{item.request.idempotency_key}
        {commandId && <button type="button" disabled={!current} onClick={() => onSelect(commandId)}>
            {' · '}{commandId}
        </button>}
        {!current && ' · earlier connection'}
        {query.error != null && ' · receipt unavailable; waiting for a robot update'}
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


let fallbackIdempotencySequence = 0;
const nextIdempotencyKey = (prefix: string): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    fallbackIdempotencySequence += 1;
    return `${prefix}-${fallbackIdempotencySequence}`;
};

const CANONICAL_DECK_ACTION_IDS = new Set([
    'oem.deck.move_to_location',
    'oem.deck.move_to_well',
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
    const documentVisible = useBioXpDocumentVisible();
    const identity = bioXpPostDispatchCommandIdentity(error);
    const commandId = receipt?.command_id ?? identity?.commandId ?? null;
    const query = useBioXpOperatorReceiptV2(commandId, generation, connected && documentVisible && (identity !== null || receipt?.terminal === false));
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
        <p>First stop ACK: {truth(evidence?.first_stop_acknowledged)} · Second stop ACK: {truth(evidence?.second_stop_acknowledged)}</p>
        {uncertain && <p>Outcome or persistence unresolved. Do not resubmit this command; reconcile the retained command identity.</p>}
        {error != null && <p>{bioXpErrorText(error)}</p>}
        {query.error != null && <p>Receipt lookup: {bioXpErrorText(query.error)}</p>}
    </div>;
}

const CONTROL_TABS = ['robot', 'pipettes', 'workflows', 'live-deck'] as const;
type ControlTab = typeof CONTROL_TABS[number];

export function BioXpCockpit({ initialTab = 'robot' }: { initialTab?: ControlTab }) {
    const documentVisible = useBioXpDocumentVisible();
    const statusQuery = useBioXpStatus(documentVisible);
    const status = statusQuery.data;
    const connection = status?.connection;
    const active = connection?.active === true;
    const displayConnected = active;
    const linkConnected = active;
    const linkHealthy = active && !statusQuery.isError && connection?.reachable !== false;
    const robotControlReady = active;
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
    const [historyOpen, setHistoryOpen] = useState(false);
    const [historyLimit, setHistoryLimit] = useState<8 | 25 | 50 | 100>(8);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [reportsOpen, setReportsOpen] = useState(false);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [workflowVisible, setWorkflowVisible] = useState(false);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [cameraOpen, setCameraOpen] = useState(true);
    const [controlTab, setControlTab] = useState<ControlTab>(initialTab);
    const operationalVisible = documentVisible && controlTab !== 'workflows';
    const robotVisible = documentVisible && controlTab === 'robot';
    const liveDeckVisible = documentVisible && controlTab === 'live-deck';
    const controlsVisible = operationalVisible && controlTab !== 'live-deck';
    const [pipettesOpened, setPipettesOpened] = useState(initialTab === 'pipettes');
    const [workflowsOpened, setWorkflowsOpened] = useState(initialTab === 'workflows');
    const [liveDeckOpened, setLiveDeckOpened] = useState(initialTab === 'live-deck');
    const [liveDeckIntent, setLiveDeckIntent] = useState<{ generation: number; selection: BioXpDeckSelection } | null>(null);
    const selectControlTab = (tab: ControlTab) => {
        setControlTab(tab);
        if (tab === 'pipettes') setPipettesOpened(true);
        if (tab === 'workflows') setWorkflowsOpened(true);
        if (tab === 'live-deck') setLiveDeckOpened(true);
    };
    useEffect(() => { selectControlTab(initialTab); }, [initialTab]);
    const [absoluteTargets, setAbsoluteTargets] = useState<Record<'x' | 'z' | 'g', number>>({ x: 60, z: 65000, g: 0 });
    const operatorCatalog = useBioXpOperatorControlCatalog(
        generation,
        linkConnected && operationalVisible,
        null,
        Number.isInteger(absoluteTargets.z) ? absoluteTargets.z : undefined,
    );
    const catalogV2Query = { ...operatorCatalog, data: operatorCatalog.data?.canonical };
    // Observation age is presentation, not a second host admission policy.
    const authorityNow = Date.now();
    const upstreamGeneratedAt = catalogV2Query.data?.dashboard.generated_at;
    const upstreamAgeMs = typeof upstreamGeneratedAt === 'number'
        ? Math.max(0, authorityNow - upstreamGeneratedAt * 1000) : Infinity;
    const localAgeMs = Math.max(0, authorityNow - catalogV2Query.dataUpdatedAt);
    const currentCatalogV2 = active ? catalogV2Query.data : undefined;
    const currentDashboardV2 = currentCatalogV2?.dashboard;
    // Presentation retains the observation in this connection's query key.
    // The generation-keyed catalog preserves actual robot action denials.
    const displayDashboardV2 = displayConnected ? catalogV2Query.data?.dashboard : undefined;
    const displayTelemetry = displayDashboardV2?.telemetry ?? undefined;
    const showingLastKnown = displayTelemetry != null && (catalogV2Query.isError
        || localAgeMs >= 15_000 || upstreamAgeMs >= 15_000 || connection?.hardware_fresh !== true);
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
    const [settledY, setSettledY] = useState<string | null>(null);
    const yReceiptQuery = useBioXpOperatorReceiptDetailV2(yReceiptCommandId, generation, active && documentVisible && (robotVisible || (settledY !== yReceiptCommandId && (yCommandId !== null || yAxisV2?.active_command != null))));
    useEffect(() => { if (yReceiptQuery.data?.terminal) setSettledY(yReceiptCommandId); }, [yReceiptQuery.data, yReceiptCommandId]);
    const [settledZHome, setSettledZHome] = useState<string | null>(null);
    const zHomeReceiptQuery = useBioXpOperatorReceiptV2(currentZHomeCommandId, generation, active && documentVisible && (operationalVisible || settledZHome !== (currentZHomeCommandId)));
    useEffect(() => { if (zHomeReceiptQuery.data?.terminal) setSettledZHome(zHomeReceiptQuery.data.command_id); }, [zHomeReceiptQuery.data]);
    const [settledLifecycle, setSettledLifecycle] = useState<string | null>(null);
    const lifecycleReceiptQuery = useBioXpOperatorReceiptV2(currentLifecycleCommandId, generation, linkConnected && documentVisible && (operationalVisible || settledLifecycle !== (currentLifecycleCommandId)));
    useEffect(() => { if (lifecycleReceiptQuery.data?.terminal) setSettledLifecycle(lifecycleReceiptQuery.data.command_id); }, [lifecycleReceiptQuery.data]);
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
    const [deckDetailsOpen, setDeckDetailsOpen] = useState(false);
    const [settledDeck, setSettledDeck] = useState<string | null>(null);
    const deckReceiptQuery = useBioXpOperatorReceiptDetailV2(effectiveDeckCommandId, generation, active && documentVisible && (liveDeckVisible || (settledDeck !== effectiveDeckCommandId && (deckCommandId !== null || dashboardDeckReceipt?.terminal === false))), true, liveDeckVisible && deckDetailsOpen);
    useEffect(() => { if (deckReceiptQuery.data?.terminal) setSettledDeck(effectiveDeckCommandId); }, [deckReceiptQuery.data, effectiveDeckCommandId]);
    const invokeLifecycleActionMutation = useInvokeBioXpOperatorActionV2();
    const invokeYAction = useInvokeBioXpOperatorActionV2();
    const [axisSubmission, setAxisSubmission] = useState<{ generation: number; commandId: string; actionId: string; receipt: BioXpOperatorReceiptV2 | null } | null>(null);
    const currentAxisSubmission = active && axisSubmission?.generation === generation ? axisSubmission : null;
    const [settledAxis, setSettledAxis] = useState<string | null>(null);
    const axisReceiptQuery = useBioXpOperatorReceiptV2(currentAxisSubmission?.commandId ?? null, generation, active && documentVisible && (operationalVisible || settledAxis !== (currentAxisSubmission?.commandId ?? null)));
    useEffect(() => { if (axisReceiptQuery.data?.terminal) setSettledAxis(axisReceiptQuery.data.command_id); }, [axisReceiptQuery.data]);
    const axisReceipt = currentAxisSubmission == null ? null
        : axisReceiptQuery.data?.command_id === currentAxisSubmission.commandId
            && axisReceiptQuery.data.action_id === currentAxisSubmission.actionId ? axisReceiptQuery.data : currentAxisSubmission.receipt;
    const axisOutcomeUnresolved = currentAxisSubmission != null
        && (axisReceipt == null || !axisReceipt.terminal || axisReceipt.status === 'ambiguous');
    const invokeDeckAction = useInvokeBioXpDeckActionV2(generation, active);
    const interruptXStop = useInterruptBioXpOperatorActionV1();
    const interruptYStop = useInterruptBioXpOperatorActionV1();
    const interruptZStop = useInterruptBioXpOperatorActionV1();
    const interruptAggregateAbort = useInterruptBioXpOperatorActionV1();
    const invokeXYAction = useInvokeBioXpOperatorActionV2();
    const [xySubmission, setXYSubmission] = useState<{ generation: number; commandId: string; receipt: BioXpOperatorReceiptV2 | null } | null>(null);
    const currentXYSubmission = active && xySubmission?.generation === generation ? xySubmission : null;
    const [settledXy, setSettledXy] = useState<string | null>(null);
    const xyReceiptQuery = useBioXpOperatorReceiptV2(currentXYSubmission?.commandId ?? null, generation, active && documentVisible && (operationalVisible || settledXy !== (currentXYSubmission?.commandId ?? null)));
    useEffect(() => { if (xyReceiptQuery.data?.terminal) setSettledXy(xyReceiptQuery.data.command_id); }, [xyReceiptQuery.data]);
    const xyReceipt = currentXYSubmission == null ? null
        : xyReceiptQuery.data?.command_id === currentXYSubmission.commandId ? xyReceiptQuery.data : currentXYSubmission.receipt;
    const xyOutcomeUnresolved = currentXYSubmission != null && (xyReceipt == null || !xyReceipt.terminal || xyReceipt.status === 'ambiguous');
    const currentXYInvokeError = isDispatchedOutcomeAmbiguous(invokeXYAction.error)
        && xyReceipt?.terminal && xyReceipt.status !== 'ambiguous' ? null : invokeXYAction.error;
    const xyPending = invokeXYAction.isPending;
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
    const historyQuery = useBioXpOperatorActionHistory(generation, linkConnected && controlsVisible && historyOpen, historyLimit, historyPagination.cursor);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();

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

    const catalog = active ? operatorCatalog.data : undefined;
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
    const motionControlsAvailable = displayTelemetry?.motion.enabled;
    const telemetryUnavailableReason = !linkConnected ? 'Connect to view robot state.'
        : !robotControlReady ? 'Robot runtime is not ready; telemetry is unavailable.'
            : catalogV2Query.error != null ? `Robot state request failed: ${bioXpErrorText(catalogV2Query.error)}`
                : catalogV2Query.data?.dashboard.telemetry == null && catalogV2Query.data != null
                    ? 'Robot did not report telemetry; motion availability is unknown.'
                    : catalogV2Query.isLoading ? 'Loading robot state; motion availability is unknown.'
                        : currentCatalogV2 == null ? 'Robot state is missing or stale; waiting for a fresh observation.'
                            : null;
    const motionLabel = motionControlsAvailable === true
        ? 'Controllers enabled — not a homing or reference confirmation'
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
    const busy = invokeOperatorAction.isPending || invokeLifecycleActionMutation.isPending || invokeYAction.isPending || invokeDeckAction.isPending || xyPending || interruptAnyPending || componentStop.isPending;
    const latestOperatorReceipt = interruptAggregateAbort.data ?? interruptZStop.data ?? interruptYStop.data ?? interruptXStop.data ?? invokeDeckAction.data ?? invokeLifecycleActionMutation.data ?? invokeYAction.data ?? xyReceipt ?? invokeOperatorAction.data;
    const [settledLatest, setSettledLatest] = useState<string | null>(null);
    const latestReceiptQuery = useBioXpOperatorReceiptV2(latestOperatorReceipt?.command_id ?? null, generation, linkConnected && documentVisible && (operationalVisible || settledLatest !== (latestOperatorReceipt?.command_id ?? null)));
    useEffect(() => { if (latestReceiptQuery.data?.terminal) setSettledLatest(latestReceiptQuery.data.command_id); }, [latestReceiptQuery.data]);
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
    const deckWellAction = v2NormalActionById('oem.deck.move_to_well');
    const dashboardDeck = currentDashboardV2?.deck;
    const deckAuthorityCoherent = v2AuthorityCoherent && deckAction !== undefined;
    // Retained options are intent only, never retained motion authority. Empty
    // prerequisite projections do not mean the finite robot catalog was deleted.
    const selectionAction = catalogV2Query.data?.actions.find((action) => action.action_id === 'oem.deck.move_to_location');
    const deckDestinations = active && deckSelectionCatalog?.generation === generation
        ? deckSelectionCatalog.options : [];
    const selectedDeckDestination = deckDestinations.find((destination) => destination.target === deckTarget);
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

    const v2ActionDisabledReason = (actionId: string): string | null => {
        const queryOnlyRefresh = actionId === 'oem.deck.collect_authority';
        if (!active || (!queryOnlyRefresh && !linkConnected)) return 'Connect to control the robot.';
        if (!queryOnlyRefresh && !v2AuthorityCoherent) return 'Current robot control state is unavailable.';
        // Installed CCI handlers: X absolute and XYZ relative/Home wait inline;
        // only manual Y absolute is explicitly nonwaiting (ui-inventory UI-01/02).
        // Hold only HTTP submission, not unresolved or historical
        // dashboard busy flags or a source-return terminal Y receipt.
        // A cached enabled row is not reserved admission or an OEM submission queue.
        // Keep independent Stop buttons outside this normal-action check.
        const pendingReadOnly = operatorActionById(invokeOperatorAction.variables?.actionId ?? '')?.safety_class === 'read_only';
        const conflictingSubmission = normalSubmissionRef.current !== null || interruptAnyPending || componentStop.isPending || xyPending || invokeLifecycleActionMutation.isPending
            || invokeDeckAction.isPending || invokeYAction.isPending
            || (invokeOperatorAction.isPending && !pendingReadOnly);
        if (conflictingSubmission) return 'A command submission is in flight; wait for its response.';
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
        const releaseSubmission = reserveNormalSubmission();
        if (!releaseSubmission) return;
        const submittedGeneration = generation;
        const submittedOwnershipGeneration = envelope.expected_ownership_generation;
        setLifecycleMutationGeneration(submittedGeneration);
        setLifecycleActionId(actionId);
        setLifecycleCommandId(null);
        setLifecycleOwnershipGeneration(submittedOwnershipGeneration);
        setLifecycleDashboardBaselineAt(currentDashboardV2?.generated_at ?? null);
        invokeLifecycleActionMutation.mutate({ request: { ...envelope, action_id: actionId, inputs: {} } }, {
            onSuccess: (receipt) => {
                if (!releaseSubmission() || currentGenerationRef.current !== submittedGeneration) return;
                if (receipt.ownership_generation !== submittedOwnershipGeneration) return;
                setLifecycleCommandId(receipt.command_id);
            },
            onError: (error) => {
                if (!releaseSubmission() || currentGenerationRef.current !== submittedGeneration) return;
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
        if (!v2AuthorityCoherent || !['oem.deck.move_to_location', 'oem.deck.move_to_well'].includes(request.action_id)) return false;
        const captured = invokeDeckAction.submit(request);
        if (captured) setDeckMutationGeneration(generation);
        return captured;
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

    // Robot action/destination denials and input validation remain authoritative.
    // Host age, reference and semantic projections are not admission. Retained ambiguous or
    // recovery-required receipts are history: they stay observable (receipt
    // notifications, recovery panel) but never disable a new movement. The robot's
    // admission re-evaluates current state on every submission, so a stale
    // record cannot wedge the deck lane.
    const deckDestinationFor = (target: string) => deckAction?.destination_options?.find(destination => destination.target === target)
        ?? deckAction?.destination_options?.find(destination => destination.aliases.includes(target));
    const stationDisabledReason = (target: string): string | null => {
        if (invokeDeckAction.isPending) return 'A deck command submission is in flight; wait for its response.';
        if (!v2AuthorityCoherent) return 'Robot action catalog is unavailable.';
        if (!deckAuthorityCoherent || deckAction == null) return 'Robot deck movement action is unavailable.';
        if (deckAction.enabled !== true) return deckAction.disabled_reason ?? 'Robot deck movement action is unavailable.';
        const destination = deckDestinationFor(target);
        if (!destination) return 'This destination is not in the robot catalog.';
        return destination.enabled === true ? null : destination.disabled_reason ?? 'Selected robot destination is unavailable.';
    };
    const deckDisabledReason = selectedDeckDestination == null
        ? 'Choose a robot destination.' : stationDisabledReason(selectedDeckDestination.target);
    const wellDisabledReason = invokeDeckAction.isPending
        ? 'A deck command submission is in flight; wait for its response.'
        : !v2AuthorityCoherent ? 'Robot action catalog is unavailable.'
            : deckWellAction == null ? 'Robot well-positioning action is unavailable.'
                : deckWellAction.enabled !== true ? deckWellAction.disabled_reason ?? 'Robot well-positioning action is unavailable.' : null;
    const captureDeckIntent = (request: BioXpOperatorActionV2Request, selection: BioXpDeckSelection) => {
        // The shared hook reserves synchronously. A dropped duplicate must not
        // change the requested-target marker or become a deferred intent.
        if (!submitDeckV2(request)) return false;
        setLiveDeckIntent({ generation, selection });
        if (deckRecoveryResolved && effectiveDeckCommandId !== null) {
            setReconciledDeckPredecessor({ commandId: effectiveDeckCommandId, generation });
        }
        return true;
    };
    const invokeNamedDeckMove = (target: string, cameraOffset: boolean) => {
        if (stationDisabledReason(target) !== null) return;
        const destination = deckDestinationFor(target);
        const envelope = v2NormalEnvelope();
        if (!destination || !envelope) return;
        const station = deckStations.find(item => item.id === destination.target || destination.aliases.includes(item.id));
        if (captureDeckIntent({
            ...envelope,
            action_id: 'oem.deck.move_to_location',
            expected_board_epoch_by_board: deckAction?.expected_board_epoch_by_board ?? {},
            inputs: { target: destination.target, camera_offset: destination.camera_offset_option === true && cameraOffset },
        }, { station: station?.id ?? destination.target, wells: [] })) setDeckTarget(destination.target);
    };
    const invokeDeckMove = () => {
        if (selectedDeckDestination) invokeNamedDeckMove(selectedDeckDestination.target, deckCameraOffset);
    };
    const invokeWellDeckMove = (locationId: number, well: string) => {
        if (wellDisabledReason !== null) return;
        const envelope = v2NormalEnvelope();
        if (!envelope) return;
        const station = deckStations.find(item => item.locationId === locationId);
        captureDeckIntent({
            ...envelope,
            action_id: 'oem.deck.move_to_well',
            expected_board_epoch_by_board: deckWellAction?.expected_board_epoch_by_board ?? {},
            inputs: { location_id: locationId, well, position_flag: 1 },
        }, { station: station?.id ?? String(locationId), wells: [well] });
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

    const [xyOpen, setXYOpen] = useState(false);
    const axisSliderBounds = (axis: Axis | 'y') => {
        const reported = displayTelemetry?.axes.find(item => item.axis === axis);
        return { min: reported?.min_steps, max: reported?.max_steps };
    };
    const axisPresentation = (axis: Axis | 'y') => {
        const telemetry = displayTelemetry?.axes.find(item => item.axis === axis);
        return { reference: axis === 'x' ? xReference : axis === 'y' ? yAxisV2?.reference_state : axis === 'z' ? dashboard?.z_axis?.status?.reference : telemetry?.reference,
            position: axis === 'x' ? xPosition : axis === 'y' ? yAxisV2?.position_steps : axis === 'z' ? dashboard?.z_axis?.status?.position_steps : telemetry?.position_steps };
    };
    return (
        <div className="bioxp-cockpit space-y-3 p-3 md:p-4" style={{ color: controlTab === 'live-deck' ? 'var(--text-primary)' : '#f1f5f9' }}>
            <header className="bx-ui bx-header">
                <h1>BioXP 3200</h1>
                <div role="tablist" aria-label="Robot controls" className="bx-tabs">
                    {CONTROL_TABS.map(tab => <button key={tab} type="button" role="tab"
                        id={`control-tab-${tab}`} aria-controls={`control-panel-${tab}`} aria-selected={controlTab === tab}
                        tabIndex={controlTab === tab ? 0 : -1}
                        onKeyDown={event => {
                            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                            event.preventDefault();
                            const index = CONTROL_TABS.indexOf(tab);
                            const next = event.key === 'Home' ? CONTROL_TABS[0] : event.key === 'End' ? CONTROL_TABS[CONTROL_TABS.length - 1]
                                : CONTROL_TABS[(index + (event.key === 'ArrowRight' ? 1 : -1) + CONTROL_TABS.length) % CONTROL_TABS.length];
                            selectControlTab(next);
                            document.getElementById(`control-tab-${next}`)?.focus();
                        }}
                        onClick={() => selectControlTab(tab)}
                        className="bx-tab">
                        {tab === 'robot' ? 'Robot controls' : tab === 'pipettes' ? 'Pipettes' : tab === 'workflows' ? 'Workflows' : 'Live deck movement'}
                    </button>)}
                </div>
                <div title="Cancels software waiters, not motor motion. Motors may continue. This is not a physical emergency stop. Use Stop for motors." aria-label="Stop controls" className="bx-stops">
                    {(['x', 'y', 'z'] as const).map(axis => <button key={axis} type="button"
                        disabled={!linkConnected || generation <= 0 || interruptPending(`oem.${axis}.stop`)}
                        title={`Immediate ${axis.toUpperCase()} stop`}
                        onClick={() => axis === 'y' ? interruptY() : stopAxis(axis)} className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-2 text-sm ">Stop {axis.toUpperCase()}</button>)}
                    <button type="button" disabled={!linkConnected || generation <= 0 || operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true || operatorActionForPath('/motion/diagnostics/stop')?.safety_class !== 'stop' || componentStop.isPending}
                        onClick={() => stopAxis('g')} className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-2 text-sm ">Stop gripper</button>
                    <button type="button" disabled={!linkConnected || generation <= 0 || interruptPending('oem.abort_all') || v2InterruptActionById('oem.abort_all')?.enabled !== true}
                        title={v2InterruptActionById('oem.abort_all')?.disabled_reason ?? 'Software Abort cancels waiters only; motors may continue. Use Stop for motors.'}
                        onClick={abortXAggregate} className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-2 text-sm ring-1  " aria-label="Software Abort (cancel waiters)">Software Abort</button>
                </div>
            </header>
            <BioXpStatusStrip connected={displayConnected} data={displayTelemetry} stale={showingLastKnown}
                connectedLabel={connectedLabel} motionControlsAvailable={motionControlsAvailable} motionLabel={motionLabel}
                observedAt={connection?.observed_at} hardwareFresh={connection?.hardware_fresh} ownershipLabel={ownershipLabel}
                connectionControls={<><button
                            type="button"
                            disabled={!configured || linkHealthy || connect.isPending || disconnect.isPending}
                            onClick={() => connect.mutate(undefined)}
                            className="bx-button"
                         aria-label={linkHealthy ? 'BMS Link Connected' : active ? 'Reconnect BMS Link' : 'Connect BMS Link'}>{linkHealthy ? 'Connected' : active ? 'Reconnect' : 'Connect'}</button>
                        <button
                            type="button"
                            disabled={!active || connect.isPending || disconnect.isPending}
                            onClick={() => disconnect.mutate(undefined)}
                            className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-2 text-sm font-semibold "
                        >Disconnect</button>
</>}
                connectionMessages={<>                        {statusQuery.isError && <p role="status" className="mt-1 text-sm text-[var(--text-secondary)]">Connection status refresh failed; showing last-known observations. The robot checks each request.</p>}
                        {connection?.last_error && <p className="mt-1 break-words text-sm text-[var(--text-secondary)]">{connection.last_error}</p>}
</>}
                controllerControls={<>                    <button
                        type="button"
                        disabled={!linkConnected || v2ActionDisabledReason('meta.activate_motion') !== null || busy}
                        title={v2ActionDisabledReason('meta.activate_motion') ?? 'Enable or recover controllers without homing. This does not establish axis references or clear earlier command outcomes.'}
                        onClick={claimTransport}
                        className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-4 py-2 font-semibold  disabled:cursor-not-allowed "
                     aria-label="Enable controllers">Enable</button>
                    <button
                        type="button"
                        disabled={!linkConnected || v2ActionDisabledReason('meta.recover_motion_non_homing') !== null || busy}
                        title={v2ActionDisabledReason('meta.recover_motion_non_homing') ?? 'Robot-authoritative non-homing recovery'}
                        onClick={recoverMotionNonHoming}
                        className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-4 py-2 font-semibold  disabled:cursor-not-allowed "
                     aria-label="Recover controllers (no homing)">Recover</button>
</>}
                restart={<BioXpServiceRestart generation={generation} />} />
            <div className="bx-ui bx-outcomes">
                {v2AuthorityCoherent && v2ActionDisabledReason('meta.activate_motion') !== null && (
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">
                        Activate: {v2ActionDisabledReason('meta.activate_motion')}
                    </p>
                )}
                {(currentLifecycleActionId !== null || lifecycleReceipt !== undefined || currentLifecycleInvokeError !== null) && (
                    <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
                        <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Action</dt><dd className="font-mono">{currentLifecycleActionId ?? '—'}</dd></div>
                        <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Command ID</dt><dd className="break-all font-mono">{currentLifecycleCommandId ?? lifecycleReceipt?.command_id ?? '—'}</dd></div>
                        <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Lifecycle</dt><dd className="font-mono">{lifecycleReceipt?.status ?? (invokeLifecycleActionMutation.isPending ? 'submitting' : 'unavailable')}</dd></div>
                    </dl>
                )}
                {lifecycleFailureDetail && (
                    <div role="alert" className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-3 text-sm text-[var(--text-secondary)]">
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
                <InterruptOutcome label="Software Abort (motors may continue)" receipt={interruptAggregateAbort.data} error={interruptAggregateAbort.error} pending={interruptAggregateAbort.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="X STOP" receipt={interruptXStop.data} error={interruptXStop.error} pending={interruptXStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Y STOP" receipt={interruptYStop.data} error={interruptYStop.error} pending={interruptYStop.isPending} generation={generation} connected={linkConnected} />
                <InterruptOutcome label="Z STOP" receipt={interruptZStop.data} error={interruptZStop.error} pending={interruptZStop.isPending} generation={generation} connected={linkConnected} />
                    {linkConnected && componentStop.data && <p role="status" data-testid="component-stop-receipt">Independent component Stop receipt: {componentStop.data.command_id} · {componentStop.data.status}{bioXpReceiptFailureText(componentStop.data)}</p>}
                    {linkConnected && componentStop.error && <p role="alert">Component Stop: {bioXpErrorText(componentStop.error)}</p>}
            </div>
            <div role="tabpanel" id="control-panel-workflows" aria-labelledby="control-tab-workflows" hidden={controlTab !== 'workflows'}>
                {workflowsOpened && <BioXpWorkflowEditor visible={documentVisible && controlTab === 'workflows'} generation={generation} connected={linkConnected} controlsEnabled={robotControlReady} />}
                {workflowsOpened && <>
            <details onToggle={event => { setWorkflowVisible(event.currentTarget.open); if (event.currentTarget.open) setWorkflowOpen(true); }}>
                <summary className="cursor-pointer text-lg font-semibold">Prepared request files</summary>
                {workflowOpen && <BioXpWorkflowControls key={generation} generation={generation} connected={active}
                    controlsEnabled={robotControlReady} visible={documentVisible && controlTab === 'workflows' && workflowVisible} />}
            </details>
                </>}
            </div>
            <div role="tabpanel" id="control-panel-live-deck" aria-labelledby="control-tab-live-deck" hidden={controlTab !== 'live-deck'}>
                {liveDeckOpened && <>
                    <p className="mb-2 text-sm" style={{ color: 'var(--text-secondary)' }}>{connectedLabel}
                        {statusQuery.isError && ' · Status refresh unavailable; showing last-known observations.'}
                    </p>
                    <BioXpLiveDeck generation={generation} connected={linkConnected} visible={liveDeckVisible}
                        dashboard={catalogV2Query.data?.dashboard} stale={showingLastKnown || !linkHealthy}
                        selection={liveDeckIntent?.generation === generation ? liveDeckIntent.selection : { station: '', wells: [] }}
                        selectedDestination={selectedDeckDestination}
                        onMoveToStation={target => invokeNamedDeckMove(target, false)} onMoveToWell={invokeWellDeckMove}
                        stationDisabledReason={target => {
                            const reason = stationDisabledReason(target);
                            return reason === null ? null : bioXpDeckReadinessText(reason);
                        }} wellDisabledReason={wellDisabledReason === null ? null : bioXpDeckReadinessText(wellDisabledReason)}
                        doorControls={<div data-testid="live-deck-door-controls" className="space-y-2">
                    <h3 className="font-semibold">Thermal door</h3>
                    <div className="flex gap-2">
                        {(['open', 'close'] as const).map(operation => {
                            const action = operatorActionForPath(`/motion/thermal_door/${operation}`);
                            const enabled = linkConnected && !operatorCatalog.isLoading && action?.enabled === true;
                            return <button key={operation} type="button" disabled={!enabled}
                                title={action?.enabled === true ? 'Robot control' : action?.disabled_reason ?? 'Robot action unavailable.'}
                                onClick={() => runControl('door', operation)}
                                className="flex-1 rounded border px-3 py-2 text-sm font-semibold disabled:opacity-35"
                                style={{ borderColor: 'var(--border-primary)', background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}>
                                {operation === 'open' ? 'Open' : 'Close'}
                            </button>;
                        })}
                    </div>
                    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Direct door controls; not a state toggle.</p>
                    {invokeOperatorAction.variables?.actionId === operatorActionForPath('/motion/thermal_door/open')?.action_id
                        || invokeOperatorAction.variables?.actionId === operatorActionForPath('/motion/thermal_door/close')?.action_id
                        ? <YOperatorError label="Thermal door" error={invokeOperatorAction.error} /> : null}
                </div>}
                        cameraControls={<details className="bld-camera" data-testid="live-deck-camera" open={cameraOpen} onToggle={event => setCameraOpen(event.currentTarget.open)}>
                            <summary className="cursor-pointer text-sm font-semibold">Camera</summary>
                            <div className="mt-2"><BioXpCameraPanel visible={liveDeckVisible && cameraOpen} connected={active}
                                connectionGeneration={active ? generation : null} mutationEnabled={linkConnected && status?.mutation_access?.enabled === true} /></div>
                        </details>}
                        movementControls={<div className="space-y-3 text-sm">
                    <label className="block">
                        Robot destination
                        <select value={selectedDeckDestination?.target ?? ''}
                            disabled={!active || deckDestinations.length === 0}
                            onChange={event => setDeckTarget(event.target.value)}
                            className="mt-1 w-full rounded border p-2"
                            style={{ color: 'var(--text-primary)', background: 'var(--bg-primary)', borderColor: 'var(--border-primary)' }}>
                            {selectedDeckDestination == null && <option value="">Choose a destination</option>}
                            {deckDestinations.map(destination => <option key={destination.target} value={destination.target}>{destination.label}</option>)}
                        </select>
                    </label>
                    <label className="flex items-center gap-2">
                        <input type="checkbox" checked={selectedDeckDestination?.camera_offset_option === true && deckCameraOffset}
                            disabled={selectedDeckDestination?.camera_offset_option !== true}
                            onChange={event => setDeckCameraOffset(event.target.checked)} />
                        Add camera offset
                    </label>
                    <button type="button" disabled={deckDisabledReason !== null}
                        title={deckDisabledReason ? bioXpDeckReadinessText(deckDisabledReason) : 'Move to the selected destination'}
                        onClick={invokeDeckMove}
                        className="w-full rounded bg-teal-700 px-4 py-2 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-35">
                        Move to destination
                    </button>
                    {deckDisabledReason && <p role="status">{bioXpDeckReadinessText(deckDisabledReason)}</p>}
                    <p role="status" data-testid="deck-current-command" className="break-words text-xs">
                        Selected command: {invokeDeckAction.isPending ? 'submitting' : deckReceipt?.status ?? (deckReceiptUnavailable ? 'outcome uncertain' : 'none')}
                        {bioXpReceiptFailureText(deckReceipt)}
                    </p>
                    {deckReceipt?.terminal && deckReceipt.status !== 'completed' && <div>
                        <p>The earlier move did not finish. Its recorded outcome does not block a new request.</p>
                        <button type="button" className="mt-1 rounded border px-2 py-1" onClick={() => setDeckDetailsOpen(true)}>Explain this move</button>
                    </div>}
                    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Travel only. No pickup, liquid handling or tip loading.</p>
                </div>}
                        commandDetailsOpen={deckDetailsOpen}
                        onCommandDetailsToggle={setDeckDetailsOpen}
                        commandDetails={<div className="space-y-2 break-words">
                <BioXpMovementFailureEvidence receipt={deckReceipt} />
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
                {deckResolution && <p className="text-sm text-slate-300">Earlier move reconciled. Historical outcome remains {deckReceipt?.status}; this does not retry the command. {deckRecoveryResolved ? 'The robot checks each new request.' : 'Current recovery revision is not yet observed.'}</p>}
                <YOperatorError label="Deck receipt" error={deckReceiptQuery.error} />

                        </div>}
                        transferControls={visible => <BioXpTransferControls visible={visible} key={`${generation}:${active}`} generation={generation} connected={linkConnected} />}
                    />
                    {error && <p role="alert" className="mt-3 rounded border border-red-600 p-3 text-sm" style={{ color: 'var(--text-primary)' }}>{bioXpErrorText(error)}</p>}
                </>}
            </div>
            <div hidden={controlTab === 'workflows' || controlTab === 'live-deck'}>
            <div role="tabpanel" id="control-panel-robot" aria-labelledby="control-tab-robot" hidden={controlTab !== 'robot'} className="bx-ui">
                <section className="bx-axes" aria-label="Axis controls">
                    <div className="bx-axis-heading"><span>Axis</span><span>Reference</span><span>Position</span><span>Move by</span><span>Home</span><span>Go to</span><span>Stop</span></div>
                    <div className="bx-axis-list"><BioXpAxisRow axis="y" label="Y Axis" testId="serial206-y-authority-panel" {...axisPresentation('y')}
 controls={<><button type="button" disabled={yMutationDisabled('oem.y.move_steps')} title={yActionDisabledReason('oem.y.move_steps', 'Y relative move unavailable.')} onClick={() => invokeYMoveSteps(-Math.abs(yStepInput))} data-operation="move-negative" className="bx-button" aria-label="Move −">−</button>
<button type="button" disabled={yMutationDisabled('oem.y.manual_panel_home')} title={yActionDisabledReason('oem.y.manual_panel_home', 'Y manual-panel home unavailable.')} onClick={() => invokeYHome('oem.y.manual_panel_home')} data-operation="home" className="bx-button" aria-label="Home">Home</button>
<button type="button" disabled={yMutationDisabled('oem.y.move_steps')} title={yActionDisabledReason('oem.y.move_steps', 'Y relative move unavailable.')} onClick={() => invokeYMoveSteps(Math.abs(yStepInput))} data-operation="move-positive" className="bx-button" aria-label="Move +">+</button></>}
 relative={<><BioXpStepSlider label="Y relative steps" min={0} max={axisSliderBounds('y').max} value={yStepInput} onChange={setYStepInput} /><label className="bx-relative"><span className="bx-sr-only">Relative move steps</span><input type="number" min={0} max={BIOXP_Y_RELATIVE_MAX_STEPS} value={yStepInput} onChange={(event) => setYStepInput(Number(event.target.value))} className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm" /></label>
</>}
 absolute={<div className="bx-absolute" title="Y absolute requests return before motion stops."><BioXpStepSlider label="Y absolute target" min={axisSliderBounds('y').min} max={axisSliderBounds('y').max} value={yTargetInput} onChange={setYTargetInput} />                            <label className="block text-xs text-[var(--text-secondary)]"><span className="bx-sr-only">Absolute target (steps)</span><input type="number" min={BIOXP_Y_ABSOLUTE_MIN_STEPS} max={BIOXP_Y_ABSOLUTE_MAX_STEPS} value={Number.isFinite(yTargetInput) ? yTargetInput : ''} onChange={(event) => setYTargetInput(event.target.valueAsNumber)} className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm" /></label>
<button type="button" disabled={yMutationDisabled('oem.y.move_absolute')} title={yActionDisabledReason('oem.y.move_absolute', 'Y absolute move unavailable.')} onClick={() => invokeYMoveAbsolute(yTargetInput)} data-operation="absolute" className="bx-button" aria-label="Go absolute">Go</button></div>}
 stop={<button type="button" disabled={yStopDisabled} title="Stop the Y motor independently of normal command submission." onClick={interruptY} className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-1.5 text-sm font-semibold  ">Stop</button>}
 details={<>                            <p className="mt-2 text-[var(--text-secondary)]">Robot-owned Serial-206 Y authority. Controller completion and physical observation stay separate.</p>
                            <div className="mt-2 text-[var(--text-secondary)]">Board epoch: <span className="font-mono text-[var(--text-secondary)]">{yAxisV2?.active_board_epoch ?? '—'}</span> · Lifecycle: <span className="font-mono text-[var(--text-secondary)]">{yAxisV2?.lifecycle_state ?? '—'}</span></div>
                        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Position</dt><dd className="font-mono">{yAxisV2?.position_steps ?? '—'}</dd><dd className={yAxisV2?.position_reply_valid ? 'text-[var(--text-secondary)]' : 'text-[var(--text-secondary)]'}>{yAxisV2 ? `${yAxisV2.position_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.position_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Reference</dt><dd className="font-mono">{yAxisV2?.reference_state ?? '—'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Speed</dt><dd className="font-mono">{yAxisV2?.speed_steps_s ?? '—'}</dd><dd className={yAxisV2?.speed_reply_valid ? 'text-[var(--text-secondary)]' : 'text-[var(--text-secondary)]'}>{yAxisV2 ? `${yAxisV2.speed_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.speed_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Home switch</dt><dd className="font-mono">{yAxisV2?.left_switch_raw ?? '—'}</dd><dd className={yAxisV2?.left_switch_reply_valid ? 'text-[var(--text-secondary)]' : 'text-[var(--text-secondary)]'}>{yAxisV2 ? `${yAxisV2.left_switch_reply_valid ? 'Valid' : 'Invalid'} reply · status ${yAxisV2.left_switch_status_code ?? 'not reported'}` : 'Reply unavailable'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Profile</dt><dd className={yAxisV2?.profile_readback_valid ? 'text-[var(--text-secondary)]' : 'text-[var(--text-secondary)]'}>{yAxisV2 ? yAxisV2.profile_readback_valid ? 'Valid' : `Invalid${yAxisV2.profile_mismatches.length > 0 ? ` · ${yAxisV2.profile_mismatches.join('; ')}` : ''}` : '—'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Updated</dt><dd className="font-mono">{yAxisV2 ? new Date(yAxisV2.updated_at * 1000).toISOString() : '—'}</dd></div>
                            <div className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2"><dt className="text-[var(--text-secondary)]">Physical proof</dt><dd className="font-mono text-[var(--text-secondary)]">{yAxisV2?.physical_position_verified ? 'observed' : 'not observed'}</dd></div>
                        </dl>
                        {yReceiptQuery.data && (
                            <details className="mt-2 text-xs">
                                <summary className="cursor-pointer text-[var(--text-secondary)]">Y command details{yReceiptQuery.data.physical_effect_verified ? ' · physically observed' : ' · physical arrival not verified'}</summary>
                            <div className="mt-3 grid gap-2 text-xs lg:grid-cols-2">
                                <div className="rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2"><strong>Completion</strong><p className="mt-1">class={yReceiptQuery.data.completion_class ?? 'not reported'} · terminal position={String(yReceiptQuery.data.observed_values?.terminal_position_steps ?? 'not reported')} · terminal speed={String(yReceiptQuery.data.observed_values?.terminal_speed_steps_s ?? 'not reported')} · discrepancy={String(yReceiptQuery.data.observed_values?.discrepancy_steps ?? 'not reported')}</p></div>
                                <div className="rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2"><strong>Independent physical observation</strong><p className="mt-1">physical_effect_verified={String(yReceiptQuery.data.physical_effect_verified)}</p></div>
                            </div>
                            </details>
                        )}
{yReceiptCommandId && yReceiptQuery.data?.status === 'completed' && yReceiptQuery.data.completion_class !== 'issued_pending' && !bioXpReceiptFailureText(yReceiptQuery.data) && <p className="mt-2 text-xs">Y request: completed</p>}
<BioXpAxisTelemetry axis={displayTelemetry?.axes.find(item => item.axis === 'y')} /></>}
 notices={<>                        {submittedAxis === 'y' && <YOperatorError label="Y enqueue" error={currentYInvokeError} reconcileAmbiguousOutcome />}
                        <YOperatorError label="Y STOP" error={interruptYStop.error} />
                        {yPendingActionId && !yCommandId && <p role="status" className="mt-2 text-xs text-[var(--text-secondary)]">Submitting <span className="font-mono">{yPendingActionId}</span>; awaiting durable robot command ID.</p>}
                        {yReceiptCommandId && (yReceiptQuery.data?.status !== 'completed' || yReceiptQuery.data.completion_class === 'issued_pending' || bioXpReceiptFailureText(yReceiptQuery.data)) && <p className="mt-2 text-xs text-[var(--text-secondary)]">Y request: <span className="font-mono">{yReceiptQuery.data?.status ?? 'receipt unavailable / outcome uncertain'}</span>{yReceiptQuery.data?.completion_class === 'issued_pending' ? ' · awaiting robot completion' : ''}</p>}
                        {yReceiptQuery.data && bioXpReceiptFailureText(yReceiptQuery.data) && <p role="alert" className="mt-2 text-sm text-[var(--text-secondary)]">{bioXpReceiptFailureText(yReceiptQuery.data)}</p>}
                        {interruptYStop.data && <p role="status">Latest independent Y STOP receipt: {interruptYStop.data.command_id} · {interruptYStop.data.status}{bioXpReceiptFailureText(interruptYStop.data)}</p>}
                        {yReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-[var(--text-secondary)]">Y receipt unavailable: {bioXpErrorText(yReceiptQuery.error)}</p>}
</>} />
{AXES.map(({ axis, label, controls }) => (
 <BioXpAxisRow key={axis} axis={axis} label={label} {...axisPresentation(axis)}
 controls={<>                                {controls.map(({ label: controlLabel, operation }) => {
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
                                            data-operation={operation} aria-label={controlLabel} className="bx-button"
                                        >{operation === 'move-negative' ? '−' : operation === 'move-positive' ? '+' : controlLabel}</button>
                                    );
                                })}
</>}
 relative={axis !== 'door' ? <><BioXpStepSlider label={`${axis.toUpperCase()} relative steps`} min={1} max={axisSliderBounds(axis).max} value={manualSteps[axis]} onChange={value => setManualSteps(current => ({ ...current, [axis]: value }))} /><label className="bx-relative">
                                        <span className="bx-sr-only">Relative move steps</span>
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
                                            className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm"
                                        />
                                    </label>
</> : null}
 absolute={axis !== 'door' ? <>                                    <div className="block text-xs text-[var(--text-secondary)]">
                                        <div className="bx-absolute">
                                            <BioXpStepSlider label={`${axis.toUpperCase()} absolute target`}
                                                min={axisSliderBounds(axis).min == null ? undefined : Math.max(axisSliderBounds(axis).min!, axis === 'x' ? xAbsoluteMinimum ?? -Infinity : axis === 'z' ? zAbsoluteMinimum ?? -Infinity : -Infinity)}
                                                max={axisSliderBounds(axis).max == null ? undefined : Math.min(axisSliderBounds(axis).max!, axis === 'x' ? xAbsoluteMaximum ?? Infinity : axis === 'z' ? zAbsoluteMaximum ?? Infinity : Infinity)}
                                                value={absoluteTargets[axis]} onChange={value => setAbsoluteTargets(current => ({ ...current, [axis]: value }))} />
                                            <label><span className="bx-sr-only">Absolute target (steps)</span>
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
                                                className="min-w-0 flex-1 rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm"
                                            />
                                            </label>
                                            <button
                                                type="button"
                                                disabled={!linkConnected || (axis === 'x' ? !xAbsoluteEnabled : axis === 'z' ? !zAbsoluteEnabled : operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true || integerInputError(absoluteTargets[axis], operatorActionForPath('/motion/oem/manual/absolute')?.inputs.find(input => input.name === 'position_steps'), 'Requested target') !== null)}
                                                title={axis === 'x' ? xAbsoluteDisabledReason ?? 'Move X to the absolute target' : axis === 'z' ? zAbsoluteDisabledReason ?? 'Move to the absolute target' : undefined}
                                                onClick={() => runAbsolute(axis)}
                                                className="bx-button"
                                             aria-label="Go absolute">Go</button>
                                        </div>
                                        </div>
{axis === 'z' && <p data-testid="z-target-context" className="bx-target-hint">min {zTargetProvider?.current_minimum_steps ?? 'unavailable'}{zTargetPreview?.effective_position_steps != null && zTargetPreview.effective_position_steps !== absoluteTargets.z && ` → ${zTargetPreview.effective_position_steps}`}</p>}</> : null}
 stop={                                    <button
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
                                        className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-1.5 text-sm font-semibold  "
                                    >Stop</button>
} extras={<>                                {axis === 'z' && <button type="button" className="bx-button"
                                    disabled={!linkConnected || v2ActionDisabledReason('oem.z.diagnostic_home_axis') !== null}
                                    title={v2ActionDisabledReason('oem.z.diagnostic_home_axis') ?? 'Physical Z switch-search home; establishes controller zero without the ordinary Home preposition.'}
                                    onClick={() => {
                                        const envelope = v2NormalEnvelope();
                                        if (envelope) submitV2({ ...envelope, action_id: 'oem.z.diagnostic_home_axis', inputs: {} });
                                    }} aria-label="Z switch-search recovery home">Switch-search home</button>}
{axis === 'z' && (                                                <button
                                                    type="button"
                                                    className="bx-button"
                                                    disabled={!linkConnected || v2ActionDisabledReason('oem.z.clear') !== null}
                                                    title={v2ActionDisabledReason('oem.z.clear') ?? 'Move to the robot-owned clear position selected from tip and gantry state'}
                                                    onClick={() => {
                                                        const envelope = v2NormalEnvelope();
                                                        if (envelope) submitV2({ ...envelope, action_id: 'oem.z.clear', inputs: {} });
                                                    }}
                                                 aria-label="Z Clear (automatic position)">Clear Z</button>
)}</>}
 details={<>{axis === 'x' && <>
    {xReceipt != null && xReceipt.status === 'completed' && !bioXpReceiptFailureText(xReceipt) && <p>Latest X authority receipt: {String(xReceipt.command_id ?? "unknown")} · {String(xReceipt.status ?? "unknown")} · {bioXpReceiptFailureText(xReceipt)}</p>}
    <h4 className="mt-2 font-semibold text-[var(--text-secondary)]">X readiness</h4>
                                            <p className="mt-1"><strong>Position:</strong> {xPosition} · <strong>Software reference state (not physical proof):</strong> {xReference}</p>
                                            <p className="mt-1"><strong>Lifecycle:</strong> {xLifecycle} · <strong>Authority:</strong> {xAuthority}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {xLeftSwitchState} / {xRightSwitchState} · <strong>GAP13/12 disabled:</strong> {String(xLeftSwitchDisabled)} / {String(xRightSwitchDisabled)}</p>
                                            <p className="mt-1"><strong>Configured GAP4/5/6/205:</strong> {xMaxSpeed} / {xMaxAcceleration} / {xMaxCurrent} / {xStallGuard}</p>
                                            <p className="mt-1"><strong>Catalog absolute bounds:</strong> {xAbsoluteMinimum ?? 'unbounded'}..{xAbsoluteMaximum ?? 'unbounded'} · <strong>Catalog relative magnitude:</strong> {xRelativeMaximum ?? 'unbounded'}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(xSwitchMaskTuple?.['12'] ?? 'unknown')} / {String(xSwitchMaskTuple?.['13'] ?? 'unknown')}. X initialization writes neither register. Profile {xProfileVerified === true ? 'verified' : xProfileVerified === false ? 'not verified' : 'unknown'}.</p>
                                            <p className="mt-1 text-[var(--text-secondary)]">Controller/software reference is reported exactly as published by the robot provider; it is not independent evidence of the physical X location.</p>
</>}{axis === 'z' && <>                                            <p className="mt-2"><strong>Dynamic pseudo-home floor:</strong> Z movement uses the robot’s current pseudo-home as a dynamic minimum target. A request below the current value is replaced with that value before dispatch. Z does not automatically return to pseudo-home after every movement.</p>
                                            <p className="mt-1"><strong>Clear and Home:</strong> Z Clear returns to the selected pseudo-home. Manual Home follows the homing sequence and establishes controller coordinate 0.</p>
                                            <p className="mt-1"><strong>Position:</strong> {dashboard?.z_axis?.status?.position_steps ?? 'unknown'} · <strong>Reference:</strong> {dashboard?.z_axis?.status?.reference ?? 'unknown'} · <strong>Authority state:</strong> {dashboard?.z_axis?.provider.state ?? 'unknown'}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {dashboard?.z_axis?.status?.left_switch_state ?? 'unknown'} / {dashboard?.z_axis?.status?.right_switch_state ?? 'unknown'} · <strong>GAP13/12 disabled:</strong> {String(dashboard?.z_axis?.status?.left_switch_disabled ?? 'unknown')} / {String(dashboard?.z_axis?.status?.right_switch_disabled ?? 'unknown')}</p>
                                            <p className="mt-1"><strong>SAP12/13 observed:</strong> {String(dashboard?.z_axis?.provider.switch_mask_tuple?.['12'] ?? 'unknown')} / {String(dashboard?.z_axis?.provider.switch_mask_tuple?.['13'] ?? 'unknown')}. Z initialization writes neither register.</p>
</>}<BioXpAxisTelemetry axis={displayTelemetry?.axes.find(item => item.axis === axis)} /></>} notices={<>{axis === 'x' && <>                                             {xLastFailure != null && <p className="text-[var(--text-secondary)]">Last X failure: {bioXpReceiptFailureText({ error: xLastFailure }) || bioXpErrorText(xLastFailure)}</p>}
                                             {xReceipt != null && (xReceipt.status !== 'completed' || bioXpReceiptFailureText(xReceipt)) && <p>Latest X authority receipt: {String(xReceipt.command_id ?? "unknown")} · {String(xReceipt.status ?? "unknown")} · {bioXpReceiptFailureText(xReceipt)}</p>}
</>}{axis === 'z' && <>                                             {dashboard?.z_axis?.last_failure != null && <p className="text-[var(--text-secondary)]">Last Z failure: {bioXpReceiptFailureText({ error: dashboard.z_axis.last_failure }) || bioXpErrorText(dashboard.z_axis.last_failure)}</p>}
</>}                            {axis === 'z' && zHomeFailureDetail && (
                                <div role="alert" className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-3 text-sm text-[var(--text-secondary)]">
                                    <p className="font-semibold">{zHomeFailureDetail.failure}</p>
                                    <p>Provider failure: <span className="font-mono">{zHomeFailureDetail.provider_failure}</span></p>
                                    <p>{`Axis ${zHomeFailureDetail.axis} · Board ${zHomeFailureDetail.board} · Motor ${zHomeFailureDetail.motor} · Source return ${zHomeFailureDetail.source_return_code}`}</p>
                                    <p>{`Controller acknowledged: ${zHomeFailureDetail.controller_acknowledged ? 'yes' : 'no'}`}</p>
                                    <p>{`Terminal state verified: ${zHomeFailureDetail.controller_terminal_state_verified ? 'yes' : 'no'}`}</p>
                                    <p>{`Physical effect verified: ${zHomeFailureDetail.physical_effect_verified ? 'yes' : 'no'}`}</p>
                                    <p>{`Lifecycle: ${zHomeFailureDetail.lifecycle_state} · Reference: ${zHomeFailureDetail.reference_state}`}</p>
                                </div>
                            )}
</>} />
))}
</div>
                {axisErrorLabel && submittedAxis !== 'y' && <YOperatorError label={axisErrorLabel} error={currentYInvokeError} reconcileAmbiguousOutcome />}
                {currentAxisSubmission && <p role="status" className="mt-2 text-sm text-[var(--text-secondary)]">
                    {currentAxisSubmission.actionId} · {axisReceipt?.status ?? 'receipt unavailable / outcome uncertain'} · {currentAxisSubmission.commandId}
                    {axisOutcomeUnresolved && ' · Do not resubmit; checking the command receipt.'}
                    {bioXpReceiptFailureText(axisReceipt)}
                </p>}
                {currentAxisSubmission && <YOperatorError label="Manual command receipt" error={axisReceiptQuery.error} />}
                {operatorCatalog.isError && (
                    <p className="mt-1 break-words text-sm text-[var(--text-secondary)]">Robot manual-control catalog unavailable: {bioXpErrorText(operatorCatalog.error)}</p>
                )}
<article className="bx-xy" data-testid="serial206-xy-oem-panel"><button type="button" className="bx-xy-toggle" aria-expanded={xyOpen} onClick={() => setXYOpen(open => !open)}>X + Y together <span aria-hidden="true">{xyOpen ? '▾' : '▸'}</span></button><div className={`bx-xy-controls ${xyOpen ? 'is-open' : ''}`}><h3>X + Y together</h3>                            <label className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2 text-[var(--text-secondary)]">
                                X target (steps)
                                <input aria-label="Combined X target (steps)" type="number" step={1}
                                    min={xAbsoluteMinimum} max={xAbsoluteMaximum}
                                    value={Number.isFinite(absoluteTargets.x) ? absoluteTargets.x : ''}
                                    onChange={(event) => { const x = event.target.valueAsNumber; setAbsoluteTargets((current) => ({ ...current, x })); }}
                                    className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm" />
                            </label>
                            <label className="rounded bg-[var(--surface-control,var(--bg-tertiary))] p-2 text-[var(--text-secondary)]">
                                Y target (steps)
                                <input aria-label="Combined Y target (steps)" type="number" step={1}
                                    min={BIOXP_Y_ABSOLUTE_MIN_STEPS} max={BIOXP_Y_ABSOLUTE_MAX_STEPS}
                                    value={Number.isFinite(yTargetInput) ? yTargetInput : ''}
                                    onChange={(event) => setYTargetInput(event.target.valueAsNumber)}
                                    className="mt-1 w-full rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 font-mono text-sm" />
                            </label>
                            <button type="button" disabled={xyMoveDisabled} onClick={invokeXYMove} className="bx-button">Move X + Y together</button>
                            <button type="button" disabled={xyHomeDisabled} onClick={invokeXYHome} className="bx-button">Home X + Y</button>
</div><div className="bx-notices">                        <YOperatorError label="XY command" error={currentXYInvokeError} reconcileAmbiguousOutcome />
                        {xyMoveDisabledReason && <p className="mt-1 text-xs text-[var(--text-secondary)]">XY move: {xyMoveDisabledReason}</p>}
                        {xyHomeDisabledReason && <p className="mt-1 text-xs text-[var(--text-secondary)]">XY home: {xyHomeDisabledReason}</p>}
                        {(xyPending || xyOutcomeUnresolved) && <p role="status" className="mt-2 text-sm text-[var(--text-secondary)]">XY command pending · {xyReceipt?.status ?? 'submitting'} · Do not retry.</p>}
                        {xyReceipt && !xyPending && !xyOutcomeUnresolved && <p role="status" className="mt-2 text-sm">{bioXpReceiptStatusText(xyReceipt, `XY command ${xyReceipt.status}`)}{xyReceipt.status === 'ambiguous' ? '; outcome unknown; do not resubmit' : ''}</p>}
                        {xyReceipt && bioXpReceiptFailureText(xyReceipt) && <p role="status" className="mt-2 text-sm text-[var(--text-secondary)]">{bioXpReceiptFailureText(xyReceipt)}</p>}
                        {currentXYSubmission && xyReceiptQuery.error && <p role="alert" className="mt-2 text-sm text-[var(--text-secondary)]">XY command status unavailable: {bioXpErrorText(xyReceiptQuery.error)}. {xyOutcomeUnresolved ? 'Do not retry until the outcome is reconciled.' : 'The received terminal outcome is retained.'}</p>}
</div></article>                </section>
            </div>
            <div role="tabpanel" id="control-panel-pipettes" aria-labelledby="control-tab-pipettes" hidden={controlTab !== 'pipettes'}>
                {pipettesOpened && <>
                <BioXpPipetteControlPanel visible={documentVisible && controlTab === 'pipettes'}
                    generation={generation}
                    connected={robotControlReady && operatorCatalog.data !== undefined}
                    pipettes={operatorCatalog.data?.dashboard.pipettes ?? undefined}
                    freshness={operatorCatalog.data?.dashboard.snapshot.freshness}
                    actions={catalog?.actions}
                    catalogLoading={operatorCatalog.isLoading}
                    invokePending={invokeOperatorAction.isPending}
                    invokeAction={(actionId, inputs) => invokeAction(actionId, inputs)}
                />
                <BioXpWellPipettingPanel visible={documentVisible && controlTab === 'pipettes'} generation={generation} connected={linkConnected}
                    destinations={selectionAction?.destination_options ?? []}
                    positionTableRevision={selectionAction?.position_table_revision} />
                <details onToggle={event => setSettingsOpen(event.currentTarget.open)} className="mt-4 rounded border border-slate-700 p-3">
                    <summary className="cursor-pointer font-semibold">Settings & calibration</summary>
                    <BioXpPipetteSettings visible={settingsOpen && documentVisible && controlTab === 'pipettes'} key={`pipette-settings:${generation}:${active}`} generation={generation} connected={linkConnected} />
                    <BioXpCalibrationSettings visible={settingsOpen && documentVisible && controlTab === 'pipettes'} key={`calibration:${generation}:${active}`} generation={generation} connected={linkConnected} />
                </details>
                </>}
            </div>
            <section aria-label="Latest command" className="bx-ui bx-last"><strong>Last command</strong>
                {invokeOperatorAction.error && (
                    <p role="alert" className="mt-3 whitespace-pre-wrap break-words text-sm text-[var(--text-secondary)]">{bioXpErrorText(invokeOperatorAction.error)}</p>
                )}
                {(invokeOperatorAction.isPending || invokeLifecycleActionMutation.isPending || invokeYAction.isPending || invokeDeckAction.isPending || interruptAnyPending) && (
                    <p role="status" className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-2 text-sm text-[var(--text-secondary)]">Command accepted by BMS; waiting for the robot-owned terminal receipt. Stop and Abort remain available.</p>
                )}
                {linkConnected && latestReceiptFailure && <p role="alert" className="mt-2 text-sm text-[var(--text-secondary)]">{latestReceiptFailure}</p>}
                {linkConnected && displayedLatestReceipt && (
                    <details className="mt-3 rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-3">
                        <summary className="cursor-pointer text-sm font-semibold">Action receipt details · {bioXpReceiptStatusText(displayedLatestReceipt, displayedLatestReceipt.status)}</summary>
                         <p>{displayedLatestReceipt.command_id} · {displayedLatestReceipt.completion_class ?? "completion not reported"} · physical effect verified={String(displayedLatestReceipt.physical_effect_verified)}</p>
                    </details>
                )}
            </section>
<div className="bx-ui bx-drawers">            <details onToggle={event => setHistoryOpen(event.currentTarget.open)} className="rounded-xl border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-3">
                <summary className="cursor-pointer font-semibold">History <span>recent robot actions · reports &amp; export</span></summary><h3>Recent Robot Actions</h3>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                    <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                        Entries
                        <select
                            className="rounded border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] px-2 py-1 text-[var(--text-secondary)]"
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
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">Connect to load robot action receipts.</p>
                ) : historyQuery.isLoading && recentCommands.length === 0 ? (
                    <p role="status" className="mt-2 text-sm text-[var(--text-secondary)]">Loading robot action receipts…</p>
                ) : historyQuery.isError && recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">No last-known receipts available; refresh failed.</p>
                ) : historyQuery.data == null ? (
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">Robot action receipts have not been loaded.</p>
                ) : recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">{historyPagination.cursor ? 'No robot action receipts recorded on this page.' : 'No robot action receipts recorded.'}</p>
                ) : (
                    <div className="mt-3 space-y-2">
                        {recentCommands.map((record) => (
                            <BioXpHistoryReceiptCard key={`${generation}:${record.command_id}`} receipt={record} generation={generation} connected={linkConnected && controlsVisible && historyOpen} />
                        ))}
                    </div>
                )}
                <BioXpHistoryPager pagination={historyPagination} nextCursor={historyQuery.data?.next_cursor ?? null} disabled={!displayConnected || historyQuery.isFetching || historyQuery.isError} />
                        <details className="rounded-xl border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-4" open={reportsOpen} onToggle={(event) => setReportsOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Reports &amp; export</summary>
                {reportsOpen && controlsVisible && <div className="mt-4"><BioXpOperatorReports generation={generation} connected={linkConnected} /></div>}
            </details>

</details>
<details className="bx-tools"><summary>Tools &amp; service</summary><div className="bx-tools-body">                <div className="mt-3 flex flex-wrap gap-2">
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/machine_config')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/machine_config', {})}
                        className="bx-button"
                    >Show axis settings</button>
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/position_table')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/position_table', {})}
                        className="bx-button"
                    >Show position table</button>
                </div>
<h3>Temperatures</h3><div>{!displayTelemetry?.temperatures.length && 'Not reported'}{displayTelemetry?.temperatures.map(sensor => <p key={sensor.sensor}>{sensor.label}: {sensor.available ? `${sensor.temperature_c ?? '—'} ${sensor.unit}` : 'Not reported'}</p>)}</div>            <details className="rounded-xl border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-tertiary))] p-4" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-lg font-semibold">Advanced Full Command Catalog</summary>
                {advancedOpen && <><p className="mt-1 text-sm text-[var(--text-secondary)]">Additional service, recovery and diagnostic controls.</p><div className="mt-4"><BioXpOperatorControlTabs visible={controlsVisible} generation={generation} connected={robotControlReady} catalogObservation={operatorCatalog} zTargetSteps={Number.isInteger(absoluteTargets.z) ? absoluteTargets.z : undefined} /></div></>}
            </details>
</div></details></div><div className="bx-ui">                {historyQuery.isError && <p role="alert" className="mt-2 text-sm text-[var(--text-secondary)]">Robot action history unavailable: {bioXpErrorText(historyQuery.error)}</p>}
            {error && <p role="alert" className="rounded border border-[var(--border-primary)] p-3 text-sm text-[var(--text-secondary)]">{bioXpErrorText(error)}</p>}
            {statusQuery.isError && <p role="alert" className="text-sm text-[var(--text-secondary)]">BioXP status unavailable.</p>}
</div>
            </div>
        </div>
    );
}
