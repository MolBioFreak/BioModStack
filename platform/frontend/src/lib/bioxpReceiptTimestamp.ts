export function bioXpReceiptTimestampText(value: string | number | null): string {
    if (value === null) return 'in progress';
    const text = String(value);
    const trimmed = text.trim();
    if (trimmed.length === 0) return text;
    const numericSeconds = Number(trimmed);
    const timestamp = Number.isFinite(numericSeconds)
        ? new Date(numericSeconds * 1000)
        : new Date(trimmed);
    return Number.isNaN(timestamp.getTime()) ? text : timestamp.toLocaleString();
}
