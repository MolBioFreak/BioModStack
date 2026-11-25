import type { ProjectHubReadModel } from '../../../lib/api';
import type { ProjectSequenceShelfItem } from '../../../lib/projectManager';
import type { NucleotideSequenceListItem } from '../types';

export function projectHubDNASequencesToConstructShelf(model: ProjectHubReadModel): NucleotideSequenceListItem[] {
    const seen = new Set<string>();
    return model.plasmids.flatMap((plasmid) => {
        if (seen.has(plasmid.sequence_id)) return [];
        seen.add(plasmid.sequence_id);
        const isCircular = plasmid.topology === 'circular';
        const seqType: 'dna' | 'rna' = plasmid.molecule_type === 'rna' ? 'rna' : 'dna';
        return {
            id: plasmid.sequence_id,
            revision_id: plasmid.revision_id,
            reopen_href: plasmid.reopen_href,
            name: plasmid.name,
            description: null,
            sequence_type: seqType,
            molecule_strandedness: seqType === 'rna' ? 'single' : 'double',
            molecule_orientation: 'not_applicable',
            molecule_label: seqType === 'rna' ? 'RNA' : (isCircular ? 'Circular DNA' : 'Linear DNA'),
            is_circular: isCircular,
            topology: isCircular ? 'circular' : 'linear',
            length: plasmid.length_bp,
            gc_content: plasmid.gc_percent ?? 0,
            feature_count: plasmid.feature_count,
            created_at: '',
            updated_at: null,
            is_head_editable: false,
        };
    });
}

export function projectSequencesToConstructShelf(items: ProjectSequenceShelfItem[]): NucleotideSequenceListItem[] {
    const seen = new Set<string>();
    return items.flatMap((item) => {
        const seqId = item.id || item.sequence_id;
        if (!seqId || seen.has(seqId)) return [];
        seen.add(seqId);
        const isCircular = Boolean(item.is_circular ?? item.circular ?? (item.topology === 'circular'));
        const seqType: 'dna' | 'rna' = item.sequence_type === 'rna' ? 'rna' : 'dna';
        return {
            id: seqId,
            revision_id: item.revision_id ?? undefined,
            reopen_href: item.reopen_href ?? undefined,
            name: item.name,
            description: item.description ?? null,
            sequence_type: seqType,
            molecule_strandedness: seqType === 'rna' ? 'single' : 'double',
            molecule_orientation: 'not_applicable',
            molecule_label: seqType === 'rna' ? 'RNA' : (isCircular ? 'Circular DNA' : 'Linear DNA'),
            is_circular: isCircular,
            topology: isCircular ? 'circular' : 'linear',
            length: item.length ?? 0,
            gc_content: item.gc_content ?? 0,
            feature_count: item.feature_count ?? 0,
            created_at: item.created_at ?? '',
            updated_at: item.updated_at ?? null,
            domain_experiment_id: item.domain_experiment_id ?? null,
            state_revision_id: item.state_revision_id ?? null,
            is_head_editable: item.is_head_editable,
        };
    });
}
