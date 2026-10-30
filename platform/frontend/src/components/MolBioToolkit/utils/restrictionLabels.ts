import type { RestrictionOccurrence } from '../../../lib/restrictionAnalysis';
import { restrictionSpecialCase } from '../../../lib/restrictionSpecialCases';

// The viewer's existing default set also breaks ties between equally specific labels.
export const DEFAULT_MAPPED_ENZYMES = [
    'EcoRI', 'BamHI', 'HindIII', 'XbaI', 'SalI', 'PstI', 'SmaI', 'KpnI', 'SacI', 'XhoI',
    'NotI', 'NdeI', 'NcoI', 'BglII', 'SpeI', 'MluI', 'ApaI', 'ClaI', 'EcoRV', 'NheI',
    'BsaI', 'BbsI', 'SapI', 'AgeI', 'AscI', 'PacI', 'SfiI', 'FseI', 'PmeI',
];
const preferredNames = new Set(DEFAULT_MAPPED_ENZYMES);
const BASE_CHOICES: Readonly<Record<string, number>> = {
    A: 1, C: 1, G: 1, T: 1,
    R: 2, Y: 2, S: 2, W: 2, K: 2, M: 2,
    B: 3, D: 3, H: 3, V: 3, N: 4,
};

function specificity(occurrence: RestrictionOccurrence) {
    const pattern = typeof occurrence.recognition_pattern === 'string' ? occurrence.recognition_pattern.toUpperCase() : '';
    // Display ranking only: no motif matching or new cleavage predictions. A/C/G/T
    // contribute 2 bits, two-base codes 1 bit, three-base codes ~0.415, and N 0.
    return {
        information: [...pattern].reduce((sum, base) => sum + Math.log2(4 / (BASE_CHOICES[base] ?? 4)), 0),
        ambiguous: [...pattern].filter(base => BASE_CHOICES[base] !== 1).length,
    };
}

function compareLabels(a: RestrictionOccurrence, b: RestrictionOccurrence): number {
    const x = specificity(a); const y = specificity(b);
    const difference = y.information - x.information;
    if (Math.abs(difference) > 1e-9) return difference;
    return x.ambiguous - y.ambiguous
        || Number(preferredNames.has(b.enzyme_id)) - Number(preferredNames.has(a.enzyme_id))
        || a.canonical_name.localeCompare(b.canonical_name, 'en')
        || a.occurrence_id.localeCompare(b.occurrence_id, 'en');
}

function cutIdentity(occurrence: RestrictionOccurrence): string {
    // Keep special substrate requirements, incomplete geometry, recognition-only
    // hits, and uncertain matches distinct. Same recognition span is NOT enough.
    if (restrictionSpecialCase(occurrence.enzyme_id)) return occurrence.occurrence_id;
    const dsb = occurrence.double_strand_events;
    const nicks = occurrence.nicks;
    if (dsb.length + nicks.length === 0
        || dsb.some(event => event.status !== 'complete' || event.top_boundary == null || event.bottom_boundary == null)
        || nicks.some(event => event.status !== 'complete' || event.boundary == null)) return occurrence.occurrence_id;
    return JSON.stringify([
        occurrence.certainty,
        dsb.map(event => [
            event.top_boundary, event.bottom_boundary,
            // Normalized coordinates alone can conceal different windings at the origin.
            event.bottom_boundary_unwrapped - event.top_boundary_unwrapped,
            event.overhang_kind, event.overhang_length_nt,
        ]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
        nicks.map(event => [event.strand, event.boundary]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    ]);
}

export interface RestrictionLabelGroup {
    representative: RestrictionOccurrence;
    members: RestrictionOccurrence[];
    name: string;
    description: string;
}

/** Condense map labels without changing selected enzymes, API results or digest inputs. */
export function groupRestrictionLabels(
    occurrences: RestrictionOccurrence[], selectedEnzymes: string[], grouped = true,
): RestrictionLabelGroup[] {
    const selected = new Set(selectedEnzymes);
    const groups = new Map<string, RestrictionOccurrence[]>();
    for (const occurrence of occurrences) {
        if (!selected.has(occurrence.enzyme_id)) continue;
        const key = grouped ? cutIdentity(occurrence) : occurrence.occurrence_id;
        const members = groups.get(key) ?? [];
        members.push(occurrence);
        groups.set(key, members);
    }
    return [...groups.values()].map(members => {
        members.sort(compareLabels);
        const representative = members[0];
        const names = [...new Set(members.map(member => member.enzyme_id))];
        const extras = names.length - 1;
        return {
            representative, members,
            name: `${representative.canonical_name}${extras ? ` +${extras}` : ''}`,
            description: extras ? `Same cut positions: ${members.map(member => `${member.canonical_name} (${member.recognition_pattern ?? 'pattern unavailable'})`).join('; ')}. Label favors the most specific recognition pattern; activity and methylation requirements remain enzyme-specific.` : '',
        };
    });
}
