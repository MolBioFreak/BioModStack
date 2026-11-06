/** Display a zero-based half-open span without inventing a base at a gap. */
export function circularAlignmentDisplayEnd(start: number, consumed: number, length: number): number {
    // The right edge is one-based inclusive: the final base is length, never 0.
    return consumed ? (start + consumed - 1) % length + 1 : start;
}

export function formatAlignmentSpan(start: number, end: number, wrapsOrigin = false, sequenceLength?: number, circular = false): string {
    if (start === end && !wrapsOrigin) {
        if (circular && sequenceLength && (start === 0 || start === sequenceLength)) return `between bases ${sequenceLength} and 1`;
        if (start === 0) return 'before base 1';
        if (sequenceLength != null && start === sequenceLength) return `after base ${sequenceLength}`;
        return `between bases ${start} and ${start + 1}`;
    }
    return wrapsOrigin ? `${start + 1}->${end}` : `${start + 1}-${end}`;
}

export function resolveSubmittedQueryName(explicitName: string, parsedName: string): string | undefined {
    const trimmedExplicitName = explicitName.trim();
    if (trimmedExplicitName) {
        return trimmedExplicitName;
    }

    const trimmedParsedName = parsedName.trim();
    if (trimmedParsedName && trimmedParsedName !== 'Untitled Sequence') {
        return trimmedParsedName;
    }

    return undefined;
}

export function resolveQueryLabel(explicitName: string, parsedName: string): string {
    return resolveSubmittedQueryName(explicitName, parsedName) ?? 'Query sequence';
}

export function getAlignmentDisplayName(queryName: string | null | undefined): string {
    const trimmedQueryName = queryName?.trim();
    return trimmedQueryName ? trimmedQueryName : 'Unnamed alignment';
}
