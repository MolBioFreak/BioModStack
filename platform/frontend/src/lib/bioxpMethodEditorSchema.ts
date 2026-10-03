import type { BioXpOperatorJsonSchema as Schema } from './bioxpClient';
const text: Schema = { type: 'string' };
const rawNumber = (description: string): Schema => ({ type: ['number', 'null'], description });
const object = (properties: Record<string, Schema>): Schema => ({ type: 'object', properties, additionalProperties: true });
const array = (items: Schema): Schema => ({ type: 'array', items });
/** Presentation-only field inventory from the published r4 model/source contract.
 * Never supplies values, resolves liquid settings or substitutes for compile.
 * Server schemas supersede these disconnected authoring labels. */
export const methodParameterSchema = array(object({ id: text, label: text,
    type: { enum: ['number', 'integer', 'boolean', 'string', 'array', 'object'] }, unit: text,
    default: {}, minimum: rawNumber('Authored minimum'), maximum: rawNumber('Authored maximum'), choices: array({}),
}));
export const methodTipPolicySchema = object({ mode: { enum: ['manual', 'per_transfer', 'per_source', 'per_step'] },
    profile_id: text, pickup: object({}), eject: object({}),
});
export const liquidClassEditorSchema: Schema = object({
    schema: { const: 'bms.bioxp-liquid-class.v1' }, name: text, description: text,
    context: object({ generation: text, composition: text, tip_profile_id: text, filter_type: text, mode: text,
        target_volume_ul: rawNumber('Intended liquid µL, not commanded displacement'), aliquot_volume_ul: rawNumber('Intended aliquot µL'), sample_count: { type: 'integer' }, recipe_context: text }),
    settings: object({
        target_volume_ul: rawNumber('Intended liquid µL'), commanded_corrected_aspiration_ul: rawNumber('Commanded air displacement µL, not measured liquid'),
        aspirate_speed_ul_s: rawNumber('Aspiration speed µL/s'), aspiration_top_speed_ul_s: rawNumber('Published multi aspiration top speed µL/s'),
        aspiration_delay_ms: rawNumber('Aspiration delay ms'), dispense_delay_ms: rawNumber('Dispense delay ms'),
        air_gap_aspiration_speed_ul_s: rawNumber('Air gap aspiration µL/s'), separate_carry_dispense_speed_ul_s: rawNumber('Carry phase µL/s'), separate_blowout_dispense_speed_ul_s: rawNumber('Blowout phase µL/s'),
        blowout_leading_air_gap_ul: rawNumber('Leading blowout air µL'), carry_trailing_air_gap_ul: rawNumber('Trailing carry air µL'),
        dispense_segments: array(object({ source_heading: text, volume_ul: rawNumber('Commanded displacement µL'), speed_ul_s: rawNumber('µL/s'), evidence: text })),
        dispense_top_speed_ul_s: rawNumber('Multi dispense top speed µL/s'), start_speed_ul_s: rawNumber('Start speed µL/s; application requires actual controller mapping'), cutoff_speed_ul_s: rawNumber('Cutoff speed µL/s; application requires actual controller mapping'),
        slope_n1: { type: ['integer', 'null'] }, slope_n2: { type: ['integer', 'null'] },
        backlash_increments: { type: ['integer', 'null'], minimum: 0, maximum: 500, description: 'Plunger backlash increments added after each aspiration (Cavro K, 0–500)' },
        number_of_dispenses: { type: ['integer', 'null'] }, dispense_volume_ul: rawNumber('Per-aliquot µL'),
        conditioning_volume_ul: rawNumber('Conditioning liquid µL, separate from air'), number_back_to_source: { type: ['integer', 'null'] },
        excess_volume_ul: rawNumber('Reserved excess liquid µL'), excess_destination: text,
        reaspiration_volume_ul: rawNumber('Reaspiration µL'), dispense_to_reaspiration_delay_ms: rawNumber('Delay ms'), retract_distance_mm: rawNumber('Retract mm'),
        contact_mode: { type: ['string', 'null'] }, immersion_depth_mm: rawNumber('Immersion mm'), retract_speed_mm_s: rawNumber('Native motion-supported retract mm/s'),
    }),
    source: object({}), water: object({ id: text, revision: { type: 'integer' } }),
});
