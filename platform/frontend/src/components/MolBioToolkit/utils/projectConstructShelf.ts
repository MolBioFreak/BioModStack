import type { NucleotideSequenceListItem, ProjectHubReadModel } from '../../../lib/api';

export function projectHubPlasmidsToConstructShelf(model: ProjectHubReadModel): NucleotideSequenceListItem[] {
    return model.plasmids.map((plasmid) => {
        const isRna = plasmid.molecule_type?.toLowerCase() === 'rna';
        const isCircular = plasmid.topology?.toLowerCase() !== 'linear';
        return {
            id: plasmid.sequence_id,
            revision_id: plasmid.revision_id,
            reopen_href: plasmid.reopen_href,
            name: plasmid.name,
            description: plasmid.description || null,
            sequence_type: isRna ? 'rna' : 'dna',
            molecule_strandedness: 'double',
            molecule_orientation: 'unknown',
            molecule_label: isRna ? 'RNA' : 'DNA',
            is_circular: isCircular,
            length: plasmid.length_bp,
            gc_content: plasmid.gc_percent,
            feature_count: plasmid.feature_count,
            organism: plasmid.organism_host_context,
            accession: null,
            source_file: null,
            entity_kind: 'molecular_sequence',
            topology: isCircular ? 'circular' : 'linear',
            created_at: '',
            updated_at: null,
        };
    });
}
