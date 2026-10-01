import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { ArtifactDetails } from './ArtifactDetails';
import { fetchExecutionTargetInventoryArtifacts, fetchExecutionTargetPreloadArtifacts } from '../../lib/api';

type Props = {
  targetId: string;
  operationId: string;
  collection: 'inventory' | 'progress' | 'cached';
  count: number;
  sequence?: number;
};
const labels = { inventory: 'Cache artifacts', progress: 'Artifact progress', cached: 'Cached preload artifacts' };
const states: Record<string, string> = { pending: 'queued', transferring: 'downloading', verifying: 'publishing', verified: 'complete or cached', interrupted: 'interrupted' };

/** Mount the query only inside an open disclosure. Keep exactly one page visible. */
export function PagedArtifactDetails(props: Props) {
  return <ArtifactDetails key={`${props.targetId}:${props.operationId}:${props.collection}`} label={labels[props.collection]} count={props.count}>
    {() => <ArtifactPageView {...props} />}
  </ArtifactDetails>;
}
function ArtifactPageView({ targetId, operationId, collection, sequence }: Props) {
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ['execution-target-artifacts', targetId, operationId, collection, offset, sequence],
    queryFn: ({ signal }) => collection === 'inventory'
      ? fetchExecutionTargetInventoryArtifacts(targetId, offset, signal)
      : fetchExecutionTargetPreloadArtifacts(targetId, operationId, collection, offset, signal),
    retry: false,
    gcTime: 0,
  });
  const page = query.isError ? undefined : query.data;
  const mismatch = page && page.operation_id !== operationId;
  const error = isAxiosError(query.error) && typeof query.error.response?.data?.detail === 'string'
    ? query.error.response.data.detail : query.error instanceof Error ? query.error.message : 'Artifact details unavailable';
  return <div className="space-y-2">
    {query.isPending && <p role="status">Loading artifact details…</p>}
    {(query.isError || mismatch) && <div role="alert"><p>{mismatch ? 'Preparation receipt changed. Refresh worker status to view its details.' : error}</p><button type="button" onClick={() => void query.refetch()}>Retry artifact details</button></div>}
    {page && !mismatch && <>
      <p>{page.total_count === 0 ? 'No artifacts reported.' : page.items.length === 0 ? 'No artifacts on this page.' : `${page.offset + 1}–${page.offset + page.items.length} of ${page.total_count} artifacts`}{page.sequence != null ? ` · Sequence ${page.sequence}` : ''}</p>
      <ul aria-label={labels[collection]} className="space-y-2 text-xs">{page.items.map((artifact, index) => <li key={`${artifact.name}:${index}`} className="break-all">
        <p className="font-mono">{artifact.name}</p>
        <p>{'state' in artifact ? `${states[String(artifact.state)] ?? artifact.state} · ` : ''}{artifact.size_bytes.toLocaleString()} bytes · SHA256 <span className="font-mono">{artifact.sha256}</span></p>
      </li>)}</ul>
    </>}
    <div className="flex gap-3">
      <button type="button" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(value => Math.max(0, value - 100))}>Previous artifacts</button>
      <button type="button" disabled={!page || !!mismatch || query.isFetching || query.isError || offset + 100 >= page.total_count} onClick={() => setOffset(value => value + 100)}>Next artifacts</button>
    </div>
  </div>;
}
