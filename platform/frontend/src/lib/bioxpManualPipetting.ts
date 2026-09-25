// Typed authoring over the robot's ordinary native protocol owner. No geometry,
// source tip-state changes, scientific presets or implicit lifecycle steps here.
export type BioXpManualPosition =
    | { operation: 'move'; location_id: number; well: string | number; position_flag: 0 | 1 | 2 }
    | { operation: 'lower'; location_id: number }
    | { operation: 'lift'; location_id: number; height_steps: number | null };
export type BioXpManualStep = BioXpManualPosition
    | { operation: 'load_tip'; tray: number; well: string; overpress: boolean; lift_z: boolean }
    | { operation: 'measure_fluid_height'; speed: number }
    | { operation: 'source_fluid_offset'; plate: 'TC' | 'MS' | 'OC' | 'RC' | 'STRIP' | 'OCMS'; speed: number; transfer_fluid: boolean; skip_steps: number }
    | { operation: 'diagnostic_detect_fluid' }
    | { operation: 'source_calwith_fluid' }
    | { operation: 'aspirate' | 'dispense'; channels: number[]; volume_ul: number; speed: number }
    | { operation: 'mix'; channels: number[]; volume_ul: number; aspirate_speed: number; dispense_speed: number; cycles: number };
export type BioXpManualRequest = { protocol_id: string; steps: BioXpManualStep[] };
export type BioXpManualAction = {
    action_id: string; stage_id: 'manual';
    kind: 'pipette_manual_physical' | 'pipette_position' | 'pipette_aspirate' | 'pipette_dispense';
    params: Record<string, unknown>; metadata: { manual_step: number };
};

export function manualPipettingDocument({ protocol_id, steps }: BioXpManualRequest) {
    if (!protocol_id.trim() || !steps.length) throw new Error('Provide a protocol ID and at least one explicit step.');
    const actions: BioXpManualAction[] = [];
    const add = (kind: BioXpManualAction['kind'], params: Record<string, unknown>, step: number) => {
        actions.push({ action_id: `manual-${step}-${actions.length}`, stage_id: 'manual', kind, params, metadata: { manual_step: step } });
    };
    const liquid = (operation: 'aspirate' | 'dispense', channels: number[], volume_ul: number, speed: number, index: number) => {
        if (!channels.length || channels.some(c => !Number.isInteger(c) || c < 0 || c > 3) || new Set(channels).size !== channels.length)
            throw new Error('Select one or more distinct plunger channels (0–3).');
        if (!Number.isFinite(volume_ul) || !Number.isFinite(speed)) throw new Error('Enter finite volume and speed.');
        // Minimal native command; the robot owns command validation and optional
        // field defaults. No source/destination metadata pretending to move XY.
        add(operation === 'aspirate' ? 'pipette_aspirate' : 'pipette_dispense', { channels: [...channels], volume_ul, speed }, index);
    };
    steps.forEach((step, index) => {
        if (step.operation === 'load_tip' || step.operation === 'measure_fluid_height' || step.operation === 'source_fluid_offset' || step.operation === 'diagnostic_detect_fluid' || step.operation === 'source_calwith_fluid') {
            if (step.operation === 'load_tip' && (!Number.isInteger(step.tray) || step.tray < 1 || step.tray > 5 || !/^[AB](?:[1-9]|1[0-2])$/.test(step.well)))
                throw new Error('Select tip tray 1–5 and tip well A1–B12.');
            if (step.operation === 'measure_fluid_height' && !Number.isSafeInteger(step.speed)) throw new Error('Enter integer detection speed.');
            if (step.operation === 'source_fluid_offset' && (!['TC', 'MS', 'OC', 'RC', 'STRIP', 'OCMS'].includes(step.plate)
                || !Number.isSafeInteger(step.speed) || !Number.isSafeInteger(step.skip_steps) || step.skip_steps < 1
                || typeof step.transfer_fluid !== 'boolean')) throw new Error('Select an OEM scan plate, integer speed and positive sample spacing.');
            add('pipette_manual_physical', { ...step }, index);
        } else if ('location_id' in step) {
            if (!Number.isSafeInteger(step.location_id)) throw new Error('Enter the canonical integer locationID.');
            if (step.operation === 'move') {
                if (!(typeof step.well === 'string' ? /^[A-H](?:[1-9]|1[0-2])$/.test(step.well) : Number.isInteger(step.well) && step.well >= 0 && step.well < 96))
                    throw new Error('Select a well A1–H12 or index 0–95.');
                if (![0, 1, 2].includes(step.position_flag)) throw new Error('Select a source positioning flag.');
            }
            if (step.operation === 'lift' && step.height_steps !== null && !Number.isSafeInteger(step.height_steps))
                throw new Error('Lift height must be integer steps or calibrated high.');
            add('pipette_position', { ...step }, index);
        } else if (step.operation === 'mix') {
            if (!Number.isInteger(step.cycles) || step.cycles < 1 || step.cycles > 50) throw new Error('Mix cycles must be 1–50.');
            for (let cycle = 0; cycle < step.cycles; cycle++) {
                liquid('aspirate', step.channels, step.volume_ul, step.aspirate_speed, index);
                liquid('dispense', step.channels, step.volume_ul, step.dispense_speed, index);
            }
        } else liquid(step.operation, step.channels, step.volume_ul, step.speed, index);
    });
    return { protocol_id, version: 1, stages: [{ stage_id: 'manual', title: 'Manual pipetting', actions }],
        metadata: { manual_scope: 'explicit_steps_only', well_alignment: 'source_machine_tip_location' } };
}

export function describeManualStep(step: BioXpManualStep): string {
    if (step.operation === 'load_tip') return `Load tip · tray ${step.tray} · ${step.well} · overpress ${step.overpress} · lift Z ${step.lift_z}`;
    if (step.operation === 'measure_fluid_height') return `Measure fluid height · current location · speed ${step.speed}`;
    if (step.operation === 'source_fluid_offset') return `OEM fluid offset scan · ${step.plate} · speed ${step.speed} · ${step.transfer_fluid ? 'prefill' : 'scan only'} · every ${step.skip_steps} well(s)`;
    if (step.operation === 'diagnostic_detect_fluid') return 'OEM Detect Fluid · five stations · no calibration save';
    if (step.operation === 'source_calwith_fluid') return 'OEM calibrate with fluid · five stations · saves each offset';
    if (step.operation === 'move') return `Move · locationID ${step.location_id} · ${step.well} · flag ${step.position_flag}`;
    if (step.operation === 'lower') return `Lower in place · locationID ${step.location_id} · calibrated zLow`;
    if (step.operation === 'lift') return `Lift in place · locationID ${step.location_id} · ${step.height_steps === null ? 'calibrated zHigh' : `zLow − ${step.height_steps} steps`}`;
    return `${step.operation} in place · plungers ${step.channels.map(c => c + 1).join(', ')} · ${step.volume_ul} µL · ${step.operation === 'mix' ? `${step.cycles} cycles · speeds ${step.aspirate_speed}/${step.dispense_speed}` : `speed ${step.speed}`}`;
}
