/** Convert a frozen share-bundle record into viewer SequenceData. */

import type { NucleotideSequenceResponse, SequenceData } from '../components/MolBioToolkit/types';
import { moleculeLabelForNucleotide } from '../components/MolBioToolkit/utils/nucleotides';
import { sequenceDataFromApiRecord } from '../components/MolBioToolkit/utils/sequenceRecord';
import type { NucleotideMoleculeOrientation, NucleotideMoleculeStrandedness } from '../lib/api';
import type { GuestSequence } from './shareGuestApi';

const STRANDEDNESS: NucleotideMoleculeStrandedness[] = ['single', 'double', 'unknown'];
const ORIENTATION: NucleotideMoleculeOrientation[] = ['positive', 'negative', 'ambisense', 'not_applicable', 'unknown'];
/** Convert a frozen bundle record into viewer data; null when it carries no viewable nucleotide sequence. */
export function guestSequenceData(item: GuestSequence): SequenceData | null {
    const r = item.record;
    const sequence = r.sequence;
    const sequenceType = r.sequence_type === 'rna' ? 'rna' : r.sequence_type === 'dna' ? 'dna' : null;
    if (!sequence || !sequenceType) return null;
    const strandedness = STRANDEDNESS.find((v) => v === r.molecule_strandedness) ?? (sequenceType === 'rna' ? 'single' : 'double');
    const orientation = ORIENTATION.find((v) => v === r.molecule_orientation) ?? (sequenceType === 'rna' ? 'unknown' : 'not_applicable');
    const record: NucleotideSequenceResponse = {
        id: item.sequence_id,
        name: r.name || 'Untitled sequence',
        description: r.description,
        sequence,
        sequence_type: sequenceType,
        molecule_strandedness: strandedness,
        molecule_orientation: orientation,
        molecule_label: moleculeLabelForNucleotide(sequenceType, strandedness, orientation),
        is_circular: r.is_circular === true,
        length: sequence.length,
        features: (r.features ?? []) as NucleotideSequenceResponse['features'],
        primers: (r.primers ?? []) as NucleotideSequenceResponse['primers'],
        analysis_tracks: (r.analysis_tracks ?? []) as NucleotideSequenceResponse['analysis_tracks'],
        organism: r.organism,
        accession: r.accession,
        source_file: r.source_file,
        gc_content: r.gc_content,
        parent_id: item.parent_sequence_id,
        operation: r.operation,
        operation_params: r.operation_params,
        version: item.revision_number,
        created_at: item.created_at ?? '',
        updated_at: null,
    };
    return sequenceDataFromApiRecord(record);
}
