import { api } from './api';
import type { PipetteResult } from './bioxpPipetteResults';

export type CalibrationSnapshot = {
    positions: ({ name: string } & CalibrationValues)[];
    liquid_calibration: Record<string, unknown>;
    revision_id: string | null;
};
export type CalibrationRun = PipetteResult & {
    run_id: string; before: CalibrationSnapshot; after: CalibrationSnapshot;
    decision: 'accept' | 'restore' | null; body_completed: boolean;
    measurements: Record<string, unknown>[];
    saved_revision_id: string | null; active_revision_id: string | null;
};
export async function readCalibrationRun(generation: number, runId: string) {
    return (await api.get<CalibrationRun>(`/api/bioxp/calibration-settings/runs/${encodeURIComponent(runId)}`,
        { params: { expected_connection_generation: generation } })).data;
}
export async function decideCalibrationRun(generation: number, runId: string, decision: 'accept' | 'restore') {
    return (await api.post<CalibrationRun>(`/api/bioxp/calibration-settings/runs/${encodeURIComponent(runId)}/decision`,
        { expected_connection_generation: generation, decision })).data;
}

// Wire mirror of oem_calibration_settings.PositionCalibrationPatch.
export const calibrationFields = ['x', 'y', 'zLow', 'zDelta', 'inc_factor'] as const;
export type CalibrationField = typeof calibrationFields[number];
export type CalibrationValues = Record<CalibrationField, number>;
export type CalibrationRow = CalibrationValues & { name: string; zHigh: number; source?: string; calibration_revision_id?: string };
export type MotionPosition = { location_id: string; base_coordinates: Record<string, number>; z_low: number | null; z_high: number | null; z_delta: number | null; inc_factor: number };
export type LoaderAdjustment = { location_id: string; kind: string; source_anchor: string };
export type CalibrationSettings = {
    schema_version: 'bioxp.calibration_settings.v1'; baseline_lock_sha256: string;
    active_revision_id: string | null; saved_revision_id: string | null; committed_revision_id?: string;
    pending_restart: boolean; application_status: 'pending_restart' | 'bound_configuration'; application_semantics: string;
    active_positions: CalibrationRow[]; saved_positions: CalibrationRow[];
    active_motion_positions: MotionPosition[]; saved_motion_positions: MotionPosition[];
    active_loader_adjustments: LoaderAdjustment[]; saved_loader_adjustments: LoaderAdjustment[];
    physical_calibration_verified: false; motion_commanded: false;
};
export type CalibrationPatch = { name: string } & Partial<CalibrationValues>;
export async function readCalibrationSettings(generation: number) {
    return (await api.get<CalibrationSettings>('/api/bioxp/calibration-settings', { params: { expected_connection_generation: generation } })).data;
}
export async function saveCalibrationSettings(generation: number, positions: CalibrationPatch[]) {
    return (await api.patch<CalibrationSettings>('/api/bioxp/calibration-settings', { expected_connection_generation: generation, positions })).data;
}
