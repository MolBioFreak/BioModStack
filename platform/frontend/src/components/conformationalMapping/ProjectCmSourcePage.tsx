import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getCmSource } from './conformationalMappingApi';

// Read through the registered source owner: a source receipt is not a Job.
export default function ProjectCmSourcePage() {
    const { projectId = '', sourceId = '' } = useParams();
    const source = useQuery({queryKey: ['cm-source', sourceId], queryFn: () => getCmSource(sourceId)});
    return <main className="mx-auto max-w-4xl space-y-4 px-4 py-6">
        <Link className="text-cyan-300" to={`/projects/${encodeURIComponent(projectId)}`}>← Back to project</Link>
        <h1 className="text-2xl font-semibold">Conformational mapping source</h1>
        {source.isLoading && <p>Loading saved source…</p>}
        {source.isError && <p role="alert">This registered source could not be opened.</p>}
        {source.data && <section className="space-y-2"><h2>{source.data.source_kind.replaceAll('_', ' ')}</h2>
            <p className="break-all">{source.data.source_id}</p><p>{source.data.format} · {source.data.bytes.toLocaleString()} bytes</p>
            <details><summary>Saved source metadata</summary><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(source.data.metadata, null, 2)}</pre></details>
        </section>}
    </main>;
}
