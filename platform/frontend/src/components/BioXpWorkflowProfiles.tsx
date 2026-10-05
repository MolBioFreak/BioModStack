import { createContext } from 'react';
import type { MethodValue } from '../lib/bioxpMethods';

const object = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
/** Projection of the existing /liquid-classes/starters source identities.
 * Mirrors bioxp_method_liquids.starter_profiles; never invents geometry or fit.
 */
export function publishedTipProfiles(entries: MethodValue[]): MethodValue[] {
    const profiles = new Map<string, MethodValue>();
    for (const entry of entries) {
        const context = object(entry.context), source = object(entry.source), tip = object(source.tip);
        if (typeof context.tip_profile_id !== 'string') continue;
        const identity = context.tip_profile_id;
        if (!profiles.has(identity)) profiles.set(identity, {
            id: identity, revision: 1, generation: context.generation,
            family: tip.family ?? 'Tecan LiHa disposable tips (DiTis)',
            nominal_capacity_ul: tip.nominal_capacity_ul ?? source.tip_size_without_filter_ul,
            filter_type: context.filter_type, source_variants: [],
            native_addressing: null, vessel_geometry: null, dead_volume_ul: null,
            tip_geometry: null, tool_offsets: null, rack_geometry: null,
            adapter_support: 'unknown', consumable_fit: 'unknown', qualification: 'not BioXP-qualified',
        });
        (profiles.get(identity)!.source_variants as unknown[]).push(entry.id);
    }
    return [...profiles.values()].sort((a, b) => String(a.id).localeCompare(String(b.id)));
}

export const BioXpWorkflowProfilesContext = createContext<{
    profiles: MethodValue[]; custodyObjects: MethodValue[];
    pin: (profile: MethodValue) => void; loading?: boolean; error?: boolean;
} | null>(null);

export function pinLabwareProfile(dependencies: MethodValue, profile: MethodValue): MethodValue {
    const retained = dependencies.labware_profiles;
    // Non-array retained expressions/null are not silently replaced by discovery.
    if (retained !== undefined && !Array.isArray(retained)) return dependencies;
    const rows = (retained ?? []) as MethodValue[];
    if (rows.some(p => p && p.id === profile.id)) return dependencies;
    return { ...dependencies, labware_profiles: [...rows, JSON.parse(JSON.stringify(profile))] };
}
