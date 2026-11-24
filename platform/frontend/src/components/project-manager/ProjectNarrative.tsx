import { Fragment, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { projectNavigationHref } from '../../lib/projectManager';

// Project notes remain plain text, with optional named links to existing BMS
// records. Do not interpret HTML, remote URLs, or executable URL schemes.
const namedLink = /\[([^\]\n]+)\]\((\/(?:(?:projects|designs|jobs)\/[^\s()]+|stats\?run_id=[A-Za-z0-9][A-Za-z0-9._-]{0,159}))\)/g;

export function hasProjectNarrativeLinks(text: string): boolean {
    return text.search(namedLink) !== -1;
}

export function ProjectNarrative({ text }: { text: string }) {
    const location = useLocation();
    const parts: ReactNode[] = [];
    let offset = 0;
    for (const match of text.matchAll(namedLink)) {
        const href = match[2];
        if (/[\\\u0000-\u001f\u007f]/.test(href) || /%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)/i.test(href)) continue;
        parts.push(text.slice(offset, match.index));
        parts.push(<Link key={match.index} to={projectNavigationHref(href, `${location.pathname}${location.search}${location.hash}`)} className="font-medium text-accent underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">{match[1]}</Link>);
        offset = match.index + match[0].length;
    }
    parts.push(text.slice(offset));
    return <span className="whitespace-pre-wrap break-words">{parts.map((part, index) => <Fragment key={index}>{part}</Fragment>)}</span>;
}
