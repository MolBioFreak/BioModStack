export function bioXpReceiptTimestampText(value: string | null): string {
    if (value === null) return 'in progress';
    const trimmed = value.trim();
    if (trimmed.length === 0) return value;
    const numericSeconds = Number(trimmed);
    const timestamp = Number.isFinite(numericSeconds)
        ? new Date(numericSeconds * 1000)
        : new Date(trimmed);
    return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}
