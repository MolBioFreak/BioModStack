import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getLaunchContext } from '../../lib/projectManager';

function verifiedReturnUri(raw: string | null): string | null {
    if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return null;
    try {
        const parsed = new URL(raw, 'https://bms.local');
        if (parsed.origin !== 'https://bms.local') return null;
        if (!/^\/projects\/[^/]+$/.test(parsed.pathname)) return null;
        if (!parsed.searchParams.get('focus') || !parsed.searchParams.get('selected')) return null;
        return `${parsed.pathname}${parsed.search}`;
    } catch {
        return null;
    }
}

export function ProjectReturnBanner() {
    const [searchParams] = useSearchParams();
    const launchContextId = searchParams.get('launch_context_id');
    const contextQuery = useQuery({
        queryKey: ['launch-context', launchContextId],
        queryFn: ({ signal }) => getLaunchContext(launchContextId as string, signal),
        enabled: Boolean(launchContextId),
        retry: false,
    });
    const context = contextQuery.data;
    const returnUri = verifiedReturnUri(context?.return_uri ?? null);
    if (!returnUri || !context) return null;

    return (
        <aside className="border-b border-accent/30 bg-accent/10 px-4 py-2 text-content" aria-label="Project return context">
            <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
                <div className="min-w-0 text-xs text-content-secondary">
                    <p className="truncate">
                        Project {context.project_id} / Experiment {context.global_experiment_id} / Domain {context.domain_experiment_id}
                    </p>
                    {context.schema === 'bms.launch-context.v2' && (
                        <p className="truncate">
                            Prepared workflow {context.workflow_id} / Preparation {context.preparation_id} / Attempt {context.run_attempt_id}
                        </p>
                    )}
                </div>
                <Link
                    to={returnUri}
                    aria-label="Return to Project context"
                    className="shrink-0 rounded-lg border border-accent px-3 py-1.5 text-xs font-semibold text-accent focus:ring-2 focus:ring-accent"
                >
                    Return to Project
                </Link>
            </div>
        </aside>
    );
}
