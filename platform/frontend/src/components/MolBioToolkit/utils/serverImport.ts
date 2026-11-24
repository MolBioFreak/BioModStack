/**
 * GenBank/FASTA import through the server importer (origin-spanning and part order
 * handled there). Returns null for formats the server importer does not read.
 */
import { commitMolBioSequenceImport } from '../../../lib/api';
import type { SequenceData } from '../types';
import { normalizeFeatureList, normalizeFeatureRecord, primersFromPrimerBindFeatures } from './sequenceRecord';

export type ServerImportTopology = 'preserve' | 'linear' | 'circular';

export interface ServerImportResult {
    data: SequenceData;
    sequenceId: string;
    /** primer_bind features were turned into primers; the saved record still lists them as features. */
    needsSave: boolean;
}

function serverFormat(fileName: string, text: string): 'genbank' | 'fasta' | null {
    const genbank = /\.(gb|gbk|gbff|genbank)$/i.test(fileName);
    const fasta = /\.(fa|fas|fasta|fna|ffn)$/i.test(fileName);
    if (!genbank && !fasta) return null;
    // The server importer is DNA-only; RNA stays on the browser parser.
    if (genbank && /^LOCUS\s.*\bRNA\b/im.test(text)) return null;
    if (fasta && /^[^>\n]*[Uu]/m.test(text.replace(/^>.*$/gm, '')) && !/[Tt]/.test(text.replace(/^>.*$/gm, ''))) return null;
    return genbank ? 'genbank' : 'fasta';
}

export async function importViaServer(file: File, text: string, topology: ServerImportTopology): Promise<ServerImportResult | null> {
    const format = serverFormat(file.name, text);
    if (!format) return null;
    const loci = format === 'genbank' ? [...text.matchAll(/^LOCUS.*$/gm)].map((m) => /\bcircular\b/i.test(m[0])) : [];
    const forced = topology === 'preserve' ? null : topology;
    const overrides: Record<number, 'linear' | 'circular'> = {};
    loci.forEach((circular, index) => { overrides[index + 1] = forced ?? (circular ? 'circular' : 'linear'); });
    const response = await commitMolBioSequenceImport({
        source_format: format,
        source_text: text,
        topology_default: forced ?? 'linear',
        topology_overrides: overrides,
        idempotency_key: `molbio-import-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        origin_surface: 'molbio',
        source_provider: 'upload',
    });
    const record = (response.data.records as unknown as Array<Record<string, any>>)[0];
    if (!record) return null;
    const sequence = String(record.sequence);
    const features = normalizeFeatureList((record.features || []).map((feature: any, index: number) => normalizeFeatureRecord(feature, `f_${index}`)));
    const converted = primersFromPrimerBindFeatures(features, sequence);
    return {
        sequenceId: String(record.sequence_id),
        needsSave: converted.primers.length > 0,
        data: {
            name: record.name || file.name.replace(/\.[^.]+$/, ''),
            description: record.description || undefined,
            sequence,
            circular: record.topology === 'circular',
            sequenceType: 'dna',
            moleculeStrandedness: 'double',
            moleculeOrientation: 'not_applicable',
            features: converted.features,
            primers: converted.primers,
            translations: [],
            analysisTracks: [],
            organism: record.organism || undefined,
            accession: record.accession || undefined,
            sourceFile: file.name,
        },
    };
}
