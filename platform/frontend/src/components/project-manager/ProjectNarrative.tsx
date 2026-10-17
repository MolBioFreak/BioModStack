import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

// Project notes remain plain text, with optional named links to existing BMS
// records. Do not interpret HTML, remote URLs, or executable URL schemes.
export function hasProjectNarrativeLinks(text: string): boolean {
    return /\[[^\]\n]+\]\(\/(?:projects|designs|jobs)\/[^\s()]+\)/.test(text);
}

export function ProjectNarrative({ text }: { text: string }) {
    const parts: ReactNode[] = [];
    const links = /\[([^\]\n]+)\]\((\/(?:projects|designs|jobs)\/[^\s()]+)\)/g;
    let offset = 0;
    for (const match of text.matchAll(links)) {
        const href = match[2];
        if (/[\\\u0000-\u001f\u007f]/.test(href) || /%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)/i.test(href)) continue;
        parts.push(text.slice(offset, match.index));
        parts.push(<Link key={match.index} to={href} className="font-medium text-accent underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">{match[1]}</Link>);
        offset = match.index + match[0].length;
    }
    parts.push(text.slice(offset));
    return <span className="whitespace-pre-wrap break-words">{parts.map((part, index) => <Fragment key={index}>{part}</Fragment>)}</span>;
}
