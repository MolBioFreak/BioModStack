export type DnaWeaverOrderFormat = 'fasta' | 'csv';

type OrderRecord = Record<string, unknown>;

function record(value: unknown): OrderRecord | null {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
        ? value as OrderRecord
        : null;
}

function text(value: unknown): string {
    return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function csv(value: unknown): string {
    return `"${text(value).replace(/"/g, '""')}"`;
}

async function sha256Hex(value: string): Promise<string> {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function buildDnaWeaverOrderContent(
    fragments: OrderRecord[],
    planChecksum: string,
    format: DnaWeaverOrderFormat,
): Promise<string> {
    if (!planChecksum || fragments.length === 0) {
        throw new Error('Persisted DNA Weaver order evidence is incomplete');
    }
    const rows = await Promise.all(fragments.map(async (fragment, index) => {
        const metadata = record(fragment.metadata) ?? {};
        const sequence = text(fragment.sequence);
        const sequenceSha256 = text(fragment.sequence_sha256);
        if (!sequence || !/^[a-f0-9]{64}$/i.test(sequenceSha256)) {
            throw new Error(`Fragment ${index + 1} is missing exact sequence/hash evidence`);
        }
        if (await sha256Hex(sequence) !== sequenceSha256.toLowerCase()) {
            throw new Error(`Fragment ${index + 1} sequence does not match its SHA-256 evidence`);
        }
        return {
            name: text(fragment.name) || `fragment_${index + 1}`,
            sequence,
            sequenceSha256,
            sourceCoreStart: fragment.source_core_start ?? metadata.source_core_start,
            sourceCoreEnd: fragment.source_core_end ?? metadata.source_core_end,
            terminalOverlapLength: fragment.terminal_overlap_length ?? metadata.terminal_overlap_length,
        };
    }));
    if (format === 'fasta') {
        return `${rows.map((fragment) => `>${fragment.name}|length=${fragment.sequence.length}|sequence_sha256=${fragment.sequenceSha256}|plan_sha256=${planChecksum}\n${fragment.sequence}`).join('\n')}\n`;
    }
    return `order,name,length,source_core_start,source_core_end,terminal_overlap_length,sequence_sha256,sequence,plan_checksum\n${rows.map((fragment, index) => [
        index + 1,
        fragment.name,
        fragment.sequence.length,
        fragment.sourceCoreStart,
        fragment.sourceCoreEnd,
        fragment.terminalOverlapLength,
        fragment.sequenceSha256,
        fragment.sequence,
        planChecksum,
    ].map(csv).join(',')).join('\n')}\n`;
}

export async function downloadDnaWeaverOrder(
    fragments: OrderRecord[],
    planChecksum: string,
    safeName: string,
    format: DnaWeaverOrderFormat,
): Promise<void> {
    const content = await buildDnaWeaverOrderContent(fragments, planChecksum, format);
    const blob = new Blob([content], { type: format === 'fasta' ? 'text/plain' : 'text/csv' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${safeName}.${format === 'fasta' ? 'fasta' : 'csv'}`;
    anchor.click();
    URL.revokeObjectURL(url);
}
