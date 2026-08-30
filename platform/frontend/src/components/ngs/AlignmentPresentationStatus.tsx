export interface AlignmentPresentationStatusValue {
    kind: 'full' | 'preview' | 'locus';
    sourceSizeBytes: number | null;
    selectedReadCount: number | null;
    availableReadCount: number | null;
    byteSize: number;
    policyVersion: number | null;
    capped: boolean;
}

const labels = {
    full: 'Full alignment',
    preview: 'Primary-read preview',
    locus: 'Bounded full-source locus slice',
} as const;

function formatBytes(value: number | null): string {
    if (value === null) return 'unknown';
    if (value < 1024) return `${value.toLocaleString()} B`;
    const units = ['KiB', 'MiB', 'GiB'];
    let amount = value;
    let unit = 'B';
    for (const candidate of units) {
        amount /= 1024;
        unit = candidate;
        if (amount < 1024) break;
    }
    return `${amount.toFixed(1)} ${unit}`;
}

export function AlignmentPresentationStatus({ status }: { status: AlignmentPresentationStatusValue }) {
    const count = status.selectedReadCount === null || status.availableReadCount === null
        ? 'all source reads'
        : `${status.selectedReadCount.toLocaleString()} of ${status.availableReadCount.toLocaleString()} reads`;
    return (
        <div role="status" className="ngs-alignment-presentation-status border-b border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]">
            <strong className="text-[var(--text-primary)]">{labels[status.kind]}</strong>
            <span> · {count}</span>
            <span> · track {formatBytes(status.byteSize)}</span>
            <span> · source {formatBytes(status.sourceSizeBytes)}</span>
            <span> · Policy {status.policyVersion || 'direct full-source'}</span>
            <span> · Capped: {status.capped ? 'yes' : 'no'}</span>
            <span className="ml-2 font-medium text-amber-200">The complete BAM remains the scientific authority.</span>
        </div>
    );
}
