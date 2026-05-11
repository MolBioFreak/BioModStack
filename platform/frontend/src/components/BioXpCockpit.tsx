import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { useIsMutating } from '@tanstack/react-query';
import {
    getCameraStreamUrl,
    useSetCameraControl,
    useAxisStatus,
    useAxisStatusBatch,
    useBioXpCapabilities,
    useBioXpInterlinkState,
    useBioXpOperationCapabilities,
    useBioXpOperationReadiness,
    useBioXpStatus,
    useCameraAutoRecover,
    useCameraControls,
    useCameraDevices,
    useCameraReset,
    useCameraSnapshot,
    useCameraStop,
    useCameraStreamHealth,
    useCameraStreamState,
    useChillerBaseline,
    useChillerHardReset,
    useSetChillerFan,
    useSetChillerPwm,
    useSetChillerRates,
    useChillerSnapshot,
    useClearLock,
    useHeadClearLockOperation,
    useHeadLiftIncrementOperation,
    useHomeAxis,
    useLatchLock,
    useLatchStatus,
    useLatchUnlock,
    useLatchLockOperation,
    useLatchUnlockOperation,
    useLedOff,
    useLedPct,
    useLedRgb,
    useLiquidAspirate,
    useLiquidDispense,
    useLiquidInit,
    useLiquidMix,
    useLiquidStatus,
    useMicroMoveProofOperation,
    useLiquidTip,
    useMarkMotionDesynced,
    useMarkMotionReferenced,
    useMotionArmStrictStartup,
    usePrepareSafeOperation,
    useMotionPowerDiag,
    useMotionPowerEnable,
    useMotionPowerStatus,
    useMotionRangeStatus,
    useMotionReferenceStatus,
    useMoveAbsolute,
    useMoveRelative,
    useOemRuntimeCommand,
    useOemRuntimeEmergencyStop,
    useEmergencyStopOperation,
    useOemRuntimeRecover,
    useOemRuntimeState,
    useOemRuntimeStatus,
    useOemStartupDoorEvent,
    useOemStartupLatest,
    useOemStartupRequest,
    usePrepareToRunJobReadiness,
    usePrepareInterlock,
    useRuntimeStatus,
    useSetThermalFan,
    useSetThermalPwm,
    useSetThermalRates,
    useSetChillerTemp,
    useSetThermalTemp,
    useThermalBaseline,
    useThermalFastProfile,
    useThermalHardReset,
    useThermalSnapshot,
    useVisionBarcodeRead,
    useVisionInspect
} from '../lib/bioxpClient';
import type { AxisMotionResult, AxisName, AxisStatus, BioXpOperationReport, CameraControlRow, ChillerBankName, LiquidStatus, MotionPowerStatus, MotionReferenceStatus, ThermalBankName } from '../lib/bioxpClient';
import { BioXpProtocolRunner } from './BioXpProtocolRunner';
import { deriveRuntimeStatusSummary } from './bioxpConnectionSemantics.js';

const getErrorMessage = (error: unknown) => {
    if (error instanceof Error) {
        return error.message;
    }
    if (typeof error === 'string') {
        return error;
    }
    if (error && typeof error === 'object') {
        return JSON.stringify(error);
    }
    return null;
};

const SectionCard = ({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) => (
    <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
        <div className="space-y-1 border-b border-border-secondary pb-2">
            <h3 className="text-sm font-semibold text-content">{title}</h3>
            {subtitle && <p className="text-xs text-content-muted">{subtitle}</p>}
        </div>
        {children}
    </div>
);

const JsonBlock = ({ title, data, fallback = 'No data yet.' }: { title: string; data: unknown; fallback?: string }) => (
    <details className="rounded border border-border-primary bg-surface/40 p-2">
        <summary className="cursor-pointer text-xs font-semibold text-content-muted hover:text-content transition-colors">
            {title} Debug payload
        </summary>
        <pre className="mt-2 text-[10px] font-mono text-content-muted p-3 bg-[#000000] rounded border border-border-primary overflow-x-auto max-h-72">
            {data ? JSON.stringify(data, null, 2) : fallback}
        </pre>
    </details>
);

const CONNECTION_STICKY_WINDOW_MS = 15000;
const CAMERA_STREAM_MODES = [
    { key: '640x480', label: '640x480 / 30 FPS', width: 640, height: 480, maxFps: 30 },
    { key: '1280x720', label: '1280x720 / 30 FPS', width: 1280, height: 720, maxFps: 30 },
    { key: '1920x1080', label: '1920x1080 / 15 FPS', width: 1920, height: 1080, maxFps: 15 },
] as const;
const CAMERA_HOLD_JOG_REPEAT_DELAY_MS = 120;
const CAMERA_HOLD_JOG_WAIT_TIMEOUT_S = 8.0;
const CAMERA_HOLD_JOG_AXES = ['x', 'y', 'z'] as const;
const CAMERA_HOLD_JOG_PROFILE = {
    speed: 100,
    acc: 50,
    xy_step: 160,
    z_step: 80,
} as const;
const V4L2_CTRL_TYPE_INTEGER = 1;
const V4L2_CTRL_TYPE_BOOLEAN = 2;
const V4L2_CTRL_TYPE_MENU = 3;
const V4L2_CTRL_FLAG_DISABLED = 0x00000002;
const V4L2_CTRL_FLAG_READ_ONLY = 0x00000004;
const CAMERA_CONTROL_PRIORITY = [
    'zoom_absolute',
    'auto_exposure',
    'exposure_time_absolute',
    'white_balance_automatic',
    'white_balance_temperature',
    'focus_auto',
    'focus_absolute',
    'gain',
    'brightness',
    'contrast',
    'saturation',
    'sharpness',
    'backlight_compensation',
    'power_line_frequency',
    'gamma',
    'hue',
    'pan_absolute',
    'tilt_absolute',
] as const;
const CAMERA_CONTROL_LABELS: Record<string, string> = {
    zoom_absolute: 'Zoom',
    focus_auto: 'Autofocus',
    focus_absolute: 'Focus',
    auto_exposure: 'Exposure Mode',
    exposure_time_absolute: 'Exposure Time',
    exposure_auto: 'Exposure Mode',
    exposure_absolute: 'Exposure',
    gain: 'Gain',
    brightness: 'Brightness',
    contrast: 'Contrast',
    saturation: 'Saturation',
    sharpness: 'Sharpness',
    white_balance_automatic: 'Auto White Balance',
    white_balance_temperature_auto: 'Auto White Balance',
    white_balance_temperature: 'White Balance',
    backlight_compensation: 'Backlight',
    power_line_frequency: 'Power Line',
    gamma: 'Gamma',
    hue: 'Hue',
    pan_absolute: 'Pan',
    tilt_absolute: 'Tilt',
};
const CAMERA_CONTROL_OPTIONS: Record<string, Array<{ value: number; label: string }>> = {
    focus_auto: [
        { value: 0, label: 'Off' },
        { value: 1, label: 'On' },
    ],
    white_balance_temperature_auto: [
        { value: 0, label: 'Manual' },
        { value: 1, label: 'Auto' },
    ],
    white_balance_automatic: [
        { value: 0, label: 'Manual' },
        { value: 1, label: 'Auto' },
    ],
    auto_exposure: [
        { value: 0, label: 'Auto' },
        { value: 1, label: 'Manual' },
        { value: 2, label: 'Shutter' },
        { value: 3, label: 'Aperture' },
    ],
    exposure_auto: [
        { value: 0, label: 'Auto' },
        { value: 1, label: 'Manual' },
        { value: 2, label: 'Shutter' },
        { value: 3, label: 'Aperture' },
    ],
    power_line_frequency: [
        { value: 0, label: 'Disabled' },
        { value: 1, label: '50 Hz' },
        { value: 2, label: '60 Hz' },
    ],
};

const normalizeCameraControlName = (name: string) =>
    name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');

const clampCameraControlValue = (control: CameraControlRow, value: number) => {
    const step = Math.max(1, Number(control.step) || 1);
    const minimum = Number(control.minimum) || 0;
    const maximum = Number(control.maximum) || minimum;
    const clamped = Math.min(maximum, Math.max(minimum, value));
    const snapped = minimum + Math.round((clamped - minimum) / step) * step;
    return Math.min(maximum, Math.max(minimum, snapped));
};

const isWritableCameraControl = (control: CameraControlRow) => {
    const flags = Number(control.flags) || 0;
    if ((flags & V4L2_CTRL_FLAG_DISABLED) !== 0 || (flags & V4L2_CTRL_FLAG_READ_ONLY) !== 0) {
        return false;
    }
    return [V4L2_CTRL_TYPE_INTEGER, V4L2_CTRL_TYPE_BOOLEAN, V4L2_CTRL_TYPE_MENU].includes(Number(control.type));
};

const cameraControlSortWeight = (control: CameraControlRow) => {
    const normalizedName = normalizeCameraControlName(control.name);
    const idx = CAMERA_CONTROL_PRIORITY.indexOf(normalizedName as (typeof CAMERA_CONTROL_PRIORITY)[number]);
    return idx === -1 ? CAMERA_CONTROL_PRIORITY.length + 100 : idx;
};

const get24vStateLabel = (rail: MotionPowerStatus['rail_24v']) => {
    if (!rail) {
        return 'UNKNOWN';
    }
    if (rail.no24v == null) {
        return 'UNKNOWN';
    }
    return rail.no24v ? 'NO_24V' : 'OK';
};

const getBoardAckSummary = (boardStatus: MotionPowerStatus['board_status']) => {
    if (!boardStatus || typeof boardStatus !== 'object') {
        return 'n/a';
    }
    const keys = Object.keys(boardStatus).sort();
    if (!keys.length) {
        return 'n/a';
    }
    return keys
        .map((key) => {
            const ack = boardStatus[key];
            const statusText = ack?.status_str ?? (ack ? 'RESP' : 'NR');
            return `0x${Number(key).toString(16).toUpperCase()}:${statusText}`;
        })
        .join('  ');
};

const getReferenceRows = (referenceStatus: MotionReferenceStatus | undefined) => {
    const rows = referenceStatus?.rows;
    if (!rows || typeof rows !== 'object') {
        return [] as Array<{ axis: string; state: string; updated_at?: string; origin?: number | null }>;
    }
    return Object.entries(rows).map(([axis, row]) => ({
        axis,
        state: String(row?.state ?? 'unknown'),
        updated_at: typeof row?.updated_at === 'string' ? row.updated_at : undefined,
        origin: typeof row?.origin_position_steps === 'number' ? row.origin_position_steps : null,
    }));
};

const getReferenceSummary = (referenceStatus: MotionReferenceStatus | undefined) => {
    const rows = getReferenceRows(referenceStatus);
    if (!rows.length) {
        return 'reference unknown';
    }
    const referenced = rows.filter((row) => row.state === 'referenced').length;
    return `${referenced}/${rows.length} referenced`;
};

const getLiquidTruthLabel = (liquidStatus: LiquidStatus | undefined) => {
    if (!liquidStatus) {
        return 'pending';
    }
    return liquidStatus.hardware_truth_level ?? (liquidStatus.available ? 'software shadow' : 'unavailable');
};

const getAxisDirectionState = (axisData: AxisStatus | undefined, _steps: number) => {
    const leftActive = axisData?.switch_activity?.left_active === true;
    const rightActive = axisData?.switch_activity?.right_active === true;
    const leftMasked = axisData?.preset?.disable_left === true;
    const rightMasked = axisData?.preset?.disable_right === true;
    const conflictingSwitches = leftActive && rightActive;

    return {
        blocked: false,
        conflictingSwitches,
        leftActive,
        rightActive,
        leftMasked,
        rightMasked,
    };
};

const hasMutationKeyPrefix = (mutationKey: unknown, prefix: readonly string[]) =>
    Array.isArray(mutationKey) && prefix.every((part, index) => mutationKey[index] === part);

const AxisControls = ({
    axis,
    label,
    enabled,
    pollIntervalMs = 8000,
}: {
    axis: AxisName;
    label: string;
    enabled: boolean;
    pollIntervalMs?: number | false;
}) => {
    const moveRelative = useMoveRelative();
    const moveAbsolute = useMoveAbsolute();
    const homeAxis = useHomeAxis();
    const [steps, setSteps] = useState(100);
    const [absolutePosition, setAbsolutePosition] = useState(0);
    const [lastMotionResult, setLastMotionResult] = useState<AxisMotionResult | null>(null);
    const [commandStartPosition, setCommandStartPosition] = useState<number | null>(null);
    const [commandLabel, setCommandLabel] = useState<string | null>(null);
    const [captureBundle, setCaptureBundle] = useState(true);
    const [dryRunBundle, setDryRunBundle] = useState(false);
    const [operatorNote, setOperatorNote] = useState('');
    const [snapshotRefsText, setSnapshotRefsText] = useState('');
    const localMotionBusy = moveRelative.isPending || moveAbsolute.isPending || homeAxis.isPending;
    const { data, isLoading, isError, error, refetch } = useAxisStatus(
        axis,
        enabled,
        localMotionBusy ? 1000 : pollIntervalMs,
    );

    const reportedPosition = data?.status?.position?.position;
    const reportedSpeed = data?.status?.speed?.speed;

    useEffect(() => {
        if (typeof reportedPosition === 'number') {
            setAbsolutePosition(reportedPosition);
        }
    }, [reportedPosition]);

    const moving = typeof reportedSpeed === 'number' ? reportedSpeed !== 0 : false;
    const leftActive = data?.switch_activity?.left_active;
    const rightActive = data?.switch_activity?.right_active;
    const leftMasked = data?.preset?.disable_left === true;
    const rightMasked = data?.preset?.disable_right === true;
    const switchConflictObserved = leftActive === true && rightActive === true;
    const negativeMoveBlocked = false;
    const positiveMoveBlocked = false;
    const homeToZeroBlocked = false;
    const homeToZeroBlockedTitle = 'Return this axis to controller coordinate 0; does not run switch-search homing. Raw switch readback is displayed but does not software-block this command.';
    const referenceHomeTitle = 'Manual switch-search home is disabled after live X/Z limit-switch ignore incidents. Use OEM startup-step recipes only after the robot-local predicate is fixed.';
    const motionProfile = lastMotionResult?.motion_profile ?? {
        speed: data?.preset?.speed ?? 100,
        acc: data?.preset?.acc ?? 50,
        no_delta_timeout_s: 2,
    };
    const liveDelta =
        commandStartPosition != null && typeof reportedPosition === 'number'
            ? reportedPosition - commandStartPosition
            : null;
    const prepPolicyNote = lastMotionResult?.prep_policy?.note;
    const truthSummary = lastMotionResult?.motion_truth?.summary;
    const truthEvidenceLevel = lastMotionResult?.motion_truth?.evidence_level?.replaceAll('_', ' ');
    const artifactBundle = lastMotionResult?.artifact_bundle;
    const artifactSnapshotCount = artifactBundle?.snapshot_refs?.length ?? 0;
    const normalizedOperatorNote = operatorNote.trim();
    const normalizedSnapshotRefs = snapshotRefsText
        .split(/\n|,/)
        .map((value) => value.trim())
        .filter(Boolean);
    const motionArtifactPayload = {
        capture_bundle: captureBundle,
        dry_run_bundle: captureBundle ? dryRunBundle : false,
        operator_note: normalizedOperatorNote || undefined,
        snapshot_refs: normalizedSnapshotRefs,
    };

    const startCommand = (labelText: string) => {
        setCommandLabel(labelText);
        setCommandStartPosition(typeof reportedPosition === 'number' ? reportedPosition : null);
    };

    const finishCommand = (payload: AxisMotionResult) => {
        setLastMotionResult(payload);
        setCommandLabel(null);
        if (typeof payload?.position_after?.position === 'number') {
            setAbsolutePosition(payload.position_after.position);
        } else if (typeof payload?.home?.position_after?.position === 'number') {
            setAbsolutePosition(payload.home.position_after.position);
        }
    };

    return (
        <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-3">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-accent font-semibold">{label}</span>
                    {isLoading && <span className="text-xs text-content-muted animate-pulse">Polling...</span>}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => refetch()}
                        className="px-2 py-0.5 text-[10px] rounded border border-border-primary text-content-muted hover:text-content transition-colors"
                    >
                        Refresh
                    </button>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full border ${moving || localMotionBusy ? 'bg-warning/20 text-warning border-warning/30' : 'bg-success/20 text-success border-success/30'}`}>
                        {moving || localMotionBusy ? 'MOVING' : 'IDLE'}
                    </span>
                </div>
            </div>

            {isError && <div className="text-xs text-error">{getErrorMessage(error) ?? 'Unable to read axis status.'}</div>}

            <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-content-muted">
                <div>Pos: {reportedPosition ?? 'n/a'}</div>
                <div>Speed: {reportedSpeed ?? 'n/a'}</div>
                <div>L sw: {leftActive == null ? 'n/a' : leftActive ? '1' : '0'}{leftMasked ? ' (masked)' : ''}</div>
                <div>R sw: {rightActive == null ? 'n/a' : rightActive ? '1' : '0'}{rightMasked ? ' (masked)' : ''}</div>
                <div>Cmd speed: {motionProfile.speed}</div>
                <div>Cmd acc: {motionProfile.acc}</div>
                <div>Stall abort: {motionProfile.no_delta_timeout_s}s</div>
                <div>Live Δ: {liveDelta ?? 'n/a'}</div>
            </div>

            <div className="flex gap-2 items-center">
                <span className="text-xs text-content-muted w-12">Step</span>
                <input
                    type="number"
                    value={steps}
                    onChange={(e) => setSteps(Number(e.target.value))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => {
                        startCommand(`REL ${-Math.abs(steps)}`);
                        moveRelative.mutate(
                            { axis, steps: -Math.abs(steps), ...motionArtifactPayload },
                            {
                                onSuccess: (payload) => finishCommand(payload),
                                onError: () => setCommandLabel(null),
                            },
                        );
                    }}
                    disabled={!enabled || moveRelative.isPending || negativeMoveBlocked}
                    className="px-3 py-1.5 bg-surface-secondary hover:bg-surface border border-accent/20 text-content text-xs rounded-lg transition-colors"
                >
                    ◄
                </button>
                <button
                    onClick={() => {
                        startCommand(`REL ${Math.abs(steps)}`);
                        moveRelative.mutate(
                            { axis, steps: Math.abs(steps), ...motionArtifactPayload },
                            {
                                onSuccess: (payload) => finishCommand(payload),
                                onError: () => setCommandLabel(null),
                            },
                        );
                    }}
                    disabled={!enabled || moveRelative.isPending || positiveMoveBlocked}
                    className="px-3 py-1.5 bg-surface-secondary hover:bg-surface border border-accent/20 text-content text-xs rounded-lg transition-colors"
                >
                    ►
                </button>
                <button
                    onClick={() => {
                        if (homeToZeroBlocked) {
                            return;
                        }
                        startCommand('HOME → 0');
                        homeAxis.mutate(
                            { axis, ...motionArtifactPayload },
                            {
                                onSuccess: (payload) => finishCommand(payload),
                                onError: () => setCommandLabel(null),
                            },
                        );
                    }}
                    disabled={!enabled || homeAxis.isPending || homeToZeroBlocked}
                    title={homeToZeroBlockedTitle}
                    className="ml-auto px-3 py-1.5 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                >
                    Home → 0
                </button>
                <span
                    title={referenceHomeTitle}
                    className="px-3 py-1.5 bg-warning/10 text-warning text-xs rounded-lg border border-warning/20"
                >
                    Switch-home disabled
                </span>
            </div>

            <div className="flex gap-2 items-center">
                <span className="text-xs text-content-muted w-12">Abs</span>
                <input
                    type="number"
                    value={absolutePosition}
                    onChange={(e) => setAbsolutePosition(Number(e.target.value))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => {
                        startCommand(`ABS ${absolutePosition}`);
                        moveAbsolute.mutate(
                            { axis, position_steps: absolutePosition, ...motionArtifactPayload },
                            {
                                onSuccess: (payload) => finishCommand(payload),
                                onError: () => setCommandLabel(null),
                            },
                        );
                    }}
                    disabled={!enabled || moveAbsolute.isPending}
                    className="px-4 py-1.5 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                >
                    Move Absolute
                </button>
            </div>

            <div className="space-y-2 rounded border border-border-primary bg-surface/40 p-3">
                <div className="flex flex-wrap items-center gap-3 text-[10px] text-content-muted">
                    <label className="inline-flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={captureBundle}
                            onChange={(e) => {
                                const checked = e.target.checked;
                                setCaptureBundle(checked);
                                if (!checked) {
                                    setDryRunBundle(false);
                                }
                            }}
                        />
                        Capture validation bundle
                    </label>
                    <label className="inline-flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={dryRunBundle}
                            disabled={!captureBundle}
                            onChange={(e) => setDryRunBundle(e.target.checked)}
                        />
                        Dry-run bundle only
                    </label>
                </div>
                <textarea
                    value={operatorNote}
                    onChange={(e) => setOperatorNote(e.target.value)}
                    rows={2}
                    placeholder="Operator note for supervised validation"
                    className="w-full rounded border border-border-primary bg-surface px-3 py-2 text-[11px] text-content"
                />
                <textarea
                    value={snapshotRefsText}
                    onChange={(e) => setSnapshotRefsText(e.target.value)}
                    rows={2}
                    placeholder="Snapshot refs or image paths (comma or newline separated)"
                    className="w-full rounded border border-border-primary bg-surface px-3 py-2 text-[11px] text-content"
                />
            </div>

            {(commandLabel || lastMotionResult) && (
                <div className="space-y-1">
                    <div className="text-[10px] text-content-muted font-mono">
                        {commandLabel ? `Command: ${commandLabel}` : null}
                        {commandLabel && liveDelta != null ? ` | live delta ${liveDelta}` : null}
                        {!commandLabel && lastMotionResult?.position_delta != null ? `Last move delta: ${lastMotionResult.position_delta}` : null}
                        {!commandLabel && lastMotionResult?.wait?.elapsed_ms != null ? ` | settle ${lastMotionResult.wait.elapsed_ms} ms` : null}
                        {!commandLabel && lastMotionResult?.home?.position_delta != null ? `Last home delta: ${lastMotionResult.home.position_delta}` : null}
                        {!commandLabel && lastMotionResult?.home?.elapsed_ms != null ? ` | home ${lastMotionResult.home.elapsed_ms} ms` : null}
                    </div>
                    {!commandLabel && prepPolicyNote && (
                        <div className="text-[10px] text-content-muted">
                            Prep policy: {prepPolicyNote}
                        </div>
                    )}
                    {!commandLabel && truthSummary && (
                        <div className="text-[10px] text-warning">
                            Truth: {truthEvidenceLevel ?? 'controller only'} — {truthSummary}
                        </div>
                    )}
                    {!commandLabel && lastMotionResult?.message && (
                        <div className="text-[10px] text-content-muted">
                            Result: {lastMotionResult.message}
                        </div>
                    )}
                    {!commandLabel && artifactBundle?.bundle_dir && (
                        <div className="text-[10px] text-success break-all">
                            Validation bundle: {artifactBundle.bundle_dir}
                            {artifactBundle.dry_run ? ' (dry-run)' : ''}
                            {artifactSnapshotCount ? ` | snapshots: ${artifactSnapshotCount}` : ''}
                        </div>
                    )}
                    {!commandLabel && artifactBundle?.operator_note && (
                        <div className="text-[10px] text-content-muted">
                            Operator note: {artifactBundle.operator_note}
                        </div>
                    )}
                </div>
            )}

            {(moveRelative.isError || moveAbsolute.isError || homeAxis.isError) && (
                <div className="text-[10px] text-error">
                    {getErrorMessage(moveRelative.error) || getErrorMessage(moveAbsolute.error) || getErrorMessage(homeAxis.error)}
                </div>
            )}

            {(negativeMoveBlocked || positiveMoveBlocked) && (
                <div className="text-[10px] text-warning">
                    {negativeMoveBlocked ? 'Negative travel readback condition observed; software block disabled.' : null}
                    {negativeMoveBlocked && positiveMoveBlocked ? ' ' : null}
                    {positiveMoveBlocked ? 'Positive travel readback condition observed; software block disabled.' : null}
                </div>
            )}

            {switchConflictObserved && (
                <div className="text-[10px] text-content-muted">
                    Raw L/R switch readback both active; displayed as telemetry only. No frontend motion block is applied.
                </div>
            )}

            {((leftActive === true && leftMasked) || (rightActive === true && rightMasked)) && (
                <div className="text-[10px] text-content-muted">
                    Raw switch active but OEM preset masks that direction on this axis.
                </div>
            )}

        </div>
    );
};

const CameraAxisQuickControls = ({ axis, label, enabled }: { axis: AxisName; label: string; enabled: boolean }) => {
    const moveRelative = useMoveRelative();
    const homeAxis = useHomeAxis();
    const [steps, setSteps] = useState(axis === 'z' ? 35 : axis === 'g' ? 30 : 60);
    const [commandStartPosition, setCommandStartPosition] = useState<number | null>(null);
    const [commandLabel, setCommandLabel] = useState<string | null>(null);
    const localMotionBusy = moveRelative.isPending || homeAxis.isPending;
    const { data, isError, error } = useAxisStatus(axis, enabled, localMotionBusy ? 750 : 2500);

    const position = data?.status?.position?.position;
    const speed = data?.status?.speed?.speed;
    const moving = typeof speed === 'number' ? speed !== 0 : false;
    const leftActive = data?.switch_activity?.left_active;
    const rightActive = data?.switch_activity?.right_active;
    const leftMasked = data?.preset?.disable_left === true;
    const rightMasked = data?.preset?.disable_right === true;
    const switchConflictObserved = leftActive === true && rightActive === true;
    const homeToZeroBlocked = false;
    const liveDelta =
        commandStartPosition != null && typeof position === 'number'
            ? position - commandStartPosition
            : null;

    const beginCommand = (labelText: string) => {
        setCommandLabel(labelText);
        setCommandStartPosition(typeof position === 'number' ? position : null);
    };

    return (
        <div className="rounded-lg border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-2 space-y-2 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
            <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-content">{label}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${moving || moveRelative.isPending || homeAxis.isPending ? 'bg-white/10 text-content border-white/15' : 'bg-white/5 text-content-muted border-white/10'}`}>
                    {moving || moveRelative.isPending || homeAxis.isPending ? 'MOVE' : 'IDLE'}
                </span>
            </div>
            <div className="flex items-center justify-between text-[10px] font-mono text-content-muted">
                <span>P {position ?? 'n/a'}</span>
                <span>Δ {liveDelta ?? '--'}</span>
            </div>
            <div className="flex items-center gap-1">
                <input
                    type="number"
                    value={steps}
                    onChange={(e) => setSteps(Number(e.target.value))}
                    className="w-14 bg-surface border border-white/10 rounded px-2 py-1 text-[11px] text-content font-mono"
                />
                <button
                    onClick={() => {
                        beginCommand(`REL ${-Math.abs(steps)}`);
                        moveRelative.mutate(
                            { axis, steps: -Math.abs(steps) },
                            { onSettled: () => setCommandLabel(null) },
                        );
                    }}
                    disabled={!enabled || moveRelative.isPending}
                    className="px-2 py-1 rounded bg-white/10 hover:bg-white/15 text-content text-[11px] border border-white/10 disabled:opacity-30 transition-colors"
                >
                    ◄
                </button>
                <button
                    onClick={() => {
                        beginCommand(`REL ${Math.abs(steps)}`);
                        moveRelative.mutate(
                            { axis, steps: Math.abs(steps) },
                            { onSettled: () => setCommandLabel(null) },
                        );
                    }}
                    disabled={!enabled || moveRelative.isPending}
                    className="px-2 py-1 rounded bg-white/10 hover:bg-white/15 text-content text-[11px] border border-white/10 disabled:opacity-30 transition-colors"
                >
                    ►
                </button>
                <button
                    onClick={() => {
                        beginCommand('HOME → 0');
                        homeAxis.mutate(
                            { axis },
                            { onSettled: () => setCommandLabel(null) },
                        );
                    }}
                    disabled={!enabled || homeAxis.isPending || homeToZeroBlocked}
                    title="Return this axis to controller coordinate 0; does not run switch-search homing. Raw switch readback is displayed but does not software-block this command."
                    className="ml-auto px-2 py-1 rounded bg-white/10 hover:bg-white/15 text-content text-[11px] border border-white/10 disabled:opacity-30 transition-colors"
                >
                    Home → 0
                </button>
            </div>
            {(leftActive === true || rightActive === true) && (
                <div className={`text-[10px] ${switchConflictObserved ? 'text-content-muted' : 'text-content-muted'} font-mono`}>
                    {switchConflictObserved
                        ? 'L/R switch readback both active (telemetry only)'
                        : `${leftActive === true ? (leftMasked ? 'L sw(masked) ' : 'L sw ') : ''}${rightActive === true ? (rightMasked ? 'R sw(masked)' : 'R sw') : ''}`.trim()}
                </div>
            )}
            {commandLabel && (
                <div className="text-[10px] text-content-muted font-mono">
                    {commandLabel}
                </div>
            )}
            {(moveRelative.isError || homeAxis.isError || isError) && (
                <div className="text-[10px] text-error">
                    {getErrorMessage(moveRelative.error) || getErrorMessage(homeAxis.error) || getErrorMessage(error)}
                </div>
            )}
        </div>
    );
};

