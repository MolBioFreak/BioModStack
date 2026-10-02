import { expect, it } from 'vitest';
import type { BioXpOperatorDashboardV2 } from '../../src/lib/bioxpClient';
import type { CalibrationSettings, MotionPosition } from '../../src/lib/bioxpCalibration';
import { activeDeckResources, alignmentReference, gunCenters, poseIsLastKnown, readReportedPose, retainReportedPose } from '../../src/lib/bioxpLiveDeck';

function frame(location_id = 'LOC_MS', inc_factor = 1): MotionPosition {
    return { location_id, inc_factor, base_coordinates: { x: 50000, y: 12000 }, z_low: 100, z_high: 50, z_delta: 10 };
}
function dashboard(x: number | null = 0, y: number | null = -20, observedAt: number | null = 100): BioXpOperatorDashboardV2 {
    return { ownership_generation: 7, generated_at: 99999, telemetry: { axes: [
        { axis: 'x', position_steps: x, reference: 'referenced' }, { axis: 'y', position_steps: y, reference: 'unknown' }, { axis: 'z', position_steps: null }],
        snapshot: { snapshot_id: 'fixture', observed_at: 50, domain_observed_at: { axes: observedAt, pipette: 10 }, freshness: { state: 'fresh', fresh_for_s: 30 } } } } as unknown as BioXpOperatorDashboardV2;
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
it('preserves zero/negative raw gantry, absent Z, axes-domain source time and reference uncertainty', () => {
    const pose = readReportedPose(dashboard(), 2)!;
    expect(pose).toMatchObject({ x: 0, y: -20, z: null, observedAt: 100, reference: 'X referenced; Y unknown' });
    expect(readReportedPose(dashboard(null), 2)).toBeNull();
    expect(readReportedPose(dashboard(1, null), 2)).toBeNull();
    expect(readReportedPose(dashboard(1, 2, null), 2)!.observedAt).toBeNull();
});
it('keeps unavailable partial display observations nonfatal without inventing pose or freshness', () => {
    const sparse = { ownership_generation: 7, telemetry: {} } as unknown as BioXpOperatorDashboardV2;
    expect(readReportedPose(sparse, 2)).toBeNull();
    const d = dashboard();
    const withoutTime = { ...d, telemetry: { axes: d.telemetry!.axes } } as BioXpOperatorDashboardV2;
    const pose = readReportedPose(withoutTime, 2)!;
    expect(pose).toMatchObject({ x: 0, y: -20, observedAt: null, snapshotId: null });
    expect(poseIsLastKnown(pose, withoutTime, false, true, 105)).toBe(true);
    const withoutFreshness = { ...d, telemetry: { ...d.telemetry, snapshot: { observed_at: 100 } } } as BioXpOperatorDashboardV2;
    expect(poseIsLastKnown(readReportedPose(withoutFreshness, 2)!, withoutFreshness, false, true, 105)).toBe(true);
    expect(activeDeckResources({} as CalibrationSettings).calibrated).toEqual([]);
});
it('retains last complete same-connection pose on missing/older samples but clears across generation/ownership', () => {
    const pose = readReportedPose(dashboard(), 2)!;
    expect(retainReportedPose(pose, null, 2, 7)).toBe(pose);
    expect(retainReportedPose(pose, readReportedPose(dashboard(99, 88, 100), 2), 2, 7)).toBe(pose);
    expect(retainReportedPose(pose, readReportedPose(dashboard(99, 88, 90), 2), 2, 7)).toBe(pose);
    expect(retainReportedPose(pose, readReportedPose(dashboard(99, 88, null), 2), 2, 7)).toBe(pose);
    expect(retainReportedPose(pose, null, 3, 7)).toBeNull();
    expect(retainReportedPose(pose, null, 2, 8)).toBeNull();
});
it('does not freshen aged source samples from a new HTTP/generated timestamp', () => {
    const d = dashboard(), pose = readReportedPose(d, 2)!;
    expect(poseIsLastKnown(pose, d, false, true, 105)).toBe(false);
    expect(poseIsLastKnown(pose, { ...d, generated_at: 200 }, false, true, 140)).toBe(true);
    expect(poseIsLastKnown(pose, d, true, true, 105)).toBe(true);
    expect(poseIsLastKnown(pose, d, false, false, 105)).toBe(true);
    expect(poseIsLastKnown(pose, dashboard(null), false, true, 105)).toBe(true);
    d.telemetry!.snapshot.clock_skew_detected = true;
    expect(poseIsLastKnown(pose, d, false, true, 105)).toBe(true);
});
