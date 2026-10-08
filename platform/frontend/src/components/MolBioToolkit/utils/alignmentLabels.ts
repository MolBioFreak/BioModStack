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
