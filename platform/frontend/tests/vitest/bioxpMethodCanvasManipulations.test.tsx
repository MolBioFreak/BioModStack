import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { methodManipulations, nativeChannelFootprint, nativeWellAddress, manipulationText, holderChannelFootprint } from '../../src/lib/bioxpMethodManipulations';
import type { MethodCompile, MethodValue } from '../../src/lib/bioxpMethods';
import { stageTimeline } from '../../src/lib/bioxpMethodTimeline';
const action = (kind: string, params: MethodValue, id = 'a'): MethodValue => ({ action_id: id, kind, params, metadata: { bms_method: { path: '/method/steps/0' } } });
const result = (actions: MethodValue[]): MethodCompile => ({ document: { stages: [{ actions }] }, issues: [], digest: null });
const move = (location_id: number, well: unknown) => ({ operation: 'position', positioning: { operation: 'move', location_id, well } });
const pickup = (keep_channels: number[]) => action('pipette_manual_physical', { operation: 'load_tip', tray: 2, well: 'A1', keep_channels }, 'pickup');
const recipe = (selected: number[]) => ({ channels: selected, before_leading_air: [move(3, 'A1')], leading_air: null, before_liquid: [], after_liquid: [], trailing_air: null,
    before_dispense: [move(1, 'A1')], dispense_segments: [{ volume_ul: '001.25' }], after_dispense: [], multi: null, final_empty_before: [], final_empty_speed_ul_s: null });
const subsets = Array.from({ length: 15 }, (_, i) => [0, 1, 2, 3].filter(c => ((i + 1) & (1 << c)) !== 0));

it.each(subsets.map(channels => [channels]))('projects selected channels %j without treating them as four or realigning from pickup', (selected) => {
    const request = result([pickup(selected), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: recipe(selected) }, 'transfer')]);
    const before = JSON.stringify(request), projected = methodManipulations(request, -1);
    expect(projected.events.find(e => e.operation === 'pick up tip')!.wells.map(w => w.well)).toEqual(['A1', 'C1', 'E1', 'G1']);
    const returned = projected.events.find(e => e.operation === 'return unused tip to rack');
    expect(returned?.wells.map(w => w.channel) ?? []).toEqual([0, 1, 2, 3].filter(c => !selected.includes(c)));
    expect(projected.links.map(l => l.channel)).toEqual(selected);
    expect(projected.links.map(l => [l.source.well, l.destination.well])).toEqual(selected.map(c => [`${'ACEG'[c]}1`, `${'ACEG'[c]}1`]));
    expect(JSON.stringify(request)).toBe(before);
});

it('uses the independently supplied reference, including a nonzero singleton and noncontiguous mask', () => {
    expect(nativeChannelFootprint(1, 'A1', [1, 3], 1).wells.map(w => w.well)).toEqual(['A1', 'E1']);
    const projected = methodManipulations(result([pickup([3]), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: recipe([3]) })]), 3);
    expect(projected.links.map(l => [l.source.well, l.destination.well])).toEqual([['A1', 'A1']]);
    expect(projected.events.find(e => e.operation === 'pick up tip')!.wells.map(w => w.well)).toEqual(['A1', 'C1', 'E1', 'G1']);
});

it('normalizes native row-major numeric and letter addresses without changing the raw action', () => {
    expect(nativeWellAddress('  c2 ')).toEqual({ row: 2, column: 1 });
    expect(nativeWellAddress(' 25 ')).toEqual({ row: 2, column: 1 });
    expect(nativeWellAddress(95)).toEqual({ row: 7, column: 11 });
    expect(nativeWellAddress(96)).toBeNull();
    expect(nativeChannelFootprint(7, 'A1', [1, 3], 3).wells.map(w => w.well)).toEqual(['C1', 'G1']);
    expect(nativeChannelFootprint(3, 'A1', [0, 3], 3).notes).toHaveLength(1);
    expect(nativeChannelFootprint(3, 'A1', [0, 3], 3).wells.map(w => w.well)).toEqual(['A1']);
});

it('keeps inherited alignment unknown after manual pickup rather than guessing from the retained mask', () => {
    const projected = methodManipulations(result([pickup([1]), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: recipe([1]) })]), null);
    expect(projected.events[0].wells).toHaveLength(4);
    expect(projected.links).toEqual([]);
    expect(manipulationText(projected)).toContain('Head reference is unknown');
});

