import { Link, useSearchParams } from 'react-router-dom';

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
    const returnUri = verifiedReturnUri(searchParams.get('return_uri'));
    if (!returnUri) return null;

    return (
        <aside className="border-b border-accent/30 bg-accent/10 px-4 py-2 text-content" aria-label="Project return context">
            <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
                <p className="text-xs text-content-secondary">This result was opened from a verified Project context.</p>
                <Link
                    to={returnUri}
                    aria-label="Return to Project context"
                    className="rounded-lg border border-accent px-3 py-1.5 text-xs font-semibold text-accent focus:ring-2 focus:ring-accent"
                >
                    Return to Project
                </Link>
            </div>
        </aside>
    );
}
