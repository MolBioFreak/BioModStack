import type { NucleotideSequenceListItem, ProjectHubReadModel } from '../../../lib/api';

export function projectHubDNASequencesToConstructShelf(model: ProjectHubReadModel): NucleotideSequenceListItem[] {
    return model.plasmids.map((sequence) => {
        const isRna = sequence.molecule_type?.toLowerCase() === 'rna';
        const isCircular = sequence.topology?.toLowerCase() !== 'linear';
        return {
            id: sequence.sequence_id,
            revision_id: sequence.revision_id,
            reopen_href: sequence.reopen_href,
            name: sequence.name,
            description: sequence.description || null,
            sequence_type: isRna ? 'rna' : 'dna',
            molecule_strandedness: 'double',
            molecule_orientation: 'unknown',
            molecule_label: isRna ? 'RNA' : 'DNA',
            is_circular: isCircular,
            length: sequence.length_bp,
            gc_content: sequence.gc_percent,
            feature_count: sequence.feature_count,
            organism: sequence.organism_host_context,
            accession: null,
            source_file: null,
            entity_kind: 'molecular_sequence',
            topology: isCircular ? 'circular' : 'linear',
            created_at: '',
            updated_at: null,
        };
    });
}
