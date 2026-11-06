import { api } from './api';

export const NGS_DATA_FORMATS = { fastq: 'FASTQ', pod5: 'POD5', slow5: 'SLOW5', blow5: 'BLOW5', bam: 'BAM / CRAM', fasta: 'FASTA', results: 'Analysis results', other: 'Other' } as const;
export const NGS_USE_CASES = { sequence_identity: 'Sequence identity confirmation', clone_selection: 'Clone selection', plasmid_screening: 'Plasmid screening', plasmid_onboarding: 'New plasmid onboarding', transcriptomics: 'Transcriptomics / RNA', genomic_sequencing: 'Genomic sequencing', other: 'Other' } as const;
export const NGS_RECORD_STAGES = { planned: 'Planned', acquired: 'Raw data acquired', basecalled: 'Basecalled reads', analysing: 'Analysis in progress', review: 'Ready for review', complete: 'Complete (operator recorded)' } as const;
export interface NgsDataFile { name: string; path: string; size: number }
export interface NgsDataRecordDraft {
    schema: 'bns.ngs-data-record.v1'; record_key: string; title: string; sample_name: string;
    data_format: keyof typeof NGS_DATA_FORMATS; use_case: keyof typeof NGS_USE_CASES;
    workflow_stage: keyof typeof NGS_RECORD_STAGES; data_location: string; notes: string; files: NgsDataFile[];
}
export interface NgsDataRecord { id: string; created_at: string; author: string | null; payload: NgsDataRecordDraft }
export async function listNgsDataRecords(projectId: string, cursor?: string, signal?: AbortSignal) {
    return (await api.get<{ schema: 'bns.ngs-data-record-list.v1'; items: NgsDataRecord[]; next_cursor: string | null }>(`/api/projects/${encodeURIComponent(projectId)}/ngs-data-records`, { params: { cursor, limit: 50 }, signal })).data;
}
export async function createNgsDataRecord(projectId: string, payload: NgsDataRecordDraft) {
    return (await api.post<NgsDataRecord>(`/api/projects/${encodeURIComponent(projectId)}/ngs-data-records`, payload)).data;
}
