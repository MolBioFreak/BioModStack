import type { CalibrationSettings, MotionPosition } from './bioxpCalibration';
import type { BioXpOperatorDashboardV2 } from './bioxpClient';
import { deckBounds, deckResources, projectDeckPoint, type DeckResource } from './bioxpWorkflowDeck';

/** Mechanical pitch, not a location's addressing factor. OEM four-head line: +Y. */
export const GUN_SPACING_STEPS = 4264;
export function gunCenters(x: number, y: number) {
    return Array.from({ length: 4 }, (_, channel) => ({ channel, point: projectDeckPoint(x, y + channel * GUN_SPACING_STEPS) }));
}
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export function activeDeckResources(settings?: CalibrationSettings): { resources: DeckResource[]; calibrated: string[] } {
    const calibrated: string[] = [];
    const resources = deckResources.map(resource => {
        const row = settings?.active_motion_positions?.find(p => p.location_id === resource.id);
        if (!row || !finite(row.base_coordinates?.x) || !finite(row.base_coordinates?.y) || !finite(row.inc_factor)) return resource;
        calibrated.push(resource.id);
        const points = resource.points.map(well => {
            const [x, y] = projectDeckPoint(row.base_coordinates.x - 2132 * row.inc_factor * well.column,
                row.base_coordinates.y + 2132 * row.inc_factor * well.row);
            return { ...well, x, y };
        });
        return { ...resource, points, bounds: deckBounds(points) };
    });
    return { resources, calibrated };
}
const exempt = new Set(['TECANRACK1', 'TECANRACK2', 'TECANRACK3', 'TECANRACK4', 'WASTE_BIN', 'LOC_TROUGH']);
/** Inverse script addressing only; NOT a measured tip/contact or gun-body transform. */
export function alignmentReference(x: number, y: number, tip: number | null | undefined, frame?: MotionPosition) {
    if (!frame || !finite(frame.inc_factor) || !finite(tip) || !Number.isInteger(tip) || tip < -1 || tip > 3) return null;
    const offset = tip === -1 || exempt.has(frame.location_id) ? 0 : GUN_SPACING_STEPS * frame.inc_factor * tip;
    return projectDeckPoint(x, y + offset);
}
export type ReportedPose = {
    generation: number; ownershipGeneration: number; x: number; y: number; z: number | null;
    observedAt: number | null; reference: string; snapshotId: string | null;
};
export function readReportedPose(dashboard: BioXpOperatorDashboardV2 | null | undefined, generation: number): ReportedPose | null {
    const telemetry = dashboard?.telemetry;
    if (!dashboard || !telemetry || !Array.isArray(telemetry.axes)) return null;
    const axis = (name: string) => telemetry.axes.find(a => a.axis.toLowerCase() === name);
    const x = axis('x'), y = axis('y'), z = axis('z');
    if (!finite(x?.position_steps) || !finite(y?.position_steps)) return null;
    const snapshot = telemetry.snapshot;
    // The native axes domain owns all rows. Snapshot time is an older-server fallback,
    // never dashboard.generated_at (which is response assembly time).
    const time = snapshot?.domain_observed_at && 'axes' in snapshot.domain_observed_at
        ? snapshot.domain_observed_at.axes : snapshot?.observed_at;
    return { generation, ownershipGeneration: dashboard.ownership_generation, x: x.position_steps, y: y.position_steps,
        z: finite(z?.position_steps) ? z.position_steps : null, observedAt: finite(time) ? time : null,
        reference: `X ${x.reference}; Y ${y.reference}`, snapshotId: snapshot?.snapshot_id ?? null };
}
export function retainReportedPose(previous: ReportedPose | null, incoming: ReportedPose | null, generation: number, ownershipGeneration?: number): ReportedPose | null {
    const retained = previous?.generation === generation && (ownershipGeneration === undefined || previous.ownershipGeneration === ownershipGeneration) ? previous : null;
    if (!incoming || incoming.generation !== generation) return retained;
    if (retained?.observedAt !== null && retained?.observedAt !== undefined && (incoming.observedAt === null || incoming.observedAt <= retained.observedAt)) return retained;
    return incoming;
}
export function poseIsLastKnown(pose: ReportedPose, dashboard: BioXpOperatorDashboardV2 | null | undefined, stale: boolean, connected: boolean, nowSeconds: number) {
    const snapshot = dashboard?.telemetry?.snapshot;
    const incoming = readReportedPose(dashboard, pose.generation);
    return !connected || stale || !incoming || incoming.observedAt !== pose.observedAt || !snapshot ||
        snapshot.clock_skew_detected === true || snapshot.freshness?.state !== 'fresh' || pose.observedAt === null ||
        pose.observedAt > nowSeconds || (finite(snapshot.freshness.fresh_for_s) && nowSeconds - pose.observedAt >= snapshot.freshness.fresh_for_s);
}
