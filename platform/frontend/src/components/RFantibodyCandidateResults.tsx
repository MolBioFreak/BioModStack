import { lazy, Suspense, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { downloadDesignPdb, fetchDesigns, fetchNativeStructureDocument, type Design } from '../lib/api';
import type { CandidateRef } from '../lib/binderSelectedRound';
import { NativeBinderGenerationResults, type NativeWorkbenchAdapter, type Props } from './NativeBinderGenerationResults';
const StructureWorkbench = lazy(() => import('../structureViewer/StructureWorkbench').then(module => ({ default: module.StructureWorkbench })));

/** RFantibody's persisted Design reader remains its owner; no generation endpoint or invented artifacts. */
export function RFantibodyCandidateResults({ onOpenWorkbench, ...props }: Props & { onOpenWorkbench: (designId: string, document?: { artifact_id?: string; target_state?: string; model?: number }) => void }) {
    const adapter = useMemo<NativeWorkbenchAdapter>(() => ({
        key: 'rfantibody', title: 'RFantibody candidates', recordLabel: 'RFantibody outputs', label: row => String(row.design_id),
        preferredColumns: ['plddt', 'rfd_rog', 'cdr_h3', 'binder_sequence'],
        fetchPage: async offset => {
            const { data } = await fetchDesigns({ job_id: props.jobId, include_children: false, offset, limit: 100, sort_by: 'name', sort_desc: false });
            return { offset, limit: 100, total: data.total, receipt: {}, publication: {}, records: data.designs.map(design => ({
                candidate_key: design.id, design_id: design.id,
                candidate_ref: (design as Design & { candidate_ref?: CandidateRef }).candidate_ref,
                native_record: design,
                metrics: Object.fromEntries(Object.entries(design).filter(([, value]) => value === null || ['number', 'string', 'boolean'].includes(typeof value))),
            })) };
        },
        inspect: row => {
            const design = row.native_record as Design;
            return <div className="space-y-3">
                <button type="button" onClick={() => onOpenWorkbench(design.id, row.candidate_ref?.kind === 'design' ? row.candidate_ref.document : undefined)}>Open RFantibody model workbench</button>
                {<RFantibodyStructure design={design} candidateRef={row.candidate_ref} />}
            </div>;
        },
    }), [props.jobId, onOpenWorkbench]);
    return <NativeBinderGenerationResults {...props} adapter={adapter} />;
}
function RFantibodyStructure({ design, candidateRef }: { design: Design; candidateRef?: CandidateRef }) {
    const exact = candidateRef?.kind === 'design' ? candidateRef.document : candidateRef?.kind === 'native_document' ? candidateRef : undefined;
    const query = useQuery({ queryKey: ['rfantibody-exact-document', candidateRef?.job_id, exact?.artifact_id, exact?.target_state],
        queryFn: () => fetchNativeStructureDocument(candidateRef!.job_id, exact!.artifact_id!, exact?.target_state).then(response => response.data), enabled: !!exact?.artifact_id, retry: false });
    const document = query.data?.document;
    const url = exact ? document?.sourceUrl : design.pdb_path ? downloadDesignPdb(design.id) : undefined;
    return url ? <Suspense fallback={<p>Loading molecule…</p>}><StructureWorkbench mode="standard" structureUrl={url}
        format={document?.sourceKind === 'mmcif' ? 'cif' : 'pdb'} structureDocumentId={document?.documentId} structureContentSha256={document?.contentSha256}
        alphafoldView={false} height={520} showSequenceTrack showMeasurements showM6Workbench /></Suspense>
        : exact ? <p role="status">{query.isFetching ? 'Loading exact RFantibody document…' : 'Exact RFantibody document unavailable; primary structure is not substituted.'}</p> : null;
}
