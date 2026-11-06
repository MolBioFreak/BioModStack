import type { AssemblyFragmentInput, AssemblyFragmentEnd } from './api';
import type { RestrictionDigestSimulation, RestrictionDigestEnd, RestrictionDigestFragment, RestrictionDigestSaveAcknowledgement } from './restrictionAnalysis';
export function digestFragmentEnds(fragment: RestrictionDigestFragment): Pick<AssemblyFragmentInput, 'left_end' | 'right_end'> {
 const end = (e: RestrictionDigestEnd): AssemblyFragmentEnd | null => e.kind === 'no_cut_circular' ? null : ({type:e.kind === 'five_prime_overhang' ? 'sticky_5' : e.kind === 'three_prime_overhang' ? 'sticky_3' : 'blunt',overhang:e.overhang_sequence_5to3 ?? '',protruding_strand:e.protruding_strand,...(e.five_prime_phosphate === 'phosphorylated' || e.five_prime_phosphate === 'unphosphorylated' ? {five_prime_phosphate:e.five_prime_phosphate} : e.enzyme_created ? {five_prime_phosphate:'phosphorylated' as const} : {})});
 return {left_end:end(fragment.left_end),right_end:end(fragment.right_end)};
}
/** Saved digest children are first revisions of their own documents. */
const SAVED_CHILD_REVISION = 1;
export function digestFragmentsForAssembly(simulation: RestrictionDigestSimulation, indices: number[], sourceId: string | null, sourceName: string, saved?: Pick<RestrictionDigestSaveAcknowledgement, 'outputs'> | null): AssemblyFragmentInput[] {
 return simulation.fragments.filter(f => indices.includes(f.fragment_index)).map(f => {
  const parentStart = typeof f.top_start_boundary_normalized === 'number' ? f.top_start_boundary_normalized : f.source_segments[0]?.[0];
  const parentEnd = typeof f.top_end_boundary_normalized === 'number' ? f.top_end_boundary_normalized : f.source_segments.at(-1)?.[1];
  const child = saved?.outputs.find(o => o.fragment_index === f.fragment_index);
  return {id:`digest-${f.fragment_index}`,name:`${sourceName} digest ${f.fragment_index}`,sequence:f.top_strand_sequence,orientation:'forward',circular:f.topology==='circular',
   // Unsaved: the slice of the parent. Saved: the retained child revision (full-source slice), with the parent slice kept in metadata.
   ...(child ? {source_sequence_id:child.document_id,source_name:child.name,source_revision:SAVED_CHILD_REVISION,source_start:0,source_end:f.top_strand_sequence.length}
    : {source_sequence_id:sourceId ?? undefined,source_name:sourceName,source_start:parentStart,source_end:parentEnd,source_wraps_origin:f.wraps_origin}),
   ...digestFragmentEnds(f),metadata:{end_enzymes:{left:f.left_end.contributing_enzyme_ids ?? [],right:f.right_end.contributing_enzyme_ids ?? []},source_segments:f.source_segments,source:simulation.source,catalog:simulation.catalog,selected_enzyme_ids:simulation.selected_enzyme_ids,simulation_sha256:simulation.simulation_sha256,digest_fragment:f,
    ...(child ? {saved_digest_child:{document_id:child.document_id,revision_id:child.revision_id,revision_number:SAVED_CHILD_REVISION},parent_slice:{source_sequence_id:sourceId,source_start:parentStart,source_end:parentEnd,source_wraps_origin:f.wraps_origin}} : {})}} as AssemblyFragmentInput;
 });
}
