import { expect, it } from 'vitest';
import type { BioXpOperatorUpdates } from '../../src/lib/bioxpClient';
import type { CalibrationSettings, MotionPosition } from '../../src/lib/bioxpCalibration';
import { activeDeckResources, alignmentReference, gunCenters, retainAxisObservations, poseFromAxisObservations } from '../../src/lib/bioxpLiveDeck';

function frame(location_id = 'LOC_MS', inc_factor = 1): MotionPosition {
    return { location_id, inc_factor, base_coordinates: { x: 50000, y: 12000 }, z_low: 100, z_high: 50, z_delta: 10 };
}
function update(axes: NonNullable<BioXpOperatorUpdates['pose']>['axes'] = [
    { axis: 'x', position_steps: 0, observed_at: 0 }, { axis: 'y', position_steps: -20, observed_at: 100 },
]): BioXpOperatorUpdates {
    return { schema_version: 'bioxp.operator_updates.v1', ownership_generation: 7, source_instance_id: 'fixture',
        next_after_sequence: 0, pose_sequence: 1, active_command_ids: [], changed_command_ids: [], has_more: false, reset: false,
        pose: { ownership_generation: 7, axes } };
}
it('projects source -X/+Y with equal scale and four fixed gun centers irrespective of utility factors', () => {
    const centers = gunCenters(50000, 12000);
    expect(centers).toHaveLength(4);
    for (const [i, gun] of centers.entries()) {
        expect(gun.point[0]).toBeCloseTo(60 + 2 * (119541.50999505838 - 50000) / 236.94, 10);
        expect(gun.point[1]).toBeCloseTo(70 + 2 * (12000 + 4264 * i) / 236.94, 10);
    }
    const utility = frame('WASTE_BIN', 0);
    expect(alignmentReference(50000, 12000, 3, utility)).toEqual(centers[0].point);
    expect(centers[3].point[1] - centers[0].point[1]).toBeCloseTo(2 * 12792 / 236.94);
});
it('uses active loader-adjusted geometry, never saved/restart-pending rows, across every well', () => {
    const settings = { active_motion_positions: [frame('LOC_MS', 2), frame('LOC_STRIP1', 1)],
        saved_motion_positions: [{ ...frame(), base_coordinates: { x: 1, y: 2 } }], pending_restart: true } as unknown as CalibrationSettings;
    const { resources, calibrated } = activeDeckResources(settings);
    expect(calibrated).toEqual(['LOC_MS', 'LOC_STRIP1']);
    expect(resources.reduce((sum, r) => sum + r.points.length, 0)).toBe(800);
    for (const name of calibrated) {
        const row = settings.active_motion_positions.find(p => p.location_id === name)!;
        for (const well of resources.find(p => p.id === name)!.points) {
            expect(well.x).toBeCloseTo(60 + 2 * (119541.50999505838 - (50000 - 2132 * row.inc_factor * well.column)) / 236.94);
            expect(well.y).toBeCloseTo(70 + 2 * (12000 + 2132 * row.inc_factor * well.row) / 236.94);
        }
    }
    expect(resources.find(r => r.id === 'LOC_STRIP1')!.points.map(p => p.well)).toEqual(['A1','B1','C1','D1','E1','F1','G1','H1']);
    expect(activeDeckResources().calibrated).toEqual([]);
});
it('keeps alignment compensation separate from fixed mechanical spacing and requires known frame', () => {
    for (const tip of [0, 1, 2, 3]) expect(alignmentReference(50000, 12000, tip, frame('LOC_MS', 2))![1]).toBeCloseTo(70 + 2 * (12000 + 4264 * 2 * tip) / 236.94);
    for (const id of ['TECANRACK1','TECANRACK2','TECANRACK3','TECANRACK4','WASTE_BIN','LOC_TROUGH']) expect(alignmentReference(50000, 12000, 3, frame(id))![1]).toBeCloseTo(70 + 24000 / 236.94);
    expect(alignmentReference(50000, 12000, -1, frame())![1]).toBeCloseTo(70 + 24000 / 236.94);
    for (const tip of [null, undefined, -2, 4, 1.5]) expect(alignmentReference(0, 0, tip, frame())).toBeNull();
    expect(alignmentReference(0, 0, 0)).toBeNull();
});
it('preserves zero/negative raw gantry, epoch-zero axis time and absent Z without catalog-derived freshness', () => {
    expect(poseFromAxisObservations(retainAxisObservations(null, update(), 2))).toMatchObject({ x: 0, y: -20, z: null, observedAt: 0 });
    expect(poseFromAxisObservations(retainAxisObservations(null, update([]), 2))).toBeNull();
});
it('retains partial axes without inventing a complete pose and ignores malformed scalar observations', () => {
    const partial = retainAxisObservations(null, update([{ axis: 'x', position_steps: 0, observed_at: 0 }]), 2);
    expect(poseFromAxisObservations(partial)).toBeNull();
    const complete = retainAxisObservations(partial, update([{ axis: 'y', position_steps: 4, observed_at: 1 }]), 2);
    expect(poseFromAxisObservations(complete)).toMatchObject({ x: 0, y: 4 });
    const invalid = retainAxisObservations(complete, update([{ axis: 'x', position_steps: NaN, observed_at: 5 }, { axis: 'y', position_steps: 8, observed_at: Infinity }]), 2);
    expect(invalid?.axes).toEqual(complete?.axes);
});
it('retains same-owner observations but clears across connection, ownership and source replacement', () => {
    const pose = retainAxisObservations(null, update(), 2);
    expect(retainAxisObservations(pose, undefined, 2)).toBe(pose);
    expect(retainAxisObservations(pose, undefined, 3)).toBeNull();
    expect(retainAxisObservations(pose, { ...update(), pose: null }, 2)?.axes).toEqual(pose?.axes);
    expect(retainAxisObservations(pose, { ...update(), ownership_generation: 8 }, 2)?.axes).toEqual([]);
    expect(retainAxisObservations(pose, { ...update(), source_instance_id: 'new', pose: null }, 2)?.axes).toEqual([]);
});
it('a newer X observation changes X without renewing Y time, even when the minimum timestamp remains unchanged', () => {
    const original = retainAxisObservations(null, update(), 2);
    const next = retainAxisObservations(original, update([{ axis: 'x', position_steps: 44, observed_at: 101 }]), 2);
    expect(poseFromAxisObservations(next)).toMatchObject({ x: 44, y: -20, observedAt: 100 });
    expect(next?.axes.find(axis => axis.axis === 'y')?.observed_at).toBe(100);
    const older = retainAxisObservations(next, update([{ axis: 'x', position_steps: 99, observed_at: 99 }]), 2);
    expect(poseFromAxisObservations(older)?.x).toBe(44);
});
