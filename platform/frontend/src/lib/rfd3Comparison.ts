import { api, type Job, type RFD3GenerationReadModel } from './api';
import type { NativeGenerationPage } from './nativeBinderResults';

export const isRfd3Comparison = (job: { model_id: string; mode: string; params?: Job['params'] }) => job.model_id === 'protein_modification_experimental' && job.mode === 'de_novo_design' && job.params?.generator === 'rfd3';
// Definitions owned by scripts/rfd3_generation/build_result_manifest.py:_structure_metrics.
export const rfd3MeasurementDefinitions = {
    length: { unit: 'residues', description: 'Distinct ATOM chain/residue identity pairs in the emitted CIF.' },
    radius: { unit: 'angstrom', description: 'Unweighted CA radius of gyration about the CA coordinate centroid.' },
    helix_count: { unit: 'helices', description: 'HELX entries in the emitted CIF struct_conf annotations; null means annotations unavailable, not zero helices.' },
    strand_count: { unit: 'strands', description: 'Entries in the emitted CIF struct_sheet_range annotations; null means annotations unavailable, not zero strands.' },
};
export async function fetchRfd3ComparisonPage(jobId: string, offset: number, signal?: AbortSignal): Promise<NativeGenerationPage> {
    const { data } = await api.get<RFD3GenerationReadModel & { total: number; execution_status?: string; population?: Record<string, unknown> }>(`/api/jobs/${encodeURIComponent(jobId)}/rfd3-generation`, { params: { offset, limit: 100 }, signal });
    return { model_id: 'rfd3', receipt: {}, publication: { result_manifest_sha256: data.result_manifest_sha256 },
        offset, limit: 100, total: data.total, native_execution_status: data.execution_status,
        records: data.candidates.map(candidate => ({ candidate_key: candidate.candidate_id, design_id: candidate.design_id,
            population: 'generated', outcome: candidate.status, native_record: candidate,
            metrics: { length: candidate.length, radius: candidate.radius, helix_count: candidate.helix_count, strand_count: candidate.strand_count },
            metric_descriptors: rfd3MeasurementDefinitions })),
    };
}