type CameraHoldJogCommand = {
    axis: 'x' | 'y' | 'z';
    steps: number;
    label: string;
};

const CameraHoldJogPad = ({ enabled }: { enabled: boolean }) => {
    const moveRelative = useMoveRelative();
    const [holdCommand, setHoldCommand] = useState<CameraHoldJogCommand | null>(null);
    const [lastAction, setLastAction] = useState<string | null>(null);
    const statusPollIntervalMs = holdCommand || moveRelative.isPending ? 500 : 2500;
    const axisBatchStatus = useAxisStatusBatch([...CAMERA_HOLD_JOG_AXES], enabled, statusPollIntervalMs);
    const axisRows = axisBatchStatus.data?.rows ?? {};

    const axisDataMap: Record<'x' | 'y' | 'z', AxisStatus | undefined> = {
        x: axisRows.x,
        y: axisRows.y,
        z: axisRows.z,
    };
    const xAxis = axisDataMap.x;
    const yAxis = axisDataMap.y;
    const zAxis = axisDataMap.z;

    const stopHold = () => setHoldCommand(null);

    const isBlocked = (command: CameraHoldJogCommand) =>
        getAxisDirectionState(axisDataMap[command.axis], command.steps).blocked;

    const issueJog = (command: CameraHoldJogCommand) => {
        moveRelative.mutate(
            {
                axis: command.axis,
                steps: command.steps,
                wait_timeout_s: CAMERA_HOLD_JOG_WAIT_TIMEOUT_S,
                reuse_prepared: false,
            },
            {
                onSuccess: (data) => {
                    const delta = typeof data?.position_delta === 'number' ? data.position_delta : command.steps;
                    setLastAction(`${command.label} | Δ ${delta}`);
                },
                onError: (error) => {
                    setLastAction(getErrorMessage(error) ?? `${command.label} failed`);
                    setHoldCommand(null);
                },
            },
        );
    };

    const startHold = (command: CameraHoldJogCommand) => (event: ReactPointerEvent<HTMLButtonElement>) => {
        event.preventDefault();
        if (!enabled || moveRelative.isPending || isBlocked(command)) {
            return;
        }
        try {
            event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
            // Pointer capture is not required for the hold loop.
        }
        setHoldCommand(command);
        setLastAction(`${command.label} | hold to repeat`);
        issueJog(command);
    };

    useEffect(() => {
        if (!holdCommand) {
            return;
        }
        const handleStop = () => setHoldCommand(null);
        const handleVisibility = () => {
            if (document.hidden) {
                setHoldCommand(null);
            }
        };
        window.addEventListener('pointerup', handleStop);
        window.addEventListener('pointercancel', handleStop);
        window.addEventListener('blur', handleStop);
        document.addEventListener('visibilitychange', handleVisibility);
        return () => {
            window.removeEventListener('pointerup', handleStop);
            window.removeEventListener('pointercancel', handleStop);
            window.removeEventListener('blur', handleStop);
            document.removeEventListener('visibilitychange', handleVisibility);
        };
    }, [holdCommand]);

    useEffect(() => {
        if (!holdCommand || !enabled || moveRelative.isPending) {
            return;
        }
        if (isBlocked(holdCommand)) {
            setLastAction(`${holdCommand.label} | limit reached`);
            setHoldCommand(null);
            return;
        }
        const timer = window.setTimeout(() => issueJog(holdCommand), CAMERA_HOLD_JOG_REPEAT_DELAY_MS);
        return () => window.clearTimeout(timer);
    }, [enabled, holdCommand, moveRelative.isPending, axisBatchStatus.data]);

    const xNegative = getAxisDirectionState(xAxis, -1);
    const xPositive = getAxisDirectionState(xAxis, 1);
    const yNegative = getAxisDirectionState(yAxis, -1);
    const yPositive = getAxisDirectionState(yAxis, 1);
    const zNegative = getAxisDirectionState(zAxis, -1);
    const zPositive = getAxisDirectionState(zAxis, 1);

    const warnings = [
        xNegative.blocked ? 'X- blocked by active left limit.' : null,
        xPositive.blocked ? 'X+ blocked by active right limit.' : null,
        yNegative.blocked ? 'Y- blocked by active left limit.' : null,
        yPositive.blocked ? 'Y+ blocked by active right limit.' : null,
        zNegative.blocked ? 'Z- blocked by active left limit.' : null,
        zPositive.blocked ? 'Z+ blocked by active right limit.' : null,
    ].filter(Boolean);

    const conflicts = [
        xNegative.conflictingSwitches ? 'X reports both limit switches active.' : null,
        yNegative.conflictingSwitches ? 'Y reports both limit switches active.' : null,
        zNegative.conflictingSwitches ? 'Z reports both limit switches active.' : null,
    ].filter(Boolean);

    const renderButton = (
        command: CameraHoldJogCommand,
        text: string,
        helper: string,
        blocked: boolean,
        active: boolean,
    ) => (
        <button
            type="button"
            onPointerDown={startHold(command)}
            onPointerUp={stopHold}
            onPointerCancel={stopHold}
            onContextMenu={(event) => event.preventDefault()}
            disabled={!enabled || blocked}
            className={`touch-none select-none rounded-xl border px-2 py-2 text-center transition-colors ${
                active
                    ? 'border-accent/50 bg-accent/20 text-accent'
                    : blocked
                        ? 'border-error/40 bg-error/10 text-error'
                        : 'border-white/10 bg-white/10 text-content hover:bg-white/15'
            } disabled:opacity-50`}
        >
            <div className="text-lg leading-none">{text}</div>
            <div className="mt-1 text-[10px] font-mono">{helper}</div>
        </button>
    );

    const axisStatusCell = (label: string, value: number | null | undefined, left: boolean | undefined, right: boolean | undefined) => (
        <div className="rounded-lg border border-white/10 bg-white/5 px-2 py-2">
            <div className="text-[9px] uppercase tracking-[0.12em] text-content-muted">{label}</div>
            <div className="mt-1 text-[11px] font-mono text-content">{value ?? 'n/a'}</div>
            <div className="mt-1 text-[9px] font-mono text-content-muted">
                L {left === true ? '1' : left === false ? '0' : '?'} / R {right === true ? '1' : right === false ? '0' : '?'}
            </div>
        </div>
    );

    return (
        <div className="rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3 space-y-3 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
            <div className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-content">Hold To Jog</div>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full border ${
                        holdCommand ? 'bg-accent/20 text-accent border-accent/30' : moveRelative.isPending ? 'bg-white/10 text-content border-white/15' : 'bg-white/5 text-content-muted border-white/10'
                    }`}>
                        {holdCommand ? 'HOLD' : moveRelative.isPending ? 'STEP' : 'READY'}
                    </span>
                </div>
                <div className="text-[10px] text-content-muted">
                    Hold an arrow to repeat guarded jog moves while you watch the live feed. Conservative jog profile: speed {CAMERA_HOLD_JOG_PROFILE.speed}, acc {CAMERA_HOLD_JOG_PROFILE.acc}.
                </div>
            </div>

            <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-3 items-start">
                <div className="grid grid-cols-3 gap-2 items-center justify-items-center">
                    <div />
                    {renderButton(
                        { axis: 'y', steps: -CAMERA_HOLD_JOG_PROFILE.xy_step, label: 'Y-' },
                        '▲',
                        'Y-',
                        yNegative.blocked,
                        holdCommand?.axis === 'y' && holdCommand?.steps < 0,
                    )}
                    <div />
                    {renderButton(
                        { axis: 'x', steps: -CAMERA_HOLD_JOG_PROFILE.xy_step, label: 'X-' },
                        '◀',
                        'X-',
                        xNegative.blocked,
                        holdCommand?.axis === 'x' && holdCommand?.steps < 0,
                    )}
                    <div className="w-full rounded-xl border border-white/10 bg-white/5 px-2 py-2 text-center">
                        <div className="text-[9px] uppercase tracking-[0.12em] text-content-muted">XY</div>
                        <div className="mt-1 text-[11px] font-mono text-content">{CAMERA_HOLD_JOG_PROFILE.xy_step} st</div>
                    </div>
                    {renderButton(
                        { axis: 'x', steps: CAMERA_HOLD_JOG_PROFILE.xy_step, label: 'X+' },
                        '▶',
                        'X+',
                        xPositive.blocked,
                        holdCommand?.axis === 'x' && holdCommand?.steps > 0,
                    )}
                    <div />
                    {renderButton(
                        { axis: 'y', steps: CAMERA_HOLD_JOG_PROFILE.xy_step, label: 'Y+' },
                        '▼',
                        'Y+',
                        yPositive.blocked,
                        holdCommand?.axis === 'y' && holdCommand?.steps > 0,
                    )}
                    <div />
                </div>

                <div className="space-y-2">
                    {renderButton(
                        { axis: 'z', steps: CAMERA_HOLD_JOG_PROFILE.z_step, label: 'Z+' },
                        '▲',
                        'Z+',
                        zPositive.blocked,
                        holdCommand?.axis === 'z' && holdCommand?.steps > 0,
                    )}
                    <div className="rounded-xl border border-white/10 bg-white/5 px-2 py-2 text-center">
                        <div className="text-[9px] uppercase tracking-[0.12em] text-content-muted">Z</div>
                        <div className="mt-1 text-[11px] font-mono text-content">{CAMERA_HOLD_JOG_PROFILE.z_step} st</div>
                        <div className="mt-1 text-[9px] text-content-muted">pipettor</div>
                    </div>
                    {renderButton(
                        { axis: 'z', steps: -CAMERA_HOLD_JOG_PROFILE.z_step, label: 'Z-' },
                        '▼',
                        'Z-',
                        zNegative.blocked,
                        holdCommand?.axis === 'z' && holdCommand?.steps < 0,
                    )}
                </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
                {axisStatusCell('X', axisRows.x?.status?.position?.position, xNegative.leftActive, xPositive.rightActive)}
                {axisStatusCell('Y', axisRows.y?.status?.position?.position, yNegative.leftActive, yPositive.rightActive)}
                {axisStatusCell('Z', axisRows.z?.status?.position?.position, zNegative.leftActive, zPositive.rightActive)}
            </div>

            {lastAction && (
                <div className="text-[10px] font-mono text-content-muted">
                    {lastAction}
                </div>
            )}

            {warnings.length > 0 && (
                <div className="text-[10px] text-warning space-y-1">
                    {warnings.map((warning) => (
                        <div key={warning}>{warning}</div>
                    ))}
                </div>
            )}

            {conflicts.length > 0 && (
                <div className="text-[10px] text-content-muted space-y-1">
                    {conflicts.map((warning) => (
                        <div key={warning}>{warning}</div>
                    ))}
                </div>
            )}

            {(moveRelative.isError || axisBatchStatus.isError) && (
                <div className="text-[10px] text-error">
                    {getErrorMessage(moveRelative.error) || getErrorMessage(axisBatchStatus.error)}
                </div>
            )}
        </div>
    );
};

const CameraSettingControl = ({
    control,
    disabled,
    pending,
    onApply,
}: {
    control: CameraControlRow;
    disabled: boolean;
    pending: boolean;
    onApply: (control: CameraControlRow, value: number) => void;
}) => {
    const normalizedName = normalizeCameraControlName(control.name);
    const currentValue =
        typeof control.get?.value === 'number'
            ? control.get.value
            : typeof control.default === 'number'
                ? control.default
                : control.minimum;
    const [draftValue, setDraftValue] = useState(currentValue ?? 0);

    useEffect(() => {
        setDraftValue(currentValue ?? 0);
    }, [control.cid, currentValue]);

    const controlOptions = CAMERA_CONTROL_OPTIONS[normalizedName];
    const controlLabel = CAMERA_CONTROL_LABELS[normalizedName] ?? control.name;
    const currentLabel = controlOptions?.find((option) => option.value === currentValue)?.label ?? currentValue;

    return (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-2 space-y-2">
            <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                    <div className="text-[10px] font-semibold text-content truncate">{controlLabel}</div>
                    <div className="text-[9px] font-mono text-content-muted truncate">{control.name}</div>
                </div>
                <div className="text-[10px] font-mono text-content-muted text-right">
                    {currentLabel ?? 'n/a'}
                </div>
            </div>

            {controlOptions ? (
                <select
                    value={String(draftValue)}
                    onChange={(e) => setDraftValue(Number(e.target.value))}
                    disabled={disabled || pending}
                    className="w-full bg-white/10 border border-white/10 rounded px-2 py-1.5 text-[11px] text-content font-mono disabled:opacity-40"
                >
                    {controlOptions
                        .filter((option) => option.value >= control.minimum && option.value <= control.maximum)
                        .map((option) => (
                            <option key={option.value} value={option.value}>
                                {option.label}
                            </option>
                        ))}
                </select>
            ) : (
                <div className="space-y-2">
                    <input
                        type="range"
                        min={control.minimum}
                        max={control.maximum}
                        step={Math.max(1, control.step || 1)}
                        value={draftValue}
                        onChange={(e) => setDraftValue(Number(e.target.value))}
                        disabled={disabled || pending}
                        className="w-full accent-white disabled:opacity-40"
                    />
                    <input
                        type="number"
                        min={control.minimum}
                        max={control.maximum}
                        step={Math.max(1, control.step || 1)}
                        value={draftValue}
                        onChange={(e) => setDraftValue(Number(e.target.value))}
                        disabled={disabled || pending}
                        className="w-full bg-white/10 border border-white/10 rounded px-2 py-1 text-[11px] text-content font-mono disabled:opacity-40"
                    />
                </div>
            )}

            <div className="flex items-center gap-2">
                <button
                    onClick={() => onApply(control, clampCameraControlValue(control, draftValue))}
                    disabled={disabled || pending}
                    className="flex-1 px-2 py-1.5 rounded bg-white/10 hover:bg-white/15 text-content text-[10px] border border-white/10 disabled:opacity-30 transition-colors"
                >
                    {pending ? 'Applying' : 'Set'}
                </button>
                <button
                    onClick={() => {
                        const defaultValue = clampCameraControlValue(control, Number(control.default) || 0);
                        setDraftValue(defaultValue);
                        onApply(control, defaultValue);
                    }}
                    disabled={disabled || pending}
                    className="px-2 py-1.5 rounded bg-white/10 hover:bg-white/15 text-content text-[10px] border border-white/10 disabled:opacity-30 transition-colors"
                >
                    Default
                </button>
            </div>

            <div className="text-[9px] font-mono text-content-muted">
                {control.minimum}..{control.maximum} step {Math.max(1, control.step || 1)}
            </div>
        </div>
    );
};

const ThermalControlCard = ({ bank, label, enabled }: { bank: ThermalBankName; label: string; enabled: boolean }) => {
    const setTemp = useSetThermalTemp();
    const setFan = useSetThermalFan();
    const setPwm = useSetThermalPwm();
    const setRates = useSetThermalRates();
    const [temp, setTempState] = useState(37);
    const [fanSpeed, setFanSpeed] = useState(128);
    const [pwm, setPwmState] = useState(35);
    const [coolRate, setCoolRate] = useState(-0.4);
    const [heatRate, setHeatRate] = useState(0.4);
    const supportsPerBankTuning = bank !== 'pedestal';
    const writeError =
        getErrorMessage(setTemp.error) ||
        getErrorMessage(setFan.error) ||
        getErrorMessage(setPwm.error) ||
        getErrorMessage(setRates.error);

    return (
        <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-3">
            <div className="space-y-1">
                <div className="text-xs text-accent font-semibold">{label}</div>
                <div className="text-[10px] text-content-muted">
                    {supportsPerBankTuning
                        ? 'Setpoint, shared fan, bank PWM, and rate tuning are available from the upstream runtime.'
                        : 'Pedestal currently supports setpoint only; PWM and rate controls are only defined for nest and lid.'}
                </div>
            </div>
            <div className="flex gap-2 items-center">
                <input
                    type="number"
                    step="0.1"
                    value={temp}
                    onChange={(e) => setTempState(Number(e.target.value))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => setTemp.mutate({ bank, target_temp_c: temp })}
                    disabled={!enabled || setTemp.isPending}
                    className="px-4 py-1.5 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                >
                    Set Target
                </button>
            </div>
            <div className="flex gap-2 items-center">
                <input
                    type="number"
                    min={0}
                    max={255}
                    step={1}
                    value={fanSpeed}
                    onChange={(e) => setFanSpeed(Math.max(0, Math.min(255, Number(e.target.value))))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => setFan.mutate({ speed: fanSpeed })}
                    disabled={!enabled || setFan.isPending}
                    className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                >
                    Set Fan
                </button>
            </div>
            {supportsPerBankTuning && (
                <>
                    <div className="flex gap-2 items-center">
                        <input
                            type="number"
                            min={0}
                            max={100}
                            step={1}
                            value={pwm}
                            onChange={(e) => setPwmState(Math.max(0, Math.min(100, Number(e.target.value))))}
                            className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                        />
                        <button
                            onClick={() => setPwm.mutate({ bank, pwm })}
                            disabled={!enabled || setPwm.isPending}
                            className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                        >
                            Set PWM
                        </button>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 items-center">
                        <input
                            type="number"
                            min={-2}
                            max={0}
                            step={0.1}
                            value={coolRate}
                            onChange={(e) => setCoolRate(Math.max(-2, Math.min(0, Number(e.target.value))))}
                            className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm"
                            aria-label={`${label} cool rate`}
                        />
                        <input
                            type="number"
                            min={0}
                            max={2}
                            step={0.1}
                            value={heatRate}
                            onChange={(e) => setHeatRate(Math.max(0, Math.min(2, Number(e.target.value))))}
                            className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm"
                            aria-label={`${label} heat rate`}
                        />
                        <button
                            onClick={() => setRates.mutate({ bank, cool_rate_c_s: coolRate, heat_rate_c_s: heatRate })}
                            disabled={!enabled || setRates.isPending}
                            className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                        >
                            Set Rates
                        </button>
                    </div>
                    <div className="text-[10px] text-content-muted">Cool rate in C/s (negative), heat rate in C/s (positive).</div>
                </>
            )}
            {writeError && <div className="text-[10px] text-error">{writeError}</div>}
        </div>
    );
};

const ChillerControlCard = ({ bank, label, enabled }: { bank: ChillerBankName; label: string; enabled: boolean }) => {
    const setTemp = useSetChillerTemp();
    const setFan = useSetChillerFan();
    const setPwm = useSetChillerPwm();
    const setRates = useSetChillerRates();
    const [temp, setTempState] = useState(4);
    const [fanSpeed, setFanSpeed] = useState(128);
    const [pwm, setPwmState] = useState(35);
    const [coolRate, setCoolRate] = useState(-0.4);
    const [heatRate, setHeatRate] = useState(0.4);
    const writeError =
        getErrorMessage(setTemp.error) ||
        getErrorMessage(setFan.error) ||
        getErrorMessage(setPwm.error) ||
        getErrorMessage(setRates.error);

    return (
        <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-3">
            <div className="space-y-1">
                <div className="text-xs text-accent font-semibold">{label}</div>
                <div className="text-[10px] text-content-muted">Each bank supports setpoint, fan, PWM, and rate tuning in the upstream runtime.</div>
            </div>
            <div className="flex gap-2 items-center">
                <input
                    type="number"
                    step="0.1"
                    value={temp}
                    onChange={(e) => setTempState(Number(e.target.value))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => setTemp.mutate({ bank, target_temp_c: temp })}
                    disabled={!enabled || setTemp.isPending}
                    className="px-4 py-1.5 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                >
                    Set Target
                </button>
            </div>
            <div className="flex gap-2 items-center">
                <input
                    type="number"
                    min={0}
                    max={255}
                    step={1}
                    value={fanSpeed}
                    onChange={(e) => setFanSpeed(Math.max(0, Math.min(255, Number(e.target.value))))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => setFan.mutate({ bank, speed: fanSpeed })}
                    disabled={!enabled || setFan.isPending}
                    className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                >
                    Set Fan
                </button>
            </div>
            <div className="flex gap-2 items-center">
                <input
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    value={pwm}
                    onChange={(e) => setPwmState(Math.max(0, Math.min(100, Number(e.target.value))))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-28"
                />
                <button
                    onClick={() => setPwm.mutate({ bank, pwm })}
                    disabled={!enabled || setPwm.isPending}
                    className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                >
                    Set PWM
                </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 items-center">
                <input
                    type="number"
                    min={-2}
                    max={0}
                    step={0.1}
                    value={coolRate}
                    onChange={(e) => setCoolRate(Math.max(-2, Math.min(0, Number(e.target.value))))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm"
                    aria-label={`${label} cool rate`}
                />
                <input
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={heatRate}
                    onChange={(e) => setHeatRate(Math.max(0, Math.min(2, Number(e.target.value))))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm"
                    aria-label={`${label} heat rate`}
                />
                <button
                    onClick={() => setRates.mutate({ bank, cool_rate_c_s: coolRate, heat_rate_c_s: heatRate })}
                    disabled={!enabled || setRates.isPending}
                    className="px-4 py-1.5 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10"
                >
                    Set Rates
                </button>
            </div>
            <div className="text-[10px] text-content-muted">Cool rate in C/s (negative), heat rate in C/s (positive).</div>
            {writeError && <div className="text-[10px] text-error">{writeError}</div>}
        </div>
    );
};

export const BioXpCockpit = () => {
    const [activeTab, setActiveTab] = useState<'connection' | 'operator' | 'service' | 'manual' | 'controls' | 'camera'>('controls');
    const [cameraDevice, setCameraDevice] = useState('/dev/video0');
    const [snapshot, setSnapshot] = useState<string | null>(null);
    const [pollCamera, setPollCamera] = useState(false);
    const [streamMode, setStreamMode] = useState<(typeof CAMERA_STREAM_MODES)[number]['key']>('1280x720');
    const [streamFps, setStreamFps] = useState(30);
    const [streamNonce, setStreamNonce] = useState(0);
    const [streamReady, setStreamReady] = useState(false);
    const [streamError, setStreamError] = useState<string | null>(null);
    const [latestCameraAction, setLatestCameraAction] = useState<{ action: string; data: any } | null>(null);
    const [latestMotionInfraAction, setLatestMotionInfraAction] = useState<{ action: string; data: any } | null>(null);
    const [ledRgbState, setLedRgbState] = useState({ r: 32, g: 128, b: 255 });
    const [ledPctState, setLedPctState] = useState(35);
    const [lastHealthyAt, setLastHealthyAt] = useState<number | null>(null);
    const [viewerFullscreen, setViewerFullscreen] = useState(false);
    const [pendingCameraControlCid, setPendingCameraControlCid] = useState<number | null>(null);
    const [liquidVolumeUl, setLiquidVolumeUl] = useState(10);
    const [liquidCycles, setLiquidCycles] = useState(3);
    const [liquidPressureProfile, setLiquidPressureProfile] = useState('1R');
    const [liquidSource, setLiquidSource] = useState('reagent_rack:A1');
    const [liquidDestination, setLiquidDestination] = useState('reaction_plate:A1');
    const [latestLiquidAction, setLatestLiquidAction] = useState<{ action: string; data: any } | null>(null);
    const [latestOemAction, setLatestOemAction] = useState<{ action: string; data: any } | null>(null);
    const [latestReferenceAction, setLatestReferenceAction] = useState<{ action: string; data: any } | null>(null);
    const [latestVisionAction, setLatestVisionAction] = useState<{ action: string; data: any } | null>(null);
    const [showCommissioningControls, setShowCommissioningControls] = useState(false);
    const [headLiftSteps, setHeadLiftSteps] = useState(500);
    const [microMoveAxis, setMicroMoveAxis] = useState<AxisName>('x');
    const [microMoveSteps, setMicroMoveSteps] = useState(100);
    const [latestOperationReport, setLatestOperationReport] = useState<BioXpOperationReport | null>(null);
    const cameraViewerRef = useRef<HTMLDivElement | null>(null);

    const hardwareMutationCount = useIsMutating({
        predicate: (mutation) => hasMutationKeyPrefix(mutation.options.mutationKey, ['bioxp', 'hardware']),
    });
    const motionMutationCount = useIsMutating({
        predicate: (mutation) => hasMutationKeyPrefix(mutation.options.mutationKey, ['bioxp', 'hardware', 'motion']),
    });
    const hardwareBusy = hardwareMutationCount > 0;
    const motionBusy = motionMutationCount > 0;
    const bioXpInterlink = useBioXpInterlinkState(false, activeTab === 'connection' ? 5000 : false);
    const interlinkActive = Boolean(bioXpInterlink.data?.active);
    const interlinkConfigured = Boolean(bioXpInterlink.data?.configured);
    const interlinkUrl = bioXpInterlink.data?.robot_api_url ?? bioXpInterlink.data?.recommended_url ?? 'http://robot:8123';
    const { data: status, isLoading: statusLoading, isError: statusIsError, error: statusError } = useBioXpStatus(interlinkActive, false);

    const { data: runtimeStatus, isLoading: runtimeLoading, isError: runtimeStatusIsError, error: runtimeStatusError } = useRuntimeStatus();
    const motionPowerEnable = useMotionPowerEnable();
    const motionPowerDiag = useMotionPowerDiag();
    const motionArmStrictStartup = useMotionArmStrictStartup();
    const prepareInterlock = usePrepareInterlock();
    const clearLock = useClearLock();

    const hardwareReachable = interlinkActive && !!status && !statusIsError && status.hardware_connected;
    const linkageConfigured = interlinkConfigured || Boolean(status?.linkage_configured || runtimeStatus?.linkage_configured);
    const runtimeSummary = deriveRuntimeStatusSummary({
        linkageConfigured,
        runtimeLoading,
        runtimeStatus: runtimeStatus ?? (runtimeStatusIsError
            ? {
                linkage_configured: linkageConfigured,
                linked_runtime_reachable: false,
                hardware_connected: false,
                admin_control_available: false,
                maintenance_mode: 'robot-local',
                detail: getErrorMessage(runtimeStatusError) ?? 'Runtime status probe failed.',
            }
            : null),
    });
    const runtimeStatusHelp = runtimeSummary.detail;
    const hasRecentHardwareContact =
        lastHealthyAt != null &&
        (Date.now() - lastHealthyAt) < CONNECTION_STICKY_WINDOW_MS;
    const connectionPollingEnabled = activeTab === 'connection' && !hardwareBusy;
    const controlsPollingEnabled = false;
    const cameraDiscoveryEnabled = hardwareReachable && activeTab === 'camera' && !pollCamera && !motionBusy;
    const capabilities = useBioXpCapabilities(interlinkActive);
    const operationCapabilities = useBioXpOperationCapabilities(interlinkActive);
    const operationReadiness = useBioXpOperationReadiness(interlinkActive && activeTab === 'service' && !hardwareBusy, hardwareBusy ? false : 30000);
    const prepareSafeOperation = usePrepareSafeOperation();
    const headClearLockOperation = useHeadClearLockOperation();
    const headLiftIncrementOperation = useHeadLiftIncrementOperation();
    const microMoveProofOperation = useMicroMoveProofOperation();
    const latchLockOperation = useLatchLockOperation();
    const latchUnlockOperation = useLatchUnlockOperation();
    const emergencyStopOperation = useEmergencyStopOperation();
    const motionReferenceStatus = useMotionReferenceStatus(controlsPollingEnabled, ['x', 'y', 'z', 'g', 'door'], motionBusy ? false : 5000);
    const markMotionReferenced = useMarkMotionReferenced();
    const markMotionDesynced = useMarkMotionDesynced();
    const oemStartupLatest = useOemStartupLatest(controlsPollingEnabled, motionBusy ? false : 5000);
    const oemRuntimeStatus = useOemRuntimeStatus(controlsPollingEnabled, motionBusy ? false : 5000);
    const oemRuntimeState = useOemRuntimeState(controlsPollingEnabled, motionBusy ? false : 5000);
    const oemStartupRequest = useOemStartupRequest();
    const oemStartupDoorEvent = useOemStartupDoorEvent();
    const oemRuntimeRecover = useOemRuntimeRecover();
    const oemRuntimeEmergencyStop = useOemRuntimeEmergencyStop();
    const oemInitializeSystem = useOemRuntimeCommand('initializeSystem');
    const oemPrepareToRunJobReadiness = usePrepareToRunJobReadiness();
    const oemUnlockProcess = useOemRuntimeCommand('unlockProcess');
    const motionRangeStatus = useMotionRangeStatus(controlsPollingEnabled, motionBusy ? false : 5000);
    const liquidStatus = useLiquidStatus(controlsPollingEnabled, motionBusy ? false : 5000);
    const liquidInit = useLiquidInit();
    const liquidTip = useLiquidTip();
    const liquidAspirate = useLiquidAspirate();
    const liquidDispense = useLiquidDispense();
    const liquidMix = useLiquidMix();
    const visionInspect = useVisionInspect();
    const visionBarcodeRead = useVisionBarcodeRead();
    const motionPowerStatus = useMotionPowerStatus(controlsPollingEnabled, motionBusy ? false : 8000);

    const latchStatus = useLatchStatus(connectionPollingEnabled);
    const latchLock = useLatchLock();
    const latchUnlock = useLatchUnlock();
    const ledRgb = useLedRgb();
    const ledPct = useLedPct();
    const ledOff = useLedOff();

    const thermalSnapshot = useThermalSnapshot(controlsPollingEnabled);
    const thermalBaseline = useThermalBaseline();
    const thermalFastProfile = useThermalFastProfile();
    const thermalHardReset = useThermalHardReset();

    const chillerSnapshot = useChillerSnapshot(controlsPollingEnabled);
    const chillerBaseline = useChillerBaseline();
    const chillerHardReset = useChillerHardReset();

    const cameraDevices = useCameraDevices(cameraDiscoveryEnabled);
    const cameraControls = useCameraControls(cameraDevice, cameraDiscoveryEnabled && activeTab === 'camera');
    const cameraSetControl = useSetCameraControl();
    const cameraReset = useCameraReset();
    const cameraSnapshot = useCameraSnapshot();
    const cameraStop = useCameraStop();
    const cameraStreamHealth = useCameraStreamHealth();
    const cameraStreamState = useCameraStreamState(hardwareReachable && activeTab === 'camera', pollCamera ? 1500 : 3000);
    const cameraAutoRecover = useCameraAutoRecover();
    const selectedStreamMode =
        CAMERA_STREAM_MODES.find((mode) => mode.key === streamMode) ?? CAMERA_STREAM_MODES[1];

    useEffect(() => {
        if (hardwareReachable) {
            setLastHealthyAt(Date.now());
        }
    }, [hardwareReachable]);

    useEffect(() => {
        if (cameraDevices.data?.preferred_device && cameraDevice !== cameraDevices.data.preferred_device) {
            setCameraDevice(cameraDevices.data.preferred_device);
            return;
        }
        if (!cameraDevice && cameraDevices.data?.rows?.[0]?.device) {
            setCameraDevice(cameraDevices.data.rows[0].device);
        }
    }, [cameraDevice, cameraDevices.data]);

    useEffect(() => {
        if (streamFps > selectedStreamMode.maxFps) {
            setStreamFps(selectedStreamMode.maxFps);
        }
    }, [selectedStreamMode, streamFps]);

    useEffect(() => {
        const handleFullscreenChange = () => {
            setViewerFullscreen(document.fullscreenElement === cameraViewerRef.current);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    const controlPlaneReachable = interlinkActive && linkageConfigured && operationCapabilities.data?.robot_openapi_reachable !== false;
    const isConnected = interlinkActive && (hardwareReachable || controlPlaneReachable || (!!status?.linkage_configured && hasRecentHardwareContact));
    const isRecovering = interlinkActive && !hardwareReachable && (controlPlaneReachable || (!!status?.linkage_configured && hasRecentHardwareContact));
    const isDegraded = !!status && !statusIsError && (status.status === 'degraded' || isRecovering);
    const linkageHelp =
        status?.status === 'not_configured'
            ? 'No linkage is configured yet. Use the recommended runtime URL below and press Connect.'
            : status?.proxy_error?.detail
                ? getErrorMessage(status.proxy_error.detail)
                : null;
    const latestCameraResult = latestCameraAction?.data ?? null;
    const latestHardwareAction = prepareInterlock.data ?? clearLock.data ?? null;
    const recordCameraAction = (action: string, data: any) => {
        setLatestCameraAction({ action, data });
    };
    const recordCameraError = (action: string, error: unknown) => {
        setLatestCameraAction({
            action,
            data: {
                ok: false,
                error: getErrorMessage(error) ?? `${action} failed`,
            },
        });
    };
    const latestCameraSummary = (() => {
        if (!latestCameraAction?.data) {
            return null;
        }
        const action = latestCameraAction.action.toUpperCase();
        const data = latestCameraAction.data;
        const parts = [action];
        if (typeof data.ok === 'boolean') {
            parts.push(data.ok ? 'OK' : 'FAIL');
        }
        if (data.busy === true) {
            parts.push('BUSY');
        }
        if (typeof data.frames === 'number') {
            parts.push(`frames ${data.frames}`);
        }
        if (typeof data.fps_last === 'number') {
            parts.push(`fps ${data.fps_last.toFixed(1)}`);
        }
        if (typeof data.rc === 'number') {
            parts.push(`rc ${data.rc}`);
        }
        if (typeof data.name === 'string' && data.name) {
            parts.push(data.name);
        }
        if (typeof data.set_value === 'number') {
            parts.push(`set ${data.set_value}`);
        }
        if (typeof data.readback === 'number') {
            parts.push(`read ${data.readback}`);
        }
        if (typeof data.device === 'string' && data.device) {
            parts.push(data.device);
        }
        if (typeof data.error === 'string' && data.error) {
            parts.push(data.error);
        }
        return parts.join(' | ');
    })();
    const recordMotionInfraAction = (action: string, data: any) => {
        setLatestMotionInfraAction({ action, data });
    };
    const recordMotionInfraError = (action: string, error: unknown) => {
        setLatestMotionInfraAction({
            action,
            data: {
                ok: false,
                error: getErrorMessage(error) ?? `${action} failed`,
            },
        });
    };
    const latestMotionInfraResult = latestMotionInfraAction?.data ?? null;
    const latestMotionInfraSummary = (() => {
        if (!latestMotionInfraAction?.data) {
            return null;
        }
        const action = latestMotionInfraAction.action.toUpperCase();
        const data = latestMotionInfraAction.data;
        const parts = [action];
        if (typeof data.ok === 'boolean') {
            parts.push(data.ok ? 'OK' : 'FAIL');
        }
        if (data.hardware_connected === true) {
            parts.push('HW');
        }
        if (typeof data.elapsed_ms === 'number') {
            parts.push(`${data.elapsed_ms}ms`);
        }
        if (data.rail_24v) {
            parts.push(`24V ${get24vStateLabel(data.rail_24v)}`);
        }
        if (typeof data.error === 'string' && data.error) {
            parts.push(data.error);
        }
        return parts.join(' | ');
    })();

    const parseLiquidLocation = (value: string) => {
        const [location_id, well_id] = value.split(':').map((part) => part.trim()).filter(Boolean);
        return { location_id: location_id || value.trim() || 'unknown', well_id: well_id || null };
    };

    const liquidCommandPayload = () => ({
        volume_ul: liquidVolumeUl,
        cycles: liquidCycles,
        pressure_profile: liquidPressureProfile,
        source: parseLiquidLocation(liquidSource),
        destination: parseLiquidLocation(liquidDestination),
        liquid_class: 'aqueous_default',
        metadata: { source: 'bms-cockpit' },
    });

    const recordLiquidAction = (action: string, data: any) => setLatestLiquidAction({ action, data });
    const recordLiquidError = (action: string, error: unknown) => recordLiquidAction(action, { ok: false, error: getErrorMessage(error) ?? `${action} failed` });
    const recordOemAction = (action: string, data: any) => setLatestOemAction({ action, data });
    const recordOemError = (action: string, error: unknown) => recordOemAction(action, { ok: false, error: getErrorMessage(error) ?? `${action} failed` });
    const recordReferenceAction = (action: string, data: any) => setLatestReferenceAction({ action, data });
    const recordReferenceError = (action: string, error: unknown) => recordReferenceAction(action, { ok: false, error: getErrorMessage(error) ?? `${action} failed` });
    const recordVisionAction = (action: string, data: any) => setLatestVisionAction({ action, data });
    const recordVisionError = (action: string, error: unknown) => recordVisionAction(action, { ok: false, error: getErrorMessage(error) ?? `${action} failed` });
    const operationPayload = () => ({
        operator_ack: true,
        operator: 'bms-cockpit',
        operator_note: 'BMS service operation',
    });
    const recordOperationReport = (report: BioXpOperationReport) => setLatestOperationReport(report);
    const recordOperationError = (operation: string, error: unknown) => setLatestOperationReport({
        schema_version: 'bioxp.bms_operation_report.v1',
        operation,
        risk: 'unknown',
        operator_ack: true,
        operator: 'bms-cockpit',
        truth_level: 'failed_before_controller_result',
        notes: [getErrorMessage(error) ?? `${operation} failed`],
    });

    const referenceRows = getReferenceRows(motionReferenceStatus.data);
    const liquidActionError =
        getErrorMessage(liquidStatus.error) ||
        getErrorMessage(liquidInit.error) ||
        getErrorMessage(liquidTip.error) ||
        getErrorMessage(liquidAspirate.error) ||
        getErrorMessage(liquidDispense.error) ||
        getErrorMessage(liquidMix.error);
    const referenceActionError =
        getErrorMessage(motionReferenceStatus.error) ||
        getErrorMessage(markMotionReferenced.error) ||
        getErrorMessage(markMotionDesynced.error);
    const visionActionError = getErrorMessage(visionInspect.error) || getErrorMessage(visionBarcodeRead.error);

    const cameraControlRows = Array.isArray(cameraControls.data?.rows)
        ? [...cameraControls.data.rows]
        : [];
    const cameraVisibleControls = cameraControlRows
        .filter((control) => isWritableCameraControl(control))
        .sort((left, right) => {
            const weightDelta = cameraControlSortWeight(left) - cameraControlSortWeight(right);
            if (weightDelta !== 0) {
                return weightDelta;
            }
            return normalizeCameraControlName(left.name).localeCompare(normalizeCameraControlName(right.name));
        })
        .slice(0, 10);
    const hasZoomControl = cameraControlRows.some((control) => normalizeCameraControlName(control.name).includes('zoom'));

    const toggleViewerFullscreen = async () => {
        if (!cameraViewerRef.current) {
            return;
        }
        try {
            if (document.fullscreenElement === cameraViewerRef.current) {
                await document.exitFullscreen();
            } else {
                await cameraViewerRef.current.requestFullscreen();
            }
        } catch (error) {
            recordCameraError('fullscreen', error);
        }
    };

    const captureFrame = () => {
        setStreamError(null);
        cameraSnapshot.mutate(
            { device: cameraDevice },
            {
                onSuccess: (data) => {
                    recordCameraAction('capture', data);
                    if (data.image_b64) {
                        setSnapshot(data.image_b64);
                    }
                },
                onError: (error) => recordCameraError('capture', error),
            }
        );
    };

    useEffect(() => {
        if (activeTab !== 'camera' && pollCamera) {
            cameraStop.mutate({ device: cameraDevice });
            setPollCamera(false);
        }
    }, [activeTab, pollCamera, cameraDevice]);

    useEffect(() => {
        if (!isConnected && pollCamera) {
            cameraStop.mutate({ device: cameraDevice });
            setPollCamera(false);
        }
    }, [isConnected, pollCamera, cameraDevice]);

    useEffect(() => {
        if (pollCamera) {
            setStreamNonce((prev) => prev + 1);
            setStreamError(null);
            setStreamReady(false);
        }
    }, [pollCamera, cameraDevice, streamFps]);

    useEffect(() => {
        if (activeTab === 'manual' && !showCommissioningControls) {
            setActiveTab('controls');
        }
    }, [activeTab, showCommissioningControls]);

    useEffect(() => {
        if (!pollCamera || streamReady) {
            return;
        }
        const timer = window.setTimeout(() => {
            const message = 'Live stream opened but no frame arrived. Stop the stream and retry or run Reset/Auto Recover.';
            setStreamError(message);
            recordCameraAction('stream', { ok: false, error: message, device: cameraDevice });
            setSnapshot(null);
            cameraStop.mutate({ device: cameraDevice });
            setPollCamera(false);
        }, 4000);
        return () => window.clearTimeout(timer);
    }, [pollCamera, streamReady, streamNonce, cameraDevice]);

    const streamUrl = pollCamera
        ? getCameraStreamUrl({
            device: cameraDevice,
            fps: streamFps,
            quality: 7,
            width: selectedStreamMode.width,
            height: selectedStreamMode.height,
            nonce: streamNonce,
        })
        : null;
    const cameraActionError =
        getErrorMessage(cameraSnapshot.error) ||
        getErrorMessage(cameraSetControl.error) ||
        getErrorMessage(cameraStop.error) ||
        getErrorMessage(cameraStreamHealth.error) ||
        getErrorMessage(cameraAutoRecover.error) ||
        getErrorMessage(cameraReset.error) ||
        streamError;
    const motionPowerApiError = getErrorMessage(motionPowerStatus.error);
    const motionPowerApiMissing = (motionPowerApiError ?? '').includes('404');
    const motionPowerEffectiveStatus: MotionPowerStatus | null = motionPowerApiMissing
        ? {
            hardware_connected: status?.hardware_connected,
            board_status: status?.board_status ?? null,
            deck_io_snapshot: status?.deck_io_snapshot ?? null,
            rail_24v: status?.deck_io_snapshot
                ? {
                    raw: status.deck_io_snapshot['0'] ?? null,
                    no24v:
                        status.deck_io_snapshot['0'] == null
                            ? null
                            : Number(status.deck_io_snapshot['0']) !== 0,
                }
                : null,
            motion_arm: null,
            latch_override: null,
        }
        : (motionPowerStatus.data ?? null);
    const motionPowerRail = motionPowerEffectiveStatus?.rail_24v ?? null;
    const motion24vState = get24vStateLabel(motionPowerRail);
    const motionIoSnapshot = motionPowerEffectiveStatus?.deck_io_snapshot ?? null;
    const motionActionError =
        getErrorMessage(motionPowerEnable.error) ||
        getErrorMessage(motionPowerDiag.error) ||
        getErrorMessage(motionArmStrictStartup.error) ||
        (!motionPowerApiMissing ? motionPowerApiError : null) ||
        getErrorMessage(prepareInterlock.error) ||
        getErrorMessage(clearLock.error);
    const applyCameraControl = (control: CameraControlRow, value: number) => {
        const nextValue = clampCameraControlValue(control, value);
        setPendingCameraControlCid(control.cid);
        cameraSetControl.mutate(
            { device: cameraDevice, cid: control.cid, value: nextValue },
            {
                onSuccess: (data) => {
                    recordCameraAction('control', { ...data, name: control.name });
                    setPendingCameraControlCid(null);
                },
                onError: (error) => {
                    recordCameraError('control', error);
                    setPendingCameraControlCid(null);
                },
            },
        );
    };

    const operationMutationBusy =
        prepareSafeOperation.isPending ||
        headClearLockOperation.isPending ||
        headLiftIncrementOperation.isPending ||
        microMoveProofOperation.isPending ||
        latchLockOperation.isPending ||
        latchUnlockOperation.isPending ||
        emergencyStopOperation.isPending;
    const operationActionError =
        getErrorMessage(prepareSafeOperation.error) ||
        getErrorMessage(headClearLockOperation.error) ||
        getErrorMessage(headLiftIncrementOperation.error) ||
        getErrorMessage(microMoveProofOperation.error) ||
        getErrorMessage(latchLockOperation.error) ||
        getErrorMessage(latchUnlockOperation.error) ||
        getErrorMessage(emergencyStopOperation.error);

    const serviceOperationsPanel = (
        <SectionCard
            title="Ready-Made Robot Recipes"
            subtitle="Use OEM/robot-local recipes first: recover/arm, clear the head, latch/interlock, and emergency stop. Raw one-axis jogs are demoted to commissioning only because controller deltas are not physical proof."
        >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <div className="rounded-lg border border-border-primary bg-surface p-3 space-y-2">
                    <div className="text-sm font-semibold text-content">Recover + arm, no homing</div>
                    <div className="text-xs text-content-muted">Runs the known-working robot strict-startup/no-homing recipe directly. No intentional axis travel; energizes the motion gate and reports controller-only truth.</div>
                    <button onClick={() => prepareSafeOperation.mutate(operationPayload(), { onSuccess: recordOperationReport, onError: (error) => recordOperationError('prepare_safe', error) })} disabled={!isConnected || prepareSafeOperation.isPending} className="px-3 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg disabled:opacity-40">{prepareSafeOperation.isPending ? 'PREPARING...' : 'Prepare Motion Safely'}</button>
                </div>

                <div className="rounded-lg border border-border-primary bg-surface p-3 space-y-2">
                    <div className="text-sm font-semibold text-content">Latch / interlock</div>
                    <div className="text-xs text-content-muted">Lock/unlock via named operation wrapper with before/after latch readback.</div>
                    <div className="flex flex-wrap gap-2">
                        <button onClick={() => latchLockOperation.mutate(operationPayload(), { onSuccess: recordOperationReport, onError: (error) => recordOperationError('latch_lock', error) })} disabled={!isConnected || latchLockOperation.isPending} className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg disabled:opacity-40">{latchLockOperation.isPending ? 'LOCKING...' : 'Lock Latch'}</button>
                        <button onClick={() => latchUnlockOperation.mutate(operationPayload(), { onSuccess: recordOperationReport, onError: (error) => recordOperationError('latch_unlock', error) })} disabled={!isConnected || latchUnlockOperation.isPending} className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg disabled:opacity-40">{latchUnlockOperation.isPending ? 'UNLOCKING...' : 'Unlock Latch'}</button>
                    </div>
                </div>

                <div className="rounded-lg border border-border-primary bg-surface p-3 space-y-2">
                    <div className="text-sm font-semibold text-content">Head clearance</div>
                    <div className="text-xs text-content-muted">Runs the robot-local all-up clear-lock primitive. This is the ready-made head-clear routine, not a generic one-direction jog; stop if physical motion does not match the report.</div>
                    <div className="flex flex-wrap items-center gap-2">
                        <button onClick={() => headClearLockOperation.mutate(operationPayload(), { onSuccess: recordOperationReport, onError: (error) => recordOperationError('head_clear_lock', error) })} disabled={!isConnected || headClearLockOperation.isPending} className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg disabled:opacity-40">{headClearLockOperation.isPending ? 'CLEARING...' : 'Clear Head Lock'}</button>
                        <select value={headLiftSteps} onChange={(e) => setHeadLiftSteps(Number(e.target.value))} className="bg-surface-secondary border border-border-primary rounded px-2 py-2 text-xs text-content">
                            <option value={500}>500</option>
                            <option value={1000}>1000</option>
                            <option value={2500}>2500</option>
                        </select>
                        <button onClick={() => headLiftIncrementOperation.mutate({ ...operationPayload(), steps_abs: headLiftSteps }, { onSuccess: recordOperationReport, onError: (error) => recordOperationError('head_lift_increment', error) })} disabled={!isConnected || headLiftIncrementOperation.isPending} className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg disabled:opacity-40">Lift Z Up</button>
                    </div>
                </div>

                <div className="rounded-lg border border-border-primary bg-surface p-3 space-y-2 opacity-80">
                    <div className="text-sm font-semibold text-content">Controller-only proof move — commissioning fallback</div>
                    <div className="text-xs text-warning">Not a ready-made robot task. Use only after Recover + arm succeeds and someone is watching the mechanism; a clean JSON delta can still mean no physical motion.</div>
                    <div className="flex flex-wrap items-center gap-2">
                        <select value={microMoveAxis} onChange={(e) => setMicroMoveAxis(e.target.value as AxisName)} className="bg-surface-secondary border border-border-primary rounded px-2 py-2 text-xs text-content">
                            {(['x', 'y', 'z', 'g', 'door'] as AxisName[]).map((axis) => <option key={axis} value={axis}>{axis.toUpperCase()}</option>)}
                        </select>
                        <input type="number" value={microMoveSteps} min={-500} max={500} onChange={(e) => setMicroMoveSteps(Number(e.target.value))} className="w-24 bg-surface-secondary border border-border-primary rounded px-2 py-2 text-xs text-content font-mono" />
                        <button onClick={() => microMoveProofOperation.mutate({ ...operationPayload(), axis: microMoveAxis, steps: microMoveSteps }, { onSuccess: recordOperationReport, onError: (error) => recordOperationError('micro_move_proof', error) })} disabled={!isConnected || microMoveProofOperation.isPending} className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg disabled:opacity-40">Micro-move Proof</button>
                    </div>
                </div>
            </div>

            <div className="rounded-lg border border-error/20 bg-error/5 p-3 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-error">Emergency / recovery</div>
                    <div className="text-xs text-content-muted">Proxies OEM runtime emergency stop; still verify physical motion stop at the instrument.</div>
                </div>
                <button onClick={() => emergencyStopOperation.mutate(operationPayload(), { onSuccess: recordOperationReport, onError: (error) => recordOperationError('emergency_stop', error) })} disabled={!isConnected || emergencyStopOperation.isPending} className="px-4 py-2 bg-error hover:bg-error/80 text-white text-xs rounded-lg disabled:opacity-40">{emergencyStopOperation.isPending ? 'STOPPING...' : 'EMERGENCY STOP'}</button>
            </div>

            {operationMutationBusy && <div className="text-xs text-warning">Operation in flight; background polling is paused where possible.</div>}
            {operationActionError && <div className="text-xs text-error">{operationActionError}</div>}
            <JsonBlock title="Readiness Bundle" data={operationReadiness.data} fallback="Open Service Operations with linkage configured to poll readiness." />
            <JsonBlock title="Operation Capabilities" data={operationCapabilities.data} fallback="Capability map pending." />
            <JsonBlock title="Latest Operation Report" data={latestOperationReport} fallback="No named service operation executed yet." />
        </SectionCard>
    );

    const referencePanel = (
        <SectionCard
            title="Motion Reference Truth"
            subtitle="Robot-local reference state is separate from motion power. Liquid/live protocol work should wait for referenced axes."
        >
            <div className="flex items-center gap-3 flex-wrap">
                <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${referenceRows.every((row) => row.state === 'referenced') && referenceRows.length ? 'bg-success/10 text-success border-success/30' : 'bg-warning/10 text-warning border-warning/30'}`}>
                    REFERENCE: {getReferenceSummary(motionReferenceStatus.data).toUpperCase()}
                </div>
                {showCommissioningControls ? (
                    <>
                        <button
                            onClick={() => markMotionReferenced.mutate({ axes: ['x', 'y', 'z', 'g', 'door'], reason: 'operator_verified_at_console', operator: 'bms-cockpit' }, {
                                onSuccess: (data) => recordReferenceAction('mark_referenced', data),
                                onError: (error) => recordReferenceError('mark_referenced', error),
                            })}
                            disabled={!isConnected || markMotionReferenced.isPending}
                            className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {markMotionReferenced.isPending ? 'MARKING...' : 'Mark Referenced'}
                        </button>
                        <button
                            onClick={() => markMotionDesynced.mutate({ axes: ['x', 'y', 'z', 'g', 'door'], reason: 'operator_forced_desync', operator: 'bms-cockpit' }, {
                                onSuccess: (data) => recordReferenceAction('mark_desynced', data),
                                onError: (error) => recordReferenceError('mark_desynced', error),
                            })}
                            disabled={!isConnected || markMotionDesynced.isPending}
                            className="px-3 py-2 bg-error/20 hover:bg-error/30 text-error text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {markMotionDesynced.isPending ? 'DESYNCING...' : 'Mark Desynced'}
                        </button>
                    </>
                ) : (
                    <div className="text-[11px] text-content-muted">Reference edits are commissioning-only; default handler view is readback-only.</div>
                )}
            </div>
            {referenceRows.length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                    {referenceRows.map((row) => (
                        <div key={row.axis} className="p-2 rounded border border-border-primary bg-surface text-xs font-mono">
                            <div className="text-content font-semibold">{row.axis.toUpperCase()}</div>
                            <div className={row.state === 'referenced' ? 'text-success' : 'text-warning'}>{row.state}</div>
                            {row.origin != null && <div className="text-content-muted">origin {row.origin}</div>}
                        </div>
                    ))}
                </div>
            )}
            {referenceActionError && <div className="text-xs text-error">{referenceActionError}</div>}
            <JsonBlock title="Reference Snapshot" data={latestReferenceAction?.data ?? motionReferenceStatus.data} fallback="Reference state pending." />
        </SectionCard>
    );

    const liveXyzMotionPanel = (
        <SectionCard
            title="Live X/Y/Z Motion"
            subtitle="Operator-readable gantry controls: arm first, then use relative/zero buttons while watching the instrument. Manual switch-search home is disabled until robot-local predicates prove deassert→active transitions."
        >
            <div className="rounded-lg border border-warning/20 bg-warning/5 p-3 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="text-sm font-semibold text-warning">Operator-supervised movement</div>
                        <div className="text-xs text-content-muted">X/Y are currently reference-safe; Z is controller-readable but may need re-reference before blind absolute travel. Relative buttons remain the fastest practical move surface.</div>
                    </div>
                    <button
                        onClick={() => motionArmStrictStartup.mutate(
                            { run_homing: false },
                            { onSuccess: (data) => recordOemAction('motion_arm_strict_startup', data), onError: (error) => recordOemError('motion_arm_strict_startup', error) },
                        )}
                        disabled={!isConnected || motionArmStrictStartup.isPending}
                        className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        {motionArmStrictStartup.isPending ? 'ARMING...' : 'Arm Motors No Homing'}
                    </button>
                </div>
                <div className="text-[11px] text-warning">These controls can physically move the robot. Keep hands/tools clear; stop if controller delta does not match visible motion.</div>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-4">
                <AxisControls axis="x" label="Gantry X" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                <AxisControls axis="y" label="Gantry Y" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                <AxisControls axis="z" label="Pipette Z" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
            </div>
        </SectionCard>
    );

    const liquidPanel = (
        <SectionCard
            title="Liquid Handler Readback"
            subtitle="OEM-backed liquid status stays visible by default. Direct pipette actions live only in Commissioning Motion."
        >
            <div className="flex items-center gap-3 flex-wrap">
                <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${liquidStatus.data?.available ? 'bg-success/10 text-success border-success/30' : 'bg-warning/10 text-warning border-warning/30'}`}>
                    LIQUID: {liquidStatus.data?.available ? 'TRANSPORT' : 'UNKNOWN'} | {getLiquidTruthLabel(liquidStatus.data).toUpperCase()}
                </div>
                <div className="text-xs font-mono text-content-muted">
                    init {String(liquidStatus.data?.initialized ?? liquidStatus.data?.software_initialized ?? false)} | tip {String(liquidStatus.data?.tip_loaded ?? liquidStatus.data?.software_tip_loaded ?? false)}
                </div>
            </div>

            {liquidActionError && <div className="text-xs text-error">{liquidActionError}</div>}
            <JsonBlock title="Liquid Snapshot" data={liquidStatus.data} fallback="Liquid status pending." />
        </SectionCard>
    );

    const liquidCommissioningPanel = (
        <SectionCard
            title="Commissioning Liquid Commands"
            subtitle="Direct pipette commands are not part of the default handler surface. Use only with the operator watching the instrument."
        >
            <div className="space-y-3 border border-warning/20 rounded-lg p-3 bg-warning/5">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                    <label className="text-xs text-content-muted space-y-1">
                        <span>Volume uL</span>
                        <input type="number" min={0.1} step={0.1} value={liquidVolumeUl} onChange={(e) => setLiquidVolumeUl(Number(e.target.value))} className="w-full bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm" />
                    </label>
                    <label className="text-xs text-content-muted space-y-1">
                        <span>Cycles</span>
                        <input type="number" min={1} max={20} value={liquidCycles} onChange={(e) => setLiquidCycles(Number(e.target.value))} className="w-full bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm" />
                    </label>
                    <label className="text-xs text-content-muted space-y-1">
                        <span>Pressure</span>
                        <input value={liquidPressureProfile} onChange={(e) => setLiquidPressureProfile(e.target.value)} className="w-full bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm font-mono" />
                    </label>
                    <label className="text-xs text-content-muted space-y-1">
                        <span>Source</span>
                        <input value={liquidSource} onChange={(e) => setLiquidSource(e.target.value)} className="w-full bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm font-mono" />
                    </label>
                    <label className="text-xs text-content-muted space-y-1 md:col-span-2">
                        <span>Destination</span>
                        <input value={liquidDestination} onChange={(e) => setLiquidDestination(e.target.value)} className="w-full bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm font-mono" />
                    </label>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button onClick={() => liquidInit.mutate({}, { onSuccess: (data) => recordLiquidAction('init', data), onError: (error) => recordLiquidError('init', error) })} disabled={!isConnected || liquidInit.isPending} className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40">{liquidInit.isPending ? 'INIT...' : 'Init'}</button>
                    <button onClick={() => liquidTip.mutate({ tip_action: 'load' }, { onSuccess: (data) => recordLiquidAction('load_tip', data), onError: (error) => recordLiquidError('load_tip', error) })} disabled={!isConnected || liquidTip.isPending} className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40">Load Tip</button>
                    <button onClick={() => liquidTip.mutate({ tip_action: 'eject' }, { onSuccess: (data) => recordLiquidAction('eject_tip', data), onError: (error) => recordLiquidError('eject_tip', error) })} disabled={!isConnected || liquidTip.isPending} className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40">Eject Tip</button>
                    <button onClick={() => liquidAspirate.mutate(liquidCommandPayload(), { onSuccess: (data) => recordLiquidAction('aspirate', data), onError: (error) => recordLiquidError('aspirate', error) })} disabled={!isConnected || liquidAspirate.isPending} className="px-3 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40">Aspirate</button>
                    <button onClick={() => liquidDispense.mutate(liquidCommandPayload(), { onSuccess: (data) => recordLiquidAction('dispense', data), onError: (error) => recordLiquidError('dispense', error) })} disabled={!isConnected || liquidDispense.isPending} className="px-3 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40">Dispense</button>
                    <button onClick={() => liquidMix.mutate(liquidCommandPayload(), { onSuccess: (data) => recordLiquidAction('mix', data), onError: (error) => recordLiquidError('mix', error) })} disabled={!isConnected || liquidMix.isPending} className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40">Mix</button>
                </div>
            </div>
            {liquidActionError && <div className="text-xs text-error">{liquidActionError}</div>}
            <JsonBlock title="Last Liquid Command" data={latestLiquidAction?.data} fallback="No direct liquid command executed yet." />
        </SectionCard>
    );

    const oemReadbackPanel = (
        <SectionCard
            title="OEM Runtime & Startup"
            subtitle="Primary BioXP operator surface: BMS proxies the robot-local OEM runtime and liquid-handler contract instead of owning a second motor supervisor."
        >
            <div className="rounded-lg border border-accent/20 bg-accent/5 p-3 space-y-2">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">Live testing entrypoint</div>
                <div className="text-xs text-content-muted">Use the no-homing motor arm first. Startup preflight and PrepareToRunJob readiness are explicitly non-motion; the named dry-run route records motion_commanded=false / hardware_touched=false from the robot-local runtime response.</div>
                <div className="flex flex-wrap gap-2">
                    <button
                        onClick={() => motionArmStrictStartup.mutate(
                            { run_homing: false },
                            { onSuccess: (data) => recordOemAction('arm_motors_no_homing', data), onError: (error) => recordOemError('arm_motors_no_homing', error) },
                        )}
                        disabled={!isConnected || motionArmStrictStartup.isPending}
                        className="px-3 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        {motionArmStrictStartup.isPending ? 'ARMING...' : 'Arm Motors No Homing'}
                    </button>
                    <button
                        onClick={() => oemStartupRequest.mutate({ mode: 'dry_run', require_config: false, operator: 'bms-cockpit', source: 'oem-startup-panel-preflight' }, { onSuccess: (data) => recordOemAction('startup_preflight_no_motion', data), onError: (error) => recordOemError('startup_preflight_no_motion', error) })}
                        disabled={!isConnected || oemStartupRequest.isPending}
                        className="px-3 py-2 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10 disabled:opacity-40"
                    >
                        {oemStartupRequest.isPending ? 'CHECKING...' : 'Startup Preflight / No Motion'}
                    </button>
                    <button
                        onClick={() => oemInitializeSystem.mutate({ operator: 'bms-cockpit' }, { onSuccess: (data) => recordOemAction('initializeSystem', data), onError: (error) => recordOemError('initializeSystem', error) })}
                        disabled={!isConnected || oemInitializeSystem.isPending}
                        className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        {oemInitializeSystem.isPending ? 'INITIALIZING...' : 'Initialize System'}
                    </button>
                    <button
                        onClick={() => oemPrepareToRunJobReadiness.mutate(
                            {
                                mode: 'dry_run',
                                operator: 'bms-cockpit',
                                source: 'bms-oem-runtime-panel',
                                params: { no_motion: true, deck_inspection: true },
                            },
                            {
                                onSuccess: (data) => recordOemAction('prepare_to_run_job_readiness_no_motion', data),
                                onError: (error) => recordOemError('prepare_to_run_job_readiness_no_motion', error),
                            },
                        )}
                        disabled={!isConnected || oemPrepareToRunJobReadiness.isPending}
                        className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                        title="Calls /api/bioxp/oem/runtime/readiness/prepare-to-run-job/dry-run; it must not execute the raw PrepareToRunJob runtime command."
                    >
                        {oemPrepareToRunJobReadiness.isPending ? 'CHECKING READINESS...' : 'PrepareToRunJob Readiness / No Motion'}
                    </button>
                    <button
                        onClick={() => oemStartupDoorEvent.mutate({ event: 'closed', operator: 'bms-cockpit' }, { onSuccess: (data) => recordOemAction('door_closed', data), onError: (error) => recordOemError('door_closed', error) })}
                        disabled={!isConnected || oemStartupDoorEvent.isPending}
                        className="px-3 py-2 bg-white/10 hover:bg-white/15 text-content text-xs rounded-lg transition-colors border border-white/10 disabled:opacity-40"
                    >
                        Door Closed Event
                    </button>
                    <button
                        onClick={() => oemUnlockProcess.mutate({ operator: 'bms-cockpit' }, { onSuccess: (data) => recordOemAction('unlockProcess', data), onError: (error) => recordOemError('unlockProcess', error) })}
                        disabled={!isConnected || oemUnlockProcess.isPending}
                        className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        Unlock Process
                    </button>
                    <button
                        onClick={() => oemRuntimeRecover.mutate({ operator: 'bms-cockpit' }, { onSuccess: (data) => recordOemAction('runtime_recover', data), onError: (error) => recordOemError('runtime_recover', error) })}
                        disabled={!isConnected || oemRuntimeRecover.isPending}
                        className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        {oemRuntimeRecover.isPending ? 'RECOVERING...' : 'Recover Runtime'}
                    </button>
                    <button
                        onClick={() => oemRuntimeEmergencyStop.mutate({ operator: 'bms-cockpit' }, { onSuccess: (data) => recordOemAction('emergency_stop', data), onError: (error) => recordOemError('emergency_stop', error) })}
                        disabled={!isConnected || oemRuntimeEmergencyStop.isPending}
                        className="px-3 py-2 bg-error hover:bg-error/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40"
                    >
                        {oemRuntimeEmergencyStop.isPending ? 'STOPPING...' : 'EMERGENCY STOP'}
                    </button>
                </div>
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                <JsonBlock title="Latest OEM Action" data={latestOemAction?.data} fallback="No OEM action executed yet." />
                <JsonBlock title="Startup Latest" data={oemStartupLatest.data} fallback="Startup status pending." />
                <JsonBlock title="Runtime Status" data={oemRuntimeStatus.data} fallback="OEM runtime status pending." />
                <JsonBlock title="Runtime State" data={oemRuntimeState.data} fallback="OEM runtime state pending." />
                <JsonBlock title="Motion Range / Switches" data={motionRangeStatus.data} fallback="Range readback pending." />
            </div>
            {(oemStartupLatest.isError || oemRuntimeStatus.isError || oemRuntimeState.isError || motionRangeStatus.isError) && (
                <div className="text-xs text-error">
                    {getErrorMessage(oemStartupLatest.error) || getErrorMessage(oemRuntimeStatus.error) || getErrorMessage(oemRuntimeState.error) || getErrorMessage(motionRangeStatus.error)}
                </div>
            )}
        </SectionCard>
    );

    const visionPanel = (
        <SectionCard title="Vision / Barcode Smoke Tests" subtitle="Thin proxies for robot-local inspection/barcode endpoints; useful before live workflow tests.">
            <div className="flex flex-wrap gap-2">
                <button
                    onClick={() => visionInspect.mutate({ device: cameraDevice, mode: 'deck_smoke' }, { onSuccess: (data) => recordVisionAction('inspect', data), onError: (error) => recordVisionError('inspect', error) })}
                    disabled={!isConnected || visionInspect.isPending}
                    className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                >
                    {visionInspect.isPending ? 'INSPECTING...' : 'Inspect'}
                </button>
                <button
                    onClick={() => visionBarcodeRead.mutate({ device: cameraDevice }, { onSuccess: (data) => recordVisionAction('barcode', data), onError: (error) => recordVisionError('barcode', error) })}
                    disabled={!isConnected || visionBarcodeRead.isPending}
                    className="px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                >
                    {visionBarcodeRead.isPending ? 'READING...' : 'Read Barcode'}
                </button>
            </div>
            {visionActionError && <div className="text-xs text-error">{visionActionError}</div>}
            <JsonBlock title="Vision Result" data={latestVisionAction?.data} fallback="No vision action executed yet." />
        </SectionCard>
    );

    const cameraTransportPanel = (
        <div className="space-y-3">
            <div className="space-y-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-content">Camera Control</div>
                <div className="text-[10px] text-content-muted">On-demand live view with recovery and fullscreen calibration controls.</div>
            </div>
            <div className="space-y-2">
                <label className="block text-[10px] font-mono text-content-muted">DEVICE</label>
                <input
                    type="text"
                    value={cameraDevice}
                    onChange={(e) => setCameraDevice(e.target.value)}
                    className="w-full bg-surface/90 border border-white/10 rounded px-2 py-1.5 text-content text-[11px] font-mono"
                />
            </div>
            <div className="space-y-2">
                <label className="block text-[10px] font-mono text-content-muted">MODE</label>
                <select
                    value={streamMode}
                    onChange={(e) => setStreamMode(e.target.value as (typeof CAMERA_STREAM_MODES)[number]['key'])}
                    className="w-full bg-surface/90 border border-white/10 rounded px-2 py-1.5 text-content text-[11px] font-mono"
                >
                    {CAMERA_STREAM_MODES.map((mode) => (
                        <option key={mode.key} value={mode.key}>
                            {mode.label}
                        </option>
                    ))}
                </select>
            </div>
            <div className="space-y-2">
                <label className="block text-[10px] font-mono text-content-muted">FPS</label>
                <input
                    type="number"
                    min={1}
                    max={selectedStreamMode.maxFps}
                    value={streamFps}
                    onChange={(e) => setStreamFps(Number(e.target.value))}
                    className="w-20 bg-surface/90 border border-white/10 rounded px-2 py-1.5 text-content text-[11px] font-mono"
                />
            </div>
            <div className="grid grid-cols-2 gap-2">
                <button
                    onClick={() => {
                        setStreamError(null);
                        if (pollCamera) {
                            cameraStop.mutate(
                                { device: cameraDevice },
                                {
                                    onSuccess: (data) => recordCameraAction('stop', data),
                                    onError: (error) => recordCameraError('stop', error),
                                },
                            );
                        } else {
                            setSnapshot(null);
                            setLatestCameraAction(null);
                        }
                        setPollCamera((prev) => !prev);
                    }}
                    className="px-2 py-2 text-[11px] rounded transition-colors bg-white/10 hover:bg-white/15 text-content border border-white/10"
                >
                    {pollCamera ? (cameraStop.isPending ? 'Stopping' : 'Stop Stream') : 'Start Stream'}
                </button>
                <button
                    onClick={captureFrame}
                    disabled={cameraSnapshot.isPending || pollCamera}
                    className="px-2 py-2 bg-white/10 hover:bg-white/15 text-content text-[11px] rounded transition-colors border border-white/10 disabled:opacity-40"
                >
                    {cameraSnapshot.isPending ? 'Capturing' : 'Capture'}
                </button>
                <button
                    onClick={() => {
                        setPollCamera(false);
                        setStreamError(null);
                        cameraReset.mutate(
                            { device: cameraDevice },
                            {
                                onSuccess: (data) => recordCameraAction('reset', data),
                                onError: (error) => recordCameraError('reset', error),
                            },
                        );
                    }}
                    disabled={cameraReset.isPending}
                    className="px-2 py-2 bg-white/10 hover:bg-white/15 text-content text-[11px] rounded transition-colors border border-white/10 disabled:opacity-40"
                >
                    {cameraReset.isPending ? 'Resetting' : 'Reset'}
                </button>
                <button
                    onClick={() =>
                        cameraStreamHealth.mutate(
                            { device: cameraDevice, seconds: 5 },
                            {
                                onSuccess: (data) => recordCameraAction('health', data),
                                onError: (error) => recordCameraError('health', error),
                            },
                        )
                    }
                    disabled={cameraStreamHealth.isPending || pollCamera}
                    className="px-2 py-2 bg-white/10 hover:bg-white/15 text-content text-[11px] rounded transition-colors border border-white/10 disabled:opacity-40"
                >
                    {cameraStreamHealth.isPending ? 'Checking' : 'Health'}
                </button>
            </div>
            <button
                onClick={() =>
                    cameraAutoRecover.mutate(
                        { device: cameraDevice, max_resets: 2 },
                        {
                            onSuccess: (data) => recordCameraAction('recover', data),
                            onError: (error) => recordCameraError('recover', error),
                        },
                    )
                }
                disabled={cameraAutoRecover.isPending || pollCamera}
                className="w-full px-2 py-2 bg-white/10 hover:bg-white/15 text-content text-[11px] rounded transition-colors border border-white/10 disabled:opacity-40"
            >
                {cameraAutoRecover.isPending ? 'Recovering' : 'Auto Recover'}
            </button>
            <button
                onClick={() => void toggleViewerFullscreen()}
                className="w-full px-2 py-2 bg-white/10 hover:bg-white/15 text-content text-[11px] rounded transition-colors border border-white/10"
            >
                {viewerFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
            </button>
            <div className="space-y-1 text-[10px] font-mono text-content-muted">
                <div>STATE: {pollCamera ? (streamReady ? `LIVE ${streamFps} FPS` : 'CONNECTING') : 'IDLE'}</div>
                <div>NODE: {cameraDevice}</div>
                <div>MODE: {selectedStreamMode.width}x{selectedStreamMode.height}</div>
                {cameraSnapshot.data?.path && <div>LAST: {cameraSnapshot.data.path}</div>}
            </div>
            {latestCameraSummary && (
                <div className="rounded border border-white/10 bg-white/5 px-2 py-2 text-[10px] font-mono text-content-muted break-words">
                    {latestCameraSummary}
                </div>
            )}
            {cameraActionError && (
                <div className="text-[10px] text-error font-mono">{cameraActionError}</div>
            )}
        </div>
    );
    const cameraSettingsPanel = (
        <div className="space-y-3">
            <div className="space-y-1 pt-1 border-t border-white/10">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-content">Lens & Image</div>
                <div className="text-[10px] text-content-muted">
                    {hasZoomControl
                        ? 'Live V4L2 tuning is available while you are in the viewer.'
                        : 'No hardware zoom control is reported here, but the available image controls are exposed below.'}
                </div>
            </div>
            {cameraVisibleControls.length > 0 ? (
                <div className="space-y-2">
                    {cameraVisibleControls.map((control) => (
                        <CameraSettingControl
                            key={control.cid}
                            control={control}
                            disabled={!isConnected}
                            pending={pendingCameraControlCid === control.cid}
                            onApply={applyCameraControl}
                        />
                    ))}
                </div>
            ) : (
                <div className="rounded border border-white/10 bg-white/5 px-2 py-2 text-[10px] font-mono text-content-muted">
                    No writable camera controls have been reported for {cameraDevice} yet.
                </div>
            )}
        </div>
    );
    const cameraMotionPanel = (
        <div className="space-y-3">
            <div className="space-y-1 pb-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-content">Quick Motion</div>
                <div className="text-[10px] text-content-muted">Guarded jog controls for live calibration inside the viewer. Hold an arrow to keep nudging while limits and runtime guardrails stay active.</div>
            </div>
            {showCommissioningControls ? (
                <>
                    <CameraHoldJogPad enabled={isConnected} />
                    <CameraAxisQuickControls axis="g" label="Gripper" enabled={isConnected} />
                </>
            ) : (
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2 text-[11px] text-content-muted">
                    Camera-overlaid jog controls are commissioning-only. Use the live image for observation unless the supervised commissioning toggle is open.
                </div>
            )}
        </div>
    );
    const motionPowerPanel = (
        <SectionCard
            title="Motion Power & Recovery"
            subtitle="Explicit motor bring-up and recovery primitives from the BioXp runtime."
        >
            <div className="grid grid-cols-2 xl:grid-cols-3 gap-2 text-[11px] font-mono text-content-muted">
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">24V Rail</div>
                    <div className={`mt-1 font-semibold ${motion24vState === 'OK' ? 'text-success' : motion24vState === 'NO_24V' ? 'text-error' : 'text-warning'}`}>
                        {motion24vState}
                    </div>
                    <div className="mt-1">raw {motionPowerRail?.raw ?? 'n/a'}</div>
                </div>
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">Motion Arm</div>
                    <div className="mt-1 font-semibold text-content">
                        {motionPowerEffectiveStatus?.motion_arm?.armed == null
                            ? 'LEGACY'
                            : motionPowerEffectiveStatus.motion_arm.armed
                                ? 'ARMED'
                                : 'DISARMED'}
                    </div>
                    <div className="mt-1">seq {motionPowerEffectiveStatus?.motion_arm?.arm_seq ?? 'n/a'}</div>
                </div>
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">Latch Override</div>
                    <div className="mt-1 font-semibold text-content">
                        {motionPowerEffectiveStatus?.latch_override?.enabled == null
                            ? 'LEGACY'
                            : motionPowerEffectiveStatus.latch_override.enabled
                                ? 'ENABLED'
                                : 'OFF'}
                    </div>
                    <div className="mt-1 truncate">{motionPowerEffectiveStatus?.latch_override?.note ?? 'default lock path'}</div>
                </div>
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">Solenoid</div>
                    <div className="mt-1 font-semibold text-content">
                        {motionIoSnapshot?.['2'] === 1 ? 'LOCKED' : motionIoSnapshot?.['2'] === 0 ? 'UNLOCKED' : 'UNKNOWN'}
                    </div>
                    <div className="mt-1">raw {motionIoSnapshot?.['2'] ?? 'n/a'}</div>
                </div>
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">Door Sensor</div>
                    <div className="mt-1 font-semibold text-content">{motionIoSnapshot?.['1'] ?? 'n/a'}</div>
                    <div className="mt-1">Latch sensor {motionIoSnapshot?.['3'] ?? 'n/a'}</div>
                </div>
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-content-muted">Boards</div>
                    <div className="mt-1 leading-relaxed break-words">{getBoardAckSummary(motionPowerEffectiveStatus?.board_status)}</div>
                </div>
            </div>

            {motionPowerApiMissing && (
                <div className="rounded border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] text-warning">
                    Linked robot runtime is older than this BMS UI. `Enable 24V / Prep Axes` is using the legacy interlock-prep path; `Arm Motion` and `Driver Power Diag` require the newer runtime update.
                </div>
            )}

            {showCommissioningControls ? (
                <div className="space-y-3 rounded-lg border border-warning/20 bg-warning/5 p-3">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-warning">Commissioning motor power actions</div>
                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => {
                                if (motionPowerApiMissing) {
                                    prepareInterlock.mutate(undefined, {
                                        onSuccess: (data) => recordMotionInfraAction('enable_legacy', { ...data, legacy_fallback: true }),
                                        onError: (error) => recordMotionInfraError('enable_legacy', error),
                                    });
                                    return;
                                }
                                motionPowerEnable.mutate(undefined, {
                                    onSuccess: (data) => recordMotionInfraAction('enable', data),
                                    onError: (error) => recordMotionInfraError('enable', error),
                                });
                            }}
                            disabled={motionPowerEnable.isPending || (motionPowerApiMissing && prepareInterlock.isPending)}
                            className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {motionPowerEnable.isPending || (motionPowerApiMissing && prepareInterlock.isPending) ? 'ENABLING...' : 'Enable 24V / Prep Axes'}
                        </button>
                        <button
                            onClick={() =>
                                motionArmStrictStartup.mutate(
                                    { run_homing: false },
                                    {
                                        onSuccess: (data) => recordMotionInfraAction('arm_motion', data),
                                        onError: (error) => recordMotionInfraError('arm_motion', error),
                                    },
                                )
                            }
                            disabled={motionArmStrictStartup.isPending || motionPowerApiMissing}
                            className="px-4 py-2 bg-success/20 hover:bg-success/30 text-success text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {motionArmStrictStartup.isPending ? 'ARMING...' : 'Arm Motion'}
                        </button>
                        <button
                            onClick={() =>
                                prepareInterlock.mutate(undefined, {
                                    onSuccess: (data) => recordMotionInfraAction('interlock', data),
                                    onError: (error) => recordMotionInfraError('interlock', error),
                                })
                            }
                            disabled={prepareInterlock.isPending}
                            className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {prepareInterlock.isPending ? 'PREPPING...' : 'Prepare Interlock'}
                        </button>
                        <button
                            onClick={() =>
                                clearLock.mutate(undefined, {
                                    onSuccess: (data) => recordMotionInfraAction('clear_lock', data),
                                    onError: (error) => recordMotionInfraError('clear_lock', error),
                                })
                            }
                            disabled={clearLock.isPending}
                            className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {clearLock.isPending ? 'CLEARING...' : 'Clear Head Lock'}
                        </button>
                        <button
                            onClick={() =>
                                motionPowerDiag.mutate(undefined, {
                                    onSuccess: (data) => recordMotionInfraAction('diag', data),
                                    onError: (error) => recordMotionInfraError('diag', error),
                                })
                            }
                            disabled={motionPowerDiag.isPending || motionPowerApiMissing}
                            className="px-4 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {motionPowerDiag.isPending ? 'CHECKING...' : 'Driver Power Diag'}
                        </button>

                    </div>
                </div>
            ) : (
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2 text-[11px] text-content-muted">
                    Actuating power, interlock, and lock-clear buttons are hidden from the default operator surface. Open Commissioning Motion only with the operator at the instrument.
                </div>
            )}

            {latestMotionInfraSummary && (
                <div className="rounded border border-border-primary bg-surface-tertiary px-3 py-2 text-[11px] font-mono text-content-muted break-words">
                    {latestMotionInfraSummary}
                </div>
            )}

            {motionActionError && (
                <div className="text-xs text-error">{motionActionError}</div>
            )}

            <JsonBlock
                title="Latest Motion Infra Result"
                data={latestMotionInfraResult ?? motionPowerEffectiveStatus}
                fallback="Motion power snapshot pending."
            />
        </SectionCard>
    );

    return (
        <div className="flex flex-col h-full overflow-y-auto p-8 space-y-6 bg-surface">
            <div className="flex justify-between items-start border-b border-border-secondary pb-4">
                <div>
                    <h2 className="text-lg font-semibold text-content">BioXP Handler Controls</h2>
                    <p className="text-sm text-content-muted">OEM/liquid-handler-first BMS proxy for the robot-local BioXP runtime: linkage, named no-motion readiness, startup, readback, protocol, thermal, chiller, camera, and commissioning controls.</p>
                </div>
                <div className={`px-4 py-1.5 rounded-sm text-xs font-mono font-semibold border ${isConnected ? (isDegraded ? 'bg-warning/10 text-warning border-warning/30' : 'bg-success/10 text-success border-success/30') : 'bg-error/10 text-error border-error/30'}`}>
                    HARDWARE: {statusLoading ? 'PINGING...' : isConnected ? (isRecovering ? 'RECOVERING' : isDegraded ? 'DEGRADED' : 'ONLINE') : 'OFFLINE'}
                </div>
            </div>

            <div className="flex gap-1 border-b border-border-secondary flex-wrap">
                {([
                    { key: 'connection', label: 'Runtime Linkage' },
                    { key: 'controls', label: 'Live X/Y/Z + Handler' },
                    { key: 'operator', label: 'Protocol Jobs' },
                    { key: 'service', label: 'Gated Service Recipes' },
                    ...(showCommissioningControls ? [{ key: 'manual', label: 'Commissioning Motion' }] as const : []),
                    { key: 'camera', label: 'Camera / Vision' },
                ] as const).map((tab) => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key)}
                        className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === tab.key ? 'border-accent text-accent' : 'border-transparent text-content-muted hover:text-content hover:border-border-primary'}`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {activeTab === 'connection' && (
                <div className="space-y-6">
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <SectionCard
                            title="Linked Runtime Status"
                            subtitle={`HTTP reachability for the robot-owned BioXP runtime at ${runtimeStatus?.runtime_url ?? interlinkUrl}.`}
                        >
                            <div className="flex items-center gap-4 flex-wrap">
                                <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${runtimeSummary.badgeClassName}`}>
                                    RUNTIME: {runtimeSummary.label}
                                </div>
                                <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${runtimeSummary.adminControlAvailable ? 'bg-accent/10 text-accent border-accent/30' : 'bg-surface-tertiary text-content border-border-primary'}`}>
                                    ADMIN: {runtimeSummary.adminLabel}
                                </div>
                            </div>
                            {runtimeStatusHelp ? (
                                <div className="text-xs text-content-muted">{runtimeStatusHelp}</div>
                            ) : null}
                            <div className="text-xs text-content-muted">
                                {runtimeSummary.adminDetail}
                            </div>
                        </SectionCard>

                        <SectionCard
                            title="Governed Interlink"
                            subtitle="The cockpit is readback-only until the top-right BIOXP LINK panel activates a saved robot profile."
                        >
                            <div className="grid grid-cols-1 gap-2 text-xs font-mono text-content-muted">
                                <div>
                                    Interlink state: <span className="text-accent">{interlinkActive ? 'active' : interlinkConfigured ? 'saved / inactive' : 'quiet / unconfigured'}</span>
                                </div>
                                <div>
                                    Robot API URL: <span className="text-accent break-all">{interlinkActive ? interlinkUrl : '(inactive)'}</span>
                                </div>
                                <div>
                                    Connect from BIOXP LINK first; this cockpit does not save robot URLs, auto-connect, reset the runtime, reboot the robot OS, home, arm, recover motion, or move axes on load.
                                </div>
                            </div>
                            {(bioXpInterlink.isError || statusIsError || linkageHelp) && (
                                <div className="text-xs text-error">
                                    {getErrorMessage(bioXpInterlink.error) || getErrorMessage(statusError) || linkageHelp}
                                </div>
                            )}
                        </SectionCard>
                    </div>

                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <SectionCard
                            title="Recovery & Interlocks"
                            subtitle="Default status surface only; lifecycle actions live in BIOXP LINK or Commissioning Motion."
                        >
                            <div className="text-[11px] text-content-muted">Runtime lifecycle actions are governed by the top-right BIOXP LINK panel. Motion interlock and lock-clear actions are commissioning-only and are not exposed on this default status tab.</div>
                            {(prepareInterlock.isError || clearLock.isError) && (
                                <div className="text-xs text-error">
                                    {getErrorMessage(prepareInterlock.error) || getErrorMessage(clearLock.error)}
                                </div>
                            )}
                            <JsonBlock title="Latest Recovery Result" data={latestHardwareAction} fallback="No recovery action executed yet." />
                        </SectionCard>

                        <SectionCard
                            title="Latch & Deck IO"
                            subtitle="Motion prep depends on the latch and deck IO states exposed by the BioXP runtime."
                        >
                            {showCommissioningControls ? (
                                <div className="space-y-2 rounded-lg border border-warning/20 bg-warning/5 p-3">
                                    <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-warning">Commissioning latch actions</div>
                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            onClick={() => latchLock.mutate()}
                                            disabled={!isConnected || latchLock.isPending}
                                            className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                                        >
                                            {latchLock.isPending ? 'LOCKING...' : 'Lock'}
                                        </button>
                                        <button
                                            onClick={() => latchUnlock.mutate()}
                                            disabled={!isConnected || latchUnlock.isPending}
                                            className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                                        >
                                            {latchUnlock.isPending ? 'UNLOCKING...' : 'Unlock'}
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <div className="text-[11px] text-content-muted">Latch state is readback-only by default; lock/unlock controls require Commissioning Motion.</div>
                            )}
                            {(latchLock.isError || latchUnlock.isError || latchStatus.isError) && (
                                <div className="text-xs text-error">
                                    {getErrorMessage(latchLock.error) || getErrorMessage(latchUnlock.error) || getErrorMessage(latchStatus.error)}
                                </div>
                            )}
                            <JsonBlock title="Latch Snapshot" data={latchStatus.data} fallback="Latch status will appear when the hardware link is online." />
                        </SectionCard>
                    </div>

                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <SectionCard
                            title="LED Control"
                            subtitle="The upstream runtime supports the deck LED path; BMS can now drive it directly."
                        >
                            <div className="flex items-center gap-3">
                                <div className="w-12 h-12 rounded-lg border border-border-primary" style={{ backgroundColor: `rgb(${ledRgbState.r}, ${ledRgbState.g}, ${ledRgbState.b})` }} />
                                <div className="text-xs font-mono text-content-muted">
                                    rgb({ledRgbState.r}, {ledRgbState.g}, {ledRgbState.b})
                                </div>
                            </div>
                            <div className="grid grid-cols-3 gap-2">
                                {(['r', 'g', 'b'] as const).map((channel) => (
                                    <input
                                        key={channel}
                                        type="number"
                                        min={0}
                                        max={255}
                                        value={ledRgbState[channel]}
                                        onChange={(e) => setLedRgbState((prev) => ({ ...prev, [channel]: Number(e.target.value) }))}
                                        className="bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm"
                                    />
                                ))}
                            </div>
                            <div className="flex flex-wrap gap-2 items-center">
                                <input
                                    type="number"
                                    min={0}
                                    max={100}
                                    value={ledPctState}
                                    onChange={(e) => setLedPctState(Number(e.target.value))}
                                    className="bg-surface border border-accent/10 rounded-lg px-3 py-2 text-content text-sm w-24"
                                />
                                <button
                                    onClick={() => ledPct.mutate(ledPctState)}
                                    disabled={!isConnected || ledPct.isPending}
                                    className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                                >
                                    Set White %
                                </button>
                                <button
                                    onClick={() => ledRgb.mutate(ledRgbState)}
                                    disabled={!isConnected || ledRgb.isPending}
                                    className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                                >
                                    Apply RGB
                                </button>
                                <button
                                    onClick={() => ledOff.mutate()}
                                    disabled={!isConnected || ledOff.isPending}
                                    className="px-4 py-2 bg-error/20 hover:bg-error/30 text-error text-xs rounded-lg transition-colors"
                                >
                                    LED Off
                                </button>
                            </div>
                            {(ledRgb.isError || ledPct.isError || ledOff.isError) && (
                                <div className="text-xs text-error">
                                    {getErrorMessage(ledRgb.error) || getErrorMessage(ledPct.error) || getErrorMessage(ledOff.error)}
                                </div>
                            )}
                        </SectionCard>

                        <SectionCard title="Telemetry Payload" subtitle="This is the raw status object returned from the BioXP runtime through the BMS proxy.">
                            <JsonBlock title="Status JSON" data={statusIsError ? { error: getErrorMessage(statusError) } : status} fallback="Polling..." />
                        </SectionCard>

                        <SectionCard title="Proxy Capability Matrix" subtitle="Shows whether BMS now exposes the robot-local automation routes needed for reference, liquid, camera, and vision testing.">
                            <JsonBlock title="Capabilities" data={capabilities.data} fallback="Capability matrix pending." />
                        </SectionCard>
                    </div>
                </div>
            )}

            {activeTab === 'operator' && (
                <BioXpProtocolRunner
                    linkageConfigured={runtimeSummary.linkageConfigured}
                    runtimeSummary={runtimeSummary}
                />
            )}


            {activeTab === 'service' && (
                !isConnected ? (
                    <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                        <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                        <p className="text-xs text-content-muted mt-2">Configure a working runtime linkage first. Service operation buttons stay disabled until BMS can reach the robot-local BioXP API.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <div className="space-y-6">
                            {serviceOperationsPanel}
                        </div>
                        <div className="space-y-6">
                            {motionPowerPanel}
                            {referencePanel}
                        </div>
                    </div>
                )
            )}

            {activeTab === 'manual' && (
                !isConnected ? (
                    <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                        <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                        <p className="text-xs text-content-muted mt-2">Configure a working runtime linkage first. Commissioning motion buttons stay disabled until BMS is linked to the robot-local BioXP API.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <div className="space-y-6">
                            <SectionCard
                                title="Commissioning Motion — Axis Controls"
                                subtitle="Hidden from the default handler path. These supervised movement buttons execute through BMS → robot-local BioXP API proxy only for live commissioning; raw switch-search home is disabled."
                            >
                                <div className="text-[11px] font-mono text-content-muted">
                                    Safety profile: speed 100, acc 50 unless the robot preset reports a safer axis profile; abort if speed is nonzero with no position delta for 2s.
                                    {motionBusy ? ' Background polling is paused while a motion command is in flight.' : null}
                                </div>
                                <div className="mt-3 space-y-4">
                                    <AxisControls axis="x" label="Gantry X" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                                    <AxisControls axis="y" label="Gantry Y" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                                    <AxisControls axis="z" label="Pipette Z" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                                    <AxisControls axis="g" label="Gripper" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                                </div>
                            </SectionCard>
                        </div>
                        <div className="space-y-6">
                            {motionPowerPanel}
                            {referencePanel}
                            {liquidCommissioningPanel}
                            <SectionCard
                                title="Thermal Door Commissioning Motion"
                                subtitle="Door-axis motion is still separated from normal X/Y/Z/gripper movement because it interacts with the thermal subsystem."
                            >
                                {showCommissioningControls ? (
                                    <AxisControls axis="door" label="Thermal Door" enabled={isConnected} pollIntervalMs={motionBusy ? false : 8000} />
                                ) : (
                                    <div className="text-xs text-content-muted">Open Commissioning Motion before moving the thermal door axis directly. Thermal door context/readback remains available on Handler Controls.</div>
                                )}
                            </SectionCard>
                        </div>
                    </div>
                )
            )}

            {activeTab === 'controls' && (
                !isConnected ? (
                    <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                        <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                        <p className="text-xs text-content-muted mt-2">Configure a working runtime linkage first. Handler readback, thermal, and chiller panels stay disabled until the runtime reports actual board reachability.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <div className="space-y-6">
                            {liveXyzMotionPanel}
                            {oemReadbackPanel}
                            <BioXpProtocolRunner
                                linkageConfigured={runtimeSummary.linkageConfigured}
                                runtimeSummary={runtimeSummary}
                            />
                            {liquidPanel}
                            {referencePanel}
                            {visionPanel}
                            <SectionCard
                                title="Commissioning Access"
                                subtitle="Opens the separate Commissioning Motion tab for raw motion, power/interlock recovery, direct pipette, latch, camera-jog, and thermal-door actions."
                            >
                                <button
                                    type="button"
                                    onClick={() => setShowCommissioningControls((value) => !value)}
                                    className="px-3 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors"
                                >
                                    {showCommissioningControls ? 'Hide Commissioning Controls' : 'Show Commissioning Controls'}
                                </button>
                                {showCommissioningControls ? (
                                    <div className="space-y-4 border border-warning/20 rounded-lg p-3 bg-warning/5">
                                        <div className="text-[11px] font-mono text-content-muted">
                                            Commissioning Motion is now the only place for raw X/Y/Z/gripper movement, direct pipette commands, latch actions, power/interlock recovery, camera-jog, and thermal-door movement.
                                        </div>
                                    </div>
                                ) : (
                                    <div className="text-xs text-content-muted">Default live testing uses Governed Interlink, Protocol Operator, and Handler Controls. Thermal Door remains available as readback and thermal-system context here; direct thermal-door movement is commissioning-only.</div>
                                )}
                            </SectionCard>
                        </div>

                        <div className="space-y-6">
                            <SectionCard
                                title="Thermal Cycler"
                                subtitle="Setpoint, fan, PWM, and rate control plus baseline, fast-profile, and hard-reset recovery."
                            >
                                <div className="grid grid-cols-1 gap-3">
                                    <ThermalControlCard bank="nest" label="Nest" enabled={isConnected} />
                                    <ThermalControlCard bank="lid" label="Lid" enabled={isConnected} />
                                    <ThermalControlCard bank="pedestal" label="Pedestal" enabled={isConnected} />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <button
                                        onClick={() => thermalBaseline.mutate()}
                                        disabled={thermalBaseline.isPending}
                                        className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                                    >
                                        Apply Baseline
                                    </button>
                                    <button
                                        onClick={() => thermalFastProfile.mutate()}
                                        disabled={thermalFastProfile.isPending}
                                        className="px-4 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors"
                                    >
                                        Fast Profile
                                    </button>
                                    <button
                                        onClick={() => thermalHardReset.mutate()}
                                        disabled={thermalHardReset.isPending}
                                        className="px-4 py-2 bg-error/20 hover:bg-error/30 text-error text-xs rounded-lg transition-colors"
                                    >
                                        Reset thermal controller profile
                                    </button>
                                </div>
                                {(thermalBaseline.isError || thermalFastProfile.isError || thermalHardReset.isError || thermalSnapshot.isError) && (
                                    <div className="text-xs text-error">
                                        {getErrorMessage(thermalBaseline.error) || getErrorMessage(thermalFastProfile.error) || getErrorMessage(thermalHardReset.error) || getErrorMessage(thermalSnapshot.error)}
                                    </div>
                                )}
                                <JsonBlock title="Thermal Snapshot" data={thermalSnapshot.data} fallback="Thermal snapshot pending." />
                            </SectionCard>

                            <SectionCard
                                title="Chiller System"
                                subtitle="Setpoint, fan, PWM, and rate control are now available in BMS for both RC and OC banks."
                            >
                                <div className="grid grid-cols-1 gap-3">
                                    <ChillerControlCard bank="rc" label="RC Bank" enabled={isConnected} />
                                    <ChillerControlCard bank="oc" label="OC Bank" enabled={isConnected} />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <button
                                        onClick={() => chillerBaseline.mutate()}
                                        disabled={chillerBaseline.isPending}
                                        className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                                    >
                                        Apply Baseline
                                    </button>
                                    <button
                                        onClick={() => chillerHardReset.mutate()}
                                        disabled={chillerHardReset.isPending}
                                        className="px-4 py-2 bg-error/20 hover:bg-error/30 text-error text-xs rounded-lg transition-colors"
                                    >
                                        Reset chiller profile
                                    </button>
                                </div>
                                {(chillerBaseline.isError || chillerHardReset.isError || chillerSnapshot.isError) && (
                                    <div className="text-xs text-error">
                                        {getErrorMessage(chillerBaseline.error) || getErrorMessage(chillerHardReset.error) || getErrorMessage(chillerSnapshot.error)}
                                    </div>
                                )}
                                <JsonBlock title="Chiller Snapshot" data={chillerSnapshot.data} fallback="Chiller snapshot pending." />
                            </SectionCard>
                        </div>
                    </div>
                )
            )}

            {activeTab === 'camera' && (
                !isConnected ? (
                    <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                        <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                        <p className="text-xs text-content-muted mt-2">The camera tab now depends on the real runtime responses. Bring the linkage online first, then capture or stream frames from the actual device.</p>
                    </div>
                ) : (
                    <div className="space-y-6">
                        <div className="space-y-3">
                            <div className="text-xs text-content-muted max-w-3xl">
                                The live viewer is now the primary calibration surface. Camera transport stays on the left rail and guarded X/Y/Z motion stays on the right rail.
                            </div>

                            <div className="flex justify-center">
                                <div
                                    ref={cameraViewerRef}
                                    className={viewerFullscreen ? 'w-screen h-screen bg-[#000000]' : 'w-full md:w-[96%] xl:w-[60%] max-w-[1180px]'}
                                >
                                    <div className={`relative bg-[#000000] flex items-center justify-center overflow-hidden ${viewerFullscreen ? 'w-full h-full rounded-none border-0' : 'aspect-video rounded-lg border border-border-primary'}`}>
                                        {pollCamera && streamUrl ? (
                                            <img
                                                key={streamUrl}
                                                src={streamUrl}
                                                alt="BioXP Deck Live"
                                                className={`w-full h-full object-contain transition-opacity duration-200 ${streamReady ? 'opacity-100' : 'opacity-0'}`}
                                                onLoad={() => {
                                                    setStreamError(null);
                                                    setStreamReady(true);
                                                }}
                                                onError={() => {
                                                    const message = 'Live stream failed. Stop the stream and run Auto Recover if the camera stays busy.';
                                                    setStreamError(message);
                                                    recordCameraAction('stream', { ok: false, error: message, device: cameraDevice });
                                                    setStreamReady(false);
                                                    setSnapshot(null);
                                                    cameraStop.mutate({ device: cameraDevice });
                                                    setPollCamera(false);
                                                }}
                                            />
                                        ) : null}

                                        {!pollCamera && snapshot ? (
                                            <img src={`data:image/jpeg;base64,${snapshot}`} alt="BioXP Deck" className="w-full h-full object-contain" />
                                        ) : (
                                            <div className={`text-content-muted text-sm font-mono flex flex-col items-center gap-2 ${pollCamera && streamReady ? 'hidden' : ''}`}>
                                                <span className="text-2xl">CAM</span>
                                                <span>{cameraSnapshot.isPending ? 'CAPTURING FRAME...' : pollCamera ? 'OPENING STREAM...' : 'STREAM INACTIVE'}</span>
                                            </div>
                                        )}

                                        <div className="absolute top-4 left-4 flex flex-col gap-1 text-[10px] font-mono text-[#00ff00] bg-black/50 p-2 rounded">
                                            <div>CAM: {cameraDevice}</div>
                                            <div>STATUS: {pollCamera ? (streamReady ? `LIVE (${streamFps} FPS target)` : 'CONNECTING') : 'IDLE'}</div>
                                            {cameraSnapshot.data?.path && <div>PATH: {cameraSnapshot.data.path}</div>}
                                        </div>

                                        <div className={`absolute inset-y-4 left-4 rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3 overflow-y-auto ${viewerFullscreen ? 'flex w-72' : 'hidden md:flex w-64'}`}>
                                            <div className="w-full space-y-3">
                                                {cameraTransportPanel}
                                                {cameraSettingsPanel}
                                            </div>
                                        </div>
                                        <div className={`absolute inset-y-4 right-4 rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3 overflow-y-auto ${viewerFullscreen ? 'flex w-64' : 'hidden md:flex w-56'}`}>
                                            {cameraMotionPanel}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {!viewerFullscreen && (
                                <div className="grid grid-cols-1 gap-4 md:hidden">
                                    <div className="rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3">
                                        {cameraTransportPanel}
                                    </div>
                                    <div className="rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3">
                                        {cameraSettingsPanel}
                                    </div>
                                    <div className="rounded-xl border border-white/10 bg-[rgba(8,16,29,0.35)] backdrop-blur-sm p-3">
                                        {cameraMotionPanel}
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
                            <SectionCard title="Visible Camera Nodes" subtitle="Live device discovery from the robot runtime host.">
                                <JsonBlock title="Devices" data={cameraDevices.data} fallback="No camera devices reported yet." />
                            </SectionCard>
                            <SectionCard title="Control Enumeration" subtitle="V4L2 control enumeration for the selected device.">
                                <JsonBlock title="Controls" data={cameraControls.data} fallback="Control enumeration pending." />
                            </SectionCard>
                            <SectionCard title="Stream State" subtitle="Robot-local stream lock/busy/reset provenance for separating software recovery from hardware symptoms.">
                                <JsonBlock title="Stream State" data={cameraStreamState.data} fallback="Stream state pending." />
                            </SectionCard>
                            <SectionCard title="Latest Camera Result" subtitle="Most recent snapshot, health test, or auto-recovery response.">
                                <JsonBlock title="Camera Action" data={latestCameraResult} fallback="No camera action executed yet." />
                            </SectionCard>
                        </div>
                    </div>
                )
            )}
        </div>
    );
};