it('shows loaded-tip lowering separately from selected strokes and keeps ejection at the actual XY', () => {
    const compiled = result([pickup([1, 3]), action('pipette_position', { operation: 'move', location_id: 3, well: 'A1' }),
        action('pipette_position', { operation: 'lower', location_id: 1 }), action('pipette_aspirate', { channels: [1], volume_ul: 5, speed: 10 }), action('tip_eject', {})]);
    const projected = methodManipulations(compiled, -1);
    const lowered = projected.events.find(e => e.operation === 'lower')!;
    expect(lowered.wells.map(w => [w.station, w.well])).toEqual([['LOC_RC', 'C1'], ['LOC_RC', 'G1']]);
    expect(projected.events.find(e => e.operation === 'aspirate')!.wells.map(w => w.well)).toEqual(['C1']);
    expect(projected.events.find(e => e.operation.startsWith('eject tips'))!.wells.map(w => w.well)).toEqual(['C1', 'G1']);
});

it('includes conditioning, every aliquot, excess, final empty and standalone in-place mixing', () => {
    const r = { ...recipe([0, 2]), before_dispense: [], after_dispense: [],
        multi: { conditioning_before: [move(3, 'B2')], conditioning_back_to_source_count: 1, conditioning_after: [], aliquots: [
            { before: [move(1, 'A2')], segments: [{ volume_ul: 1 }], after: [] },
            { before: [move(1, 'B3')], segments: [{ volume_ul: 1 }], after: [] },
        ], excess: { before: [move(11, 'A1')], segments: [{ volume_ul: 1 }], after: [] } },
        final_empty_before: [move(12, 'B1')], final_empty_speed_ul_s: 10 };
    const projected = methodManipulations(result([pickup([0, 2]), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: r }),
        action('pipette_aspirate', { channels: [2], volume_ul: 1, speed: 10 }), action('pipette_dispense', { channels: [2], volume_ul: 1, speed: 10 })]), -1);
    const wells = new Set(projected.events.flatMap(e => e.wells.map(w => `${w.station}:${w.well}`)));
    for (const address of ['LOC_RC:B2', 'LOC_RC:F2', 'LOC_OC:A2', 'LOC_OC:E2', 'LOC_OC:B3', 'LOC_OC:F3', 'LOC_STRIP1:A1', 'LOC_STRIP1:E1', 'LOC_STRIP2:B1', 'LOC_STRIP2:F1']) expect(wells.has(address), address).toBe(true);
    expect(projected.events.at(-1)!.wells.map(w => w.well)).toEqual(['F1']);
    expect(projected.links.some(l => l.source.station === l.destination.station && l.source.well === l.destination.well)).toBe(false);
});

it('keeps transport selection separate from canonical XY and does not project supplied masks for speedless native calls', () => {
    const native = result([action('pipette_tip', { action: 'load', metadata: { tip_location: 3 } }),
        action('pipette_position', { operation: 'move', location_id: 3, well: 'A1' }),
        action('pipette_aspirate', { channels: [0], volume_ul: 1 }),
        action('pipette_mix', { channels: [0, 1], volume_ul: 1, cycles: 2 })]);
    const projected = methodManipulations(native, -1);
    expect(projected.events.filter(e => e.wells.length).map(e => e.wells.map(w => w.well))).toEqual([['G1'], ['G1']]);
    expect(methodManipulations(result(native.document!.stages ? [action('pipette_aspirate', { channels: [0] })] : []), -1).events[0].wells).toEqual([]);
});

it('reports unknown geometry and conditional park state instead of reusing stale well locations', () => {
    const application = { operations: [move(3, 'A1'), { operation: 'position', positioning: { operation: 'move', location_id: 1, well: 'B1' }, rack_transform: { geometry: 'custom' } }, { operation: 'aspirate', channels: [0], volume_ul: 1 }] };
    const geometry = methodManipulations(result([pickup([0]), action('pipette_manual_physical', { operation: 'cavro_application', application })]), -1);
    expect(geometry.events.at(-1)!.wells).toEqual([]);
    expect(manipulationText(geometry)).toContain('Geometry-positioned XY');
    const parked = methodManipulations(result([pickup([1]), action('thermal_door', { door_command: 'DO' }), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: recipe([1]) })]), 1);
    expect(parked.links).toEqual([]);
    expect(manipulationText(parked)).toContain('Head reference is unknown');
});

