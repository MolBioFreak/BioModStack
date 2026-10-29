import { useQuery } from '@tanstack/react-query';
import { fetchModelById } from '../lib/api';

type PublicReference = { name: string; description: string; github: string; paper: string; docs: string; version?: string | null; registry_version?: string | null };

/** Registry-owned public references are display metadata, never scientific input. */
export function ModelReferenceCard({ model }: { model: string }) {
    const details = useQuery({
        queryKey: ['de-novo-engine-references'],
        queryFn: async () => (await fetchModelById('protein_modification_experimental')).data,
        staleTime: 60_000, retry: false,
    });
    const reference = details.data?.engine_references?.[model] as PublicReference | undefined;
    if (!reference) return <aside aria-label="Model references" className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-xs">
        {details.isPending ? 'Loading model references…' : `Public references for ${model} are not available.`}
    </aside>;
    const links = ([['GitHub', reference.github], ['Preprint', reference.paper], ['Docs', reference.docs]] as const)
        .filter(([, href]) => { try { return new URL(href).protocol === 'https:'; } catch { return false; } });
    return <aside aria-label={`${reference.name} references`} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <strong>{reference.name}</strong>
            {links.map(([label, href]) =>
                <a key={label} href={href} target="_blank" rel="noopener noreferrer" className="underline">{label}</a>)}
        </div>
        <details className="mt-1">
            <summary className="cursor-pointer text-[var(--text-secondary)]">About this engine</summary>
            <p className="text-[var(--text-secondary)]">{reference.description}</p>
            <p className="text-[var(--text-secondary)]">Registry version: {reference.registry_version ?? reference.version ?? 'not reported'} (not a runtime receipt)</p>
        </details>
    </aside>;
}
