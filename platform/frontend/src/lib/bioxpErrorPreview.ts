// Presentation only. The original response remains untouched in the query/error.
// A fixed field projection avoids enumerating arbitrarily wide backend objects.
// This is NOT a full evidence export or a secret sanitizer: text can contain secrets.
const FIELDS = [
    'failure', 'message', 'msg', 'code', 'error', 'detail', 'reason', 'block_reason', 'startup_error',
    'loc', 'type', 'http_status', 'status', 'ok', 'body', 'response', 'raw_return_layers',
    'stage_receipts', 'child_receipts', 'expected', 'actual', 'evidence', 'board', 'axis',
    'position', 'command_id', 'action_id', ...Array.from({ length: 32 }, (_, i) => String(i)),
];
const SUFFIX = '…[truncated]';
export function boundedBioXpText(value: string, limit = 2048): string {
    // Slice BEFORE trim: even an enormous whitespace prefix has bounded work.
    if (limit <= SUFFIX.length) return value.length <= limit ? value.trim() : SUFFIX.slice(0, limit);
    return value.length <= limit ? value.trim()
        : `${value.slice(0, Math.max(0, limit - SUFFIX.length)).trim()}${SUFFIX}`;
}

export function bioXpErrorBodyPreview(value: unknown): string {
    let nodes = 128;
    let characters = 4096;
    const ancestors = new WeakSet<object>();
    const visit = (input: unknown, depth: number): unknown => {
        if (nodes-- <= 0 || depth > 8) return '[truncated]';
        if (typeof input === 'string') {
            const text = boundedBioXpText(input, Math.min(2048, characters));
            characters = Math.max(0, characters - text.length);
            return text;
        }
        if (input == null || typeof input === 'boolean' || typeof input === 'number') return input ?? null;
        if (typeof input !== 'object') return '[unsupported]';
        if (ancestors.has(input)) return '[circular]';
        ancestors.add(input);
        try {
            if (Array.isArray(input)) {
                const result: unknown[] = [];
                for (let i = 0; i < Math.min(input.length, 16) && nodes > 0; i++) result.push(visit(input[i], depth + 1));
                if (result.length < input.length) result.push('[truncated]');
                return result;
            }
            const result: Record<string, unknown> = Object.create(null);
            result._preview = 'Selected error fields only; other fields omitted';
            for (const key of FIELDS) {
                if (nodes <= 0) { result._truncated = true; break; }
                // Do not invoke accessors or toJSON on the source object.
                const descriptor = Object.getOwnPropertyDescriptor(input, key);
                if (descriptor && 'value' in descriptor) result[key] = visit(descriptor.value, depth + 1);
            }
            return result;
        } finally { ancestors.delete(input); }
    };
    try {
        return boundedBioXpText(JSON.stringify(visit(value, 0), null, 2), 8192);
    } catch {
        return '[error preview unavailable]';
    }
}