it('projects shared-Z exposure for all loaded tips without attributing aspiration to inactive plungers', () => {
    const application = { operations: [move(3, 'A1'), { operation: 'surface_aspirate', channels: [1], volume_ul: 1 }, { operation: 'surface_move', channels: [1] }] };
    const projected = methodManipulations(result([pickup([0, 1, 3]), action('pipette_manual_physical', { operation: 'cavro_application', application })]), -1);
    expect(projected.events.find(e => e.operation === 'surface_aspirate')!.wells.map(w => w.channel)).toEqual([1]);
    expect(projected.events.filter(e => e.operation.includes('shared-Z')).map(e => e.wells.map(w => w.channel))).toEqual([[0, 1, 3], [0, 1, 3]]);
});

it.runIf(!!process.env.BIOXP_NATIVE_ADDRESSING_PROBE)('matches the retained pure native helper oracle across alignment values and channel subsets', () => {
    const oracle = JSON.parse(readFileSync(process.env.BIOXP_NATIVE_ADDRESSING_PROBE!, 'utf8'));
    const ids: Record<string, number> = { LOC_OC: 1, LOC_RC: 3, LOC_STRIP1: 11, TECANRACK1: 7 };
    const cases = oracle.cases.filter((c: any) => c.location in ids);
    expect(new Set(cases.map((c: any) => c.location))).toEqual(new Set(Object.keys(ids)));
    for (const c of cases) for (const subset of c.subsets) {
        const expected = subset.well_labels.filter((w: string) => /^[A-H]1$/.test(w));
        expect(nativeChannelFootprint(ids[c.location], c.reference, subset.channels, c.motion_alignment).wells.map(w => w.well)).toEqual(expected);
    }
});

it.runIf(!!process.env.BIOXP_WELL_COMPILED)('receives the actual saved workflow with every barcode and pooling well represented', () => {
    const compiled = JSON.parse(readFileSync(process.env.BIOXP_WELL_COMPILED!, 'utf8'));
    const projected = methodManipulations(compiled, -1);
    const ids = (steps: string[]) => new Set(compiled.provenance.filter((p: any) => steps.includes(p.step_id)).flatMap((p: any) => p.native_action_ids));
    const barcode = ids(['s021', 's028', 's035', 's042', 's049', 's056']);
    const links = projected.links.filter(l => barcode.has(l.actionId));
    expect(links).toHaveLength(24);
    expect(new Set(links.map(l => l.source.well)).size).toBe(24);
    expect(new Set(links.map(l => l.destination.well)).size).toBe(24);
    const pools = projected.links.filter(l => ids(['s071', 's074', 's077']).has(l.actionId));
    expect(pools).toHaveLength(24);
    expect(new Set(pools.map(l => l.channel))).toEqual(new Set([0]));
    expect(new Set(pools.map(l => l.destination.well))).toEqual(new Set(['A1', 'B1', 'C1']));
    // Every eject follows a move to the waste bin and names only the channels holding tips.
    const ejects = projected.events.filter(e => e.operation.startsWith('eject tips'));
    expect(ejects).toHaveLength(15);
    expect(projected.events.filter(e => e.operation === 'ejection position' || e.operation === 'ejection selection')).toEqual([]);
    const actions = compiled.document.stages.flatMap((s: any) => s.actions);
    actions.forEach((a: any, i: number) => { if (a.kind === 'tip_eject') expect(actions[i - 1].params).toMatchObject({ operation: 'move', location_id: 6 }); });
    expect(projected.events.flatMap(e => e.notes)).toEqual([]);
});

