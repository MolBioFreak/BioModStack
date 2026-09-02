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
    full: 'Reads',
    preview: 'Read preview',
    locus: 'Locus reads',
} as const;

export function AlignmentPresentationStatus({ status }: { status: AlignmentPresentationStatusValue }) {
    const count = status.selectedReadCount === null || status.availableReadCount === null
        ? 'all reads'
        : `${status.selectedReadCount.toLocaleString()} of ${status.availableReadCount.toLocaleString()} reads`;
    return (
        <div role="status" className="ngs-alignment-presentation-status border-b border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]">
            <strong className="text-[var(--text-primary)]">{labels[status.kind]}</strong>
            <span> · {count}</span>
        </div>
    );
}
