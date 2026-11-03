import type { AssemblyFragmentInput, AssemblyFragmentEnd } from './api';
import type { RestrictionDigestSimulation, RestrictionDigestEnd, RestrictionDigestFragment } from './restrictionAnalysis';
export function digestFragmentEnds(fragment: RestrictionDigestFragment): Pick<AssemblyFragmentInput, 'left_end' | 'right_end'> {
 const end = (e: RestrictionDigestEnd): AssemblyFragmentEnd | null => e.kind === 'no_cut_circular' ? null : ({type:e.kind === 'five_prime_overhang' ? 'sticky_5' : e.kind === 'three_prime_overhang' ? 'sticky_3' : 'blunt',overhang:e.overhang_sequence_5to3 ?? '',protruding_strand:e.protruding_strand});
 return {left_end:end(fragment.left_end),right_end:end(fragment.right_end)};
}
export function digestFragmentsForAssembly(simulation: RestrictionDigestSimulation, indices: number[], sourceId: string | null, sourceName: string): AssemblyFragmentInput[] {
 return simulation.fragments.filter(f => indices.includes(f.fragment_index)).map(f => ({id:`digest-${f.fragment_index}`,name:`${sourceName} digest ${f.fragment_index}`,sequence:f.top_strand_sequence,orientation:'forward',circular:f.topology==='circular',source_sequence_id:sourceId ?? undefined,source_name:sourceName,
 source_start:typeof f.top_start_boundary_normalized === 'number' ? f.top_start_boundary_normalized : f.source_segments[0]?.[0],source_end:typeof f.top_end_boundary_normalized === 'number' ? f.top_end_boundary_normalized : f.source_segments.at(-1)?.[1],source_wraps_origin:f.wraps_origin,
 ...digestFragmentEnds(f),metadata:{source_segments:f.source_segments,source:simulation.source,catalog:simulation.catalog,selected_enzyme_ids:simulation.selected_enzyme_ids,simulation_sha256:simulation.simulation_sha256,digest_fragment:f}}));
}
