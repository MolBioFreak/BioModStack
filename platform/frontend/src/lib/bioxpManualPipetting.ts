// Typed authoring over the robot's ordinary native protocol owner. No geometry,
// source tip-state changes, scientific presets or implicit lifecycle steps here.
export type BioXpManualPosition =
    | { operation: 'move'; location_id: number; well: string | number; position_flag: 0 | 1 | 2 }
    | { operation: 'lower'; location_id: number }
    | { operation: 'lift'; location_id: number; height_steps: number | null };
export type BioXpDiagnostic =
    | { action: 'aspirate' | 'dispense'; channels: number[]; volume_ul: number; speed: number }
    | { action: 'eject'; channels: number[] }
    | { action: 'plunger_up' | 'plunger_down'; steps: number }
    | { action: 'dispense_all' | 'diagnoses' | 'initialize' | 'get_data' | 'last_error' };
export type BioXpSourceStep =
    | { operation: 'source_load_tips'; tip_type: 50 | 200; pipette: number; force_new_tip: boolean }
    | { operation: 'source_mix'; volume_ul: number; air_ul: number; aspirate_speed: number; dispense_speed: number;
        aspirate_delay_ms: number | null; dispense_delay_ms: number | null; cycles: number; mix_type: 'N' | 'H' | 'C'; tip_dip: boolean }
    | { operation: 'source_aspirate_air' | 'source_dispense_air'; volume_ul: number }
    | { operation: 'source_purge'; speed: number; amp: boolean; ntd: boolean }
    | { operation: 'diagnostic_pipette'; diagnostic: BioXpDiagnostic };
export function isSourceStep(step: BioXpManualStep): step is BioXpSourceStep {
    return ['source_load_tips', 'source_mix', 'source_aspirate_air', 'source_dispense_air', 'source_purge', 'diagnostic_pipette'].includes(step.operation);
}
export type BioXpManualStep = BioXpManualPosition | BioXpSourceStep
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
        if (isSourceStep(step)) {
            for (const value of Object.values(step)) if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('Enter finite source values.');
            if (step.operation === 'source_load_tips' && (![50, 200].includes(step.tip_type) || ![-1, 0, 1, 2, 3].includes(step.pipette))) throw new Error('Select source tip type and pipette.');
            if (step.operation === 'source_mix' && (!Number.isInteger(step.cycles) || step.cycles < 0 || [step.aspirate_delay_ms, step.dispense_delay_ms].some(v => v !== null && !Number.isSafeInteger(v)))) throw new Error('Enter nonnegative integer cycles and integer delays or source default.');
            if (step.operation === 'diagnostic_pipette') {
                const d = step.diagnostic;
                if ('channels' in d && (d.channels.some(c => !Number.isInteger(c) || c < 0 || c > 3) || new Set(d.channels).size !== d.channels.length)) throw new Error('Select distinct diagnostic channels 0–3.');
                if ('volume_ul' in d && (!Number.isFinite(d.volume_ul) || d.volume_ul < 0 || !Number.isSafeInteger(d.speed) || d.speed <= 0)) throw new Error('Diagnostic volume must be nonnegative and speed a positive integer.');
                if ('steps' in d && (!Number.isInteger(d.steps) || d.steps < 0 || d.steps > 2147483647)) throw new Error('Enter diagnostic Z steps 0–2147483647.');
            }
            add('pipette_manual_physical', { ...step }, index);
        } else if (step.operation === 'load_tip' || step.operation === 'measure_fluid_height' || step.operation === 'source_fluid_offset' || step.operation === 'diagnostic_detect_fluid' || step.operation === 'source_calwith_fluid') {
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
