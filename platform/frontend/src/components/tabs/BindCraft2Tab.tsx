import { Link, useLocation } from 'react-router-dom';

/** Dedicated entry links preserve destination/Project context and existing aliases. */
export function bindCraft2EntrySearch(search: string, mode = 'campaign') {
    const query = new URLSearchParams(search);
    query.delete('engine'); query.delete('template'); query.delete('model'); query.delete('mode');
    if (mode === 'campaign') { query.set('template', 'antibody_denovo'); query.set('engine', 'bindcraft2'); }
    else { query.set('model', 'bindcraft2'); query.set('mode', mode); }
    return `?${query}`;
}
export function canonicalBindCraft2Entry(search: string): string {
    const query = new URLSearchParams(search);
    return query.get('model') === 'bindcraft2' && query.get('mode') === 'campaign' ? bindCraft2EntrySearch(search) : search;
}
/** Actions are supplied by existing model discovery, never a new native action list. */
export function BindCraft2Tab({ actions = [] }: { actions?: readonly string[] }) {
    const location = useLocation();
    return <section aria-label="BindCraft2 workflows" className="space-y-3">
        <h2>BindCraft2</h2>
        <Link to={{ pathname: '/submit', search: bindCraft2EntrySearch(location.search) }}>New BindCraft2 campaign</Link>
        {actions.filter(mode => mode !== 'campaign').map(mode => <Link className="block" key={mode} to={{ pathname: '/submit', search: bindCraft2EntrySearch(location.search, mode) }}>{mode}</Link>)}
    </section>;
}
