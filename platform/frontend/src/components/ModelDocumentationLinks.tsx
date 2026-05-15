import { getModelDocumentationLinks } from './modelDocumentationRegistry.js';
import type { ModelDocumentationTopic } from './modelDocumentationRegistry.js';

export { getModelDocumentationLinks, MODEL_DOCUMENTATION_LINKS } from './modelDocumentationRegistry.js';
export type { DocumentationLink, ModelDocumentationTopic } from './modelDocumentationRegistry.js';

interface ModelDocumentationLinksProps {
    topics: ModelDocumentationTopic[];
    summary?: string;
    title?: string;
    className?: string;
    compact?: boolean;
}

export function ModelDocumentationLinks({
    topics,
    summary,
    title = 'Documentation',
    className = '',
    compact = false,
}: ModelDocumentationLinksProps) {
    const links = getModelDocumentationLinks(topics);
    if (links.length === 0) return null;

    return (
        <div
            data-bms-model-doc-linkouts="true"
            data-doc-topics={topics.join(',')}
            className={`rounded-xl border border-slate-700/70 bg-slate-950/45 ${compact ? 'px-3 py-2' : 'p-3'} ${className}`}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">{title}</div>
                <div className="flex flex-wrap gap-2">
                    {links.map((link) => (
                        <a
                            key={link.href}
                            href={link.href}
                            target="_blank"
                            rel="noreferrer"
                            className="rounded-lg border border-slate-700 bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-slate-200 transition-colors hover:border-blue-400/60 hover:text-blue-200"
                        >
                            {link.label}
                        </a>
                    ))}
                </div>
            </div>
            {summary && <p className="mt-2 text-xs leading-snug text-slate-500">{summary}</p>}
        </div>
    );
}
