import type { CalibrationSettings, MotionPosition } from './bioxpCalibration';
import type { BioXpOperatorUpdates } from './bioxpClient';

export type AxisObservations = {
    generation: number; source: string; ownershipGeneration: number;
    axes: NonNullable<BioXpOperatorUpdates['pose']>['axes'];
};
export function retainAxisObservations(previous: AxisObservations | null, update: BioXpOperatorUpdates | undefined, generation: number): AxisObservations | null {
    if (!update) return previous?.generation === generation ? previous : null;
    const same = previous?.generation === generation && previous.source === update.source_instance_id
        && previous.ownershipGeneration === update.ownership_generation;
    const axes = new Map((same ? previous.axes : []).map(axis => [axis.axis, axis]));
    if (update.pose?.ownership_generation === update.ownership_generation) {
        for (const axis of update.pose.axes) {
            if (!['x', 'y', 'z'].includes(axis.axis) || !Number.isFinite(axis.position_steps) || !Number.isFinite(axis.observed_at)) continue;
            const old = axes.get(axis.axis);
            if (!old || axis.observed_at >= old.observed_at) axes.set(axis.axis, axis);
        }
    }
    return { generation, source: update.source_instance_id, ownershipGeneration: update.ownership_generation, axes: [...axes.values()] };
}
export function poseFromAxisObservations(observation: AxisObservations | null): ReportedPose | null {
    const x = observation?.axes.find(axis => axis.axis === 'x');
    const y = observation?.axes.find(axis => axis.axis === 'y');
    const z = observation?.axes.find(axis => axis.axis === 'z');
    if (!observation || !x || !y) return null;
    return { generation: observation.generation, ownershipGeneration: observation.ownershipGeneration,
        x: x.position_steps, y: y.position_steps, z: z?.position_steps ?? null,
        observedAt: Math.min(x.observed_at, y.observed_at), reference: 'Controller position readback', snapshotId: null };
}
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