it('projects bulk-holder targets and the waste bin as link endpoints', () => {
    const holderMove = { operation: 'position', positioning: { operation: 'move', target: 'LOC_TROUGH_MICRO_ROW_1', camera_offset: false } };
    const r = { ...recipe([2]), before_leading_air: [holderMove], before_dispense: [move(1, 'A5')] };
    const projected = methodManipulations(result([pickup([2]), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: r }, 'axp')]), -1);
    expect(projected.links.map(l => [l.source.station, l.source.well, l.destination.station, l.destination.well])).toEqual([['LOC_TROUGH_MICRO_ROW_1', '3', 'LOC_OC', 'E5']]);
    const waste = { ...recipe([1]), before_leading_air: [move(0, 'B5')], before_dispense: [move(6, 'A1')] };
    const toWaste = methodManipulations(result([pickup([1]), action('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: waste }, 'sup')]), -1);
    expect(toWaste.links.map(l => [l.source.station, l.source.well, l.destination.station])).toEqual([['LOC_MS', 'D5', 'WASTE_BIN']]);
    expect(holderChannelFootprint('LOC_TROUGH_PCR_ROW_2_WELL_2', [0, 1, 3]).wells.map(w => w.station)).toEqual(['LOC_TROUGH_PCR_ROW_2_WELL_2', 'LOC_TROUGH_PCR_ROW_2_WELL_4', 'LOC_TROUGH_PCR_ROW_2_WELL_8']);
});


const tla = (kind: string, params: MethodValue, id = kind): MethodValue => ({ action_id: id, kind, params, metadata: { bms_method: { path: '/method/steps/0' } } });
const tlMove = (location_id: number, well: string) => ({ operation: 'position', positioning: { operation: 'move', location_id, well } });
const tlHolder = { operation: 'position', positioning: { operation: 'move', target: 'LOC_TROUGH_MICRO_ROW_1', camera_offset: false } };
const tlXfer = (from: MethodValue, to: MethodValue, vol: string, ch = 3) => tla('pipette_manual_physical', { operation: 'cavro_liquid_recipe', recipe: { channels: [ch], target_liquid_ul: vol, before_leading_air: [from], before_dispense: [to] } });

it('returns null for pages without waits, holds or pauses', () => {
    expect(stageTimeline('x', [tla('plate_move', { plate_id: 'PL_OUTPUT', target_location: 'LOC_MS' })], [], () => '', () => undefined)).toBeNull();
});

it('builds cycler holds, ramps, door state and a marker from a thermal profile', () => {
    const t = stageTimeline('Incubate', [tla('thermal_profile', { repeat: 1, segments: [
        { target_temp_c: 30, duration_s: 120, start: 'attainment' }, { target_temp_c: 80, duration_s: 120, start: 'attainment' }] })],
        [tla('thermal_door', { door_command: 'DC' })], () => '', () => undefined)!;
    expect(t.lanes[0].blocks.map(b => b.title)).toEqual(['Ramp', 'Hold 30 °C', 'Ramp', 'Hold 80 °C']);
    expect(t.lanes[1].blocks[0].title).toBe('Closed throughout · heated lid down');
    expect(t.total).toBe('4:00 of holds, plus ramp time');
    expect(t.markers[0]).toMatchObject({ station: 'LOC_TC', title: '30 °C 2:00 → 80 °C 2:00 · lid down' });
});

it('groups reagent draws per destination, labels the bind wait and marks the wells', () => {
    const actions = (['E5', 'H5']).flatMap(w => [tlXfer(tlHolder, tlMove(1, w === 'E5' ? 'A5' : 'B5'), '27'), tlXfer(tlHolder, tlMove(1, w === 'E5' ? 'A5' : 'B5'), '27'), tla('pipette_aspirate', {})]);
    const t = stageTimeline('Bind', [...actions, tla('wait', { seconds: 600 }, 'w')], [tla('chiller_setpoint', { bank: 'oc', target_temp_c: 22 })],
        () => 'Bind DNA 10 min at room temperature', w => w.station.startsWith('LOC_TROUGH') ? 'AXP' : undefined)!;
    const liquid = t.lanes.find(l => l.id === 'liquid')!.blocks;
    expect(liquid[0]).toMatchObject({ title: 'AXP → G5', detail: '2 × 27 µL + mix' });
    expect(liquid.find(b => b.tone === 'wait')).toMatchObject({ title: 'Bind', detail: '10:00 · room temp' });
    expect(t.markers.find(m => m.tone === 'wait')).toMatchObject({ title: 'Bind 10:00 at RT', wells: ['G5', 'H5'] });
});
