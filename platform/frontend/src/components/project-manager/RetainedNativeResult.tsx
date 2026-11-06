import { lazy, Suspense, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { resolveApiResourceUrl } from '../../runtime/tailnetEnvironment';

const StructureWorkbench = lazy(() => import('../../structureViewer/StructureWorkbench').then(module => ({ default: module.StructureWorkbench })));
interface RetainedArtifact {
    resource_id: string; source_artifact_id: string; logical_path: string;
    sha256: string; media_type: string; bytes: number; content_url: string;
}
interface RetainedInventory {
    receipt_id: string;
    producer: { entity_id: string; contract_id?: string; metadata?: Record<string, unknown> };
    artifacts: RetainedArtifact[];
}

/** Project custody is the file authority. No synthetic Job, Design or browser-native parser. */
export function RetainedNativeResult({ projectId, receiptId, returnUri }: { projectId: string; receiptId: string; returnUri?: string | null }) {
    const [selectedId, setSelectedId] = useState<string>();
    const base = `/api/projects/${encodeURIComponent(projectId)}/native-results/${encodeURIComponent(receiptId)}`;
    const inventory = useQuery({ queryKey: ['project-retained-native-result', projectId, receiptId],
        queryFn: async ({ signal }) => (await api.get<RetainedInventory>(base, { signal })).data, retry: false });
    const selected = inventory.data?.artifacts.find(file => file.resource_id === selectedId);
    // Bind downloads to this Project/receipt, not to an old producer URL or a native path.
    const contentUrl = (file: RetainedArtifact) => `${base}/artifacts/${encodeURIComponent(file.resource_id)}`;
    const format = selected?.logical_path.match(/\.(pdb|cif|mmcif)$/i)?.[1]?.toLowerCase();
    const textDocument = selected && !format && (/^(text\/|application\/json)/.test(selected.media_type) || /\.(json|jsonl|txt|csv|tsv|yaml|yml|log|fasta|fa)$/i.test(selected.logical_path));
    const document = useQuery({ queryKey: ['project-retained-native-document', projectId, receiptId, selectedId],
        queryFn: async ({ signal }) => (await api.get<string>(contentUrl(selected!), { signal, responseType: 'text', transformResponse: [value => value] })).data,
        enabled: Boolean(textDocument), retry: false });
    const fallback = `/projects/${encodeURIComponent(projectId)}`;
    const back = returnUri && (returnUri === fallback || returnUri.startsWith(`${fallback}?`) || returnUri.startsWith(`${fallback}/`)) ? returnUri : fallback;
    return <section className="space-y-3" aria-label="Retained Project native result">
        <Link to={back}>Return to Project results</Link>
        <h2>Retained native result</h2>
        <p>Receipt: {receiptId}</p>
        {inventory.isLoading && <p role="status">Loading retained native files…</p>}
        {inventory.error && <p role="alert">Retained native result unavailable: {inventory.error.message}</p>}
        {inventory.data && <>
            <p>Original producer: {inventory.data.producer.entity_id}. These files are owned by this Project; reopening does not require the producer Job.</p>
            <ul>{inventory.data.artifacts.map(file => <li key={file.resource_id}>
                <button type="button" aria-pressed={file.resource_id === selectedId} onClick={() => setSelectedId(file.resource_id)}>Inspect {file.logical_path}</button>
                {' '}<a href={resolveApiResourceUrl(contentUrl(file))} download>Download {file.logical_path}</a>
                <p>{file.bytes} bytes · SHA-256 {file.sha256}</p>
            </li>)}</ul>
            {!inventory.data.artifacts.length && <p>No retained files in this receipt.</p>}
        </>}
        {selected && <section aria-label="Retained native document">
            <h3>{selected.logical_path}</h3>
            {format ? <Suspense fallback={<p>Loading structure viewer…</p>}><StructureWorkbench key={`${base}:${selectedId}`} structureUrl={resolveApiResourceUrl(contentUrl(selected))} format={format === 'mmcif' ? 'cif' : format as 'pdb' | 'cif'} height="500px" /></Suspense>
                : textDocument ? <>{document.isLoading && <p role="status">Loading native document…</p>}{document.error && <p role="alert">Native document unavailable: {document.error.message}</p>}{document.data !== undefined && <pre className="overflow-auto whitespace-pre-wrap">{document.data}</pre>}</>
                    : <p>This native binary is available through Download; no derived interpretation is substituted.</p>}
        </section>}
    </section>;
}
