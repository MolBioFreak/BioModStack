export interface ParsedAssayTable {
    headers: string[];
    rows: Array<Record<string, string>>;
    delimiter: string;
    warnings: string[];
}

export interface AssayQcColumns {
    value: string;
    group?: string;
    run?: string;
    sample?: string;
    replicate?: string;
    include?: string;
}

export interface AssayQcConfig {
    columns: AssayQcColumns;
    groupRulesText?: string;
    excludeTermsText?: string;
    defaultGroup?: string;
    defaultRun?: string;
    zScoreThreshold?: number;
    cvWarningThreshold?: number;
}

export interface AssayQcStatRow {
    key: string;
    groupId?: string;
    runId?: string;
    n: number;
    mean: number;
    median: number;
    sd: number | null;
    cvPercent: number | null;
    min: number;
    max: number;
    warningFlags: string[];
}

export interface AssayQcCrossRunRow {
    groupId: string;
    nRuns: number;
    totalN: number;
    runMeans: Array<{ runId: string; mean: number; n: number }>;
    meanOfRunMeans: number;
    betweenRunSd: number | null;
    betweenRunCvPercent: number | null;
    warningFlags: string[];
}

export interface SanitizedAssayRow {
    rowNumber: number;
    sampleId: string;
    runId: string;
    rawGroupId: string;
    groupId: string;
    replicateId: string;
    rawValue: string;
    value: number | null;
    included: boolean;
    exclusionReason?: string;
    flags: string[];
    source: Record<string, string>;
}

export interface AssayQcResult {
    rows: SanitizedAssayRow[];
    includedRows: SanitizedAssayRow[];
    excludedRows: SanitizedAssayRow[];
    groupStats: AssayQcStatRow[];
    runStats: AssayQcStatRow[];
    groupRunStats: AssayQcStatRow[];
    crossRunStats: AssayQcCrossRunRow[];
    warnings: string[];
    summary: {
        totalRows: number;
        includedRows: number;
        excludedRows: number;
        numericRows: number;
        groupCount: number;
        runCount: number;
        outlierFlagCount: number;
    };
}

interface GroupRule {
    target: string;
    tokens: string[];
}

const DEFAULT_GROUP = 'Ungrouped';
const DEFAULT_RUN = 'Run 1';
const EPSILON = 1e-12;

function normalizeHeader(header: string): string {
    return header.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function ensureUniqueHeaders(headers: string[]): string[] {
    const counts = new Map<string, number>();
    return headers.map((rawHeader, index) => {
        const base = rawHeader.trim() || `column_${index + 1}`;
        const seen = counts.get(base) ?? 0;
        counts.set(base, seen + 1);
        return seen === 0 ? base : `${base}_${seen + 1}`;
    });
}

function countDelimiterOutsideQuotes(line: string, delimiter: string): number {
    let count = 0;
    let inQuote = false;
    for (let i = 0; i < line.length; i += 1) {
        const char = line[i];
        if (char === '"') {
            if (inQuote && line[i + 1] === '"') {
                i += 1;
            } else {
                inQuote = !inQuote;
            }
        } else if (!inQuote && char === delimiter) {
            count += 1;
        }
    }
    return count;
}

function detectDelimiter(lines: string[]): string {
    const candidates = ['\t', ',', ';', '|'];
    let best = ',';
    let bestScore = -1;
    for (const candidate of candidates) {
        const counts = lines.slice(0, 5).map((line) => countDelimiterOutsideQuotes(line, candidate));
        const nonZero = counts.filter((count) => count > 0);
        const score = nonZero.length * 10 + nonZero.reduce((sum, count) => sum + count, 0);
        if (score > bestScore) {
            best = candidate;
            bestScore = score;
        }
    }
    return best;
}

function parseDelimitedLine(line: string, delimiter: string): string[] {
    const cells: string[] = [];
    let current = '';
    let inQuote = false;
    for (let i = 0; i < line.length; i += 1) {
        const char = line[i];
        if (char === '"') {
            if (inQuote && line[i + 1] === '"') {
                current += '"';
                i += 1;
            } else {
                inQuote = !inQuote;
            }
        } else if (!inQuote && char === delimiter) {
            cells.push(current.trim());
            current = '';
        } else {
            current += char;
        }
    }
    cells.push(current.trim());
    return cells;
}

export function parseDelimitedTable(text: string): ParsedAssayTable {
    const warnings: string[] = [];
    const lines = text
        .replace(/^\ufeff/, '')
        .split(/\r?\n/)
        .map((line) => line.trimEnd())
        .filter((line) => line.trim().length > 0);

    if (lines.length === 0) {
        return { headers: [], rows: [], delimiter: ',', warnings: [] };
    }

    const delimiter = detectDelimiter(lines);
    const headers = ensureUniqueHeaders(parseDelimitedLine(lines[0], delimiter));
    const rows = lines.slice(1).map((line, rowIndex) => {
        const cells = parseDelimitedLine(line, delimiter);
        if (cells.length !== headers.length) {
            warnings.push(`Row ${rowIndex + 2} has ${cells.length} cells; expected ${headers.length}. Missing cells are left blank and extra cells are ignored.`);
        }
        return headers.reduce<Record<string, string>>((record, header, index) => {
            record[header] = cells[index] ?? '';
            return record;
        }, {});
    });

    return { headers, rows, delimiter, warnings };
}

function firstHeaderMatch(headers: string[], patterns: RegExp[]): string {
    return headers.find((header) => {
        const normalized = normalizeHeader(header);
        return patterns.some((pattern) => pattern.test(normalized));
    }) ?? '';
}

export function detectAssayQcColumns(headers: string[]): AssayQcColumns {
    const value = firstHeaderMatch(headers, [
        /^(primary_)?area(_percent)?$/,
        /peak_area/,
        /total_area/,
        /response/,
        /amount/,
        /concentration/,
        /recovery/,
        /purity/,
        /percent_primary/,
        /value/,
        /cq$/,
        /ct$/,
        /height/,
        /signal/,
    ]);
    const group = firstHeaderMatch(headers, [/sample_type/, /sample_role/, /condition/, /treatment/, /group/, /dose/, /level/, /lot/, /batch_group/, /isoform/]);
    const run = firstHeaderMatch(headers, [/run_date/, /^run$/, /batch/, /sequence/, /plate/, /assay_run/, /import/, /date/, /day/]);
    const sample = firstHeaderMatch(headers, [/sample_name/, /^sample$/, /sample_id/, /^id$/, /well/, /injection_name/, /name/]);
    const replicate = firstHeaderMatch(headers, [/replicate/, /^rep$/, /injection_number/, /^inj/, /technical_rep/]);
    const include = firstHeaderMatch(headers, [/include/, /^use$/, /^keep$/, /exclude/, /reject/, /omit/, /is_excluded/, /qc_status/, /flag/]);
    return { value, group, run, sample, replicate, include };
}

export function sanitizeNumericValue(rawValue: string): number | null {
    const normalized = rawValue
        .trim()
        .replace(/[−–—]/g, '-')
        .replace(/,/g, '')
        .replace(/%/g, '')
        .replace(/≤|>=|≥|<=|~|≈/g, '')
        .replace(/\b(ug|µg|mg|ng|ml|mL|uL|µL|min|sec|s|au|rfu|copies|x)\b/gi, ' ');
    if (!normalized || /^(na|n\/a|nan|null|none|undetermined|undet|nd|not detected)$/i.test(normalized)) {
        return null;
    }
    const match = normalized.match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/);
    if (!match) return null;
    const value = Number(match[0]);
    return Number.isFinite(value) ? value : null;
}

export function parseGroupRules(text = ''): GroupRule[] {
    return text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
            if (line.includes('->')) {
                const [tokensPart, targetPart] = line.split('->');
                return { target: targetPart.trim(), tokens: splitRuleTokens(tokensPart) };
            }
            const match = line.match(/^(.*?)\s*(?:=>|=|:)\s*(.*)$/);
            if (!match) {
                return { target: line, tokens: [line] };
            }
            return { target: match[1].trim(), tokens: splitRuleTokens(match[2]) };
        })
        .filter((rule) => rule.target.length > 0 && rule.tokens.length > 0);
}

function splitRuleTokens(text: string): string[] {
    return text
        .split(/[,;|]/)
        .map((token) => token.trim())
        .filter(Boolean);
}

function parseExcludeTerms(text = ''): string[] {
    return text
        .split(/\r?\n/)
        .flatMap((line) => line.split(/[,;|]/))
        .map((term) => term.trim().toLowerCase())
        .filter(Boolean);
}

function cell(row: Record<string, string>, header?: string): string {
    if (!header) return '';
    return row[header] ?? '';
}

function applyGroupRules(rawGroup: string, sampleId: string, rules: GroupRule[], fallback: string): string {
    const haystacks = [rawGroup, sampleId].map((value) => value.toLowerCase());
    for (const rule of rules) {
        if (rule.tokens.some((token) => haystacks.some((haystack) => haystack.includes(token.toLowerCase())))) {
            return rule.target;
        }
    }
    return rawGroup || fallback;
}

function flagRejectsRow(columnName: string, rawValue: string): string | null {
    if (!columnName || !rawValue.trim()) return null;
    const header = normalizeHeader(columnName);
    const value = rawValue.trim().toLowerCase();
    const truthy = /^(true|t|yes|y|1|exclude|excluded|reject|rejected|omit|omitted|fail|failed|bad)$/i.test(value);
    const falsy = /^(false|f|no|n|0|include|included|keep|kept|pass|passed|ok|good)$/i.test(value);
    if (/exclude|reject|omit|is_excluded|flag/.test(header) && truthy) {
        return `Rejected by ${columnName}`;
    }
    if (/include|use|keep|qc_status/.test(header) && falsy) {
        return `Rejected by ${columnName}`;
    }
    return null;
}

function manualTermRejectsRow(row: SanitizedAssayRow, terms: string[]): string | null {
    if (terms.length === 0) return null;
    const haystack = [row.sampleId, row.runId, row.rawGroupId, row.groupId, row.replicateId]
        .join(' ')
        .toLowerCase();
    const match = terms.find((term) => haystack.includes(term));
    return match ? `Manual exclude term: ${match}` : null;
}

function mean(values: number[]): number {
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values: number[]): number {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function sampleSd(values: number[]): number | null {
    if (values.length < 2) return null;
    const avg = mean(values);
    const variance = values.reduce((sum, value) => sum + (value - avg) ** 2, 0) / (values.length - 1);
    return Math.sqrt(variance);
}

function summarizeValues(key: string, values: number[], metadata: Pick<AssayQcStatRow, 'groupId' | 'runId'>, cvWarningThreshold: number): AssayQcStatRow | null {
    if (values.length === 0) return null;
    const avg = mean(values);
    const sd = sampleSd(values);
    const cvPercent = sd === null || Math.abs(avg) < EPSILON ? null : (sd / Math.abs(avg)) * 100;
    const warningFlags = cvPercent !== null && cvPercent > cvWarningThreshold ? [`CV ${cvPercent.toFixed(1)}% > ${cvWarningThreshold}%`] : [];
    return {
        key,
        ...metadata,
        n: values.length,
        mean: avg,
        median: median(values),
        sd,
        cvPercent,
        min: Math.min(...values),
        max: Math.max(...values),
        warningFlags,
    };
}

function groupBy<T>(items: T[], keyFor: (item: T) => string): Map<string, T[]> {
    const grouped = new Map<string, T[]>();
    for (const item of items) {
        const key = keyFor(item);
        const existing = grouped.get(key) ?? [];
        existing.push(item);
        grouped.set(key, existing);
    }
    return grouped;
}

function annotateOutliers(rows: SanitizedAssayRow[], zScoreThreshold: number): void {
    if (!Number.isFinite(zScoreThreshold) || zScoreThreshold <= 0) return;
    const grouped = groupBy(rows.filter((row) => row.included && row.value !== null), (row) => row.groupId);
    for (const groupRows of grouped.values()) {
        const values = groupRows.map((row) => row.value as number);
        const avg = mean(values);
        const sd = sampleSd(values);
        if (sd === null || sd < EPSILON) continue;
        for (const row of groupRows) {
            const z = Math.abs(((row.value as number) - avg) / sd);
            if (z > zScoreThreshold) {
                row.flags.push(`z-score ${z.toFixed(2)} > ${zScoreThreshold}`);
            }
        }
    }
}

export function analyzeAssayQcTable(parsed: ParsedAssayTable, config: AssayQcConfig): AssayQcResult {
    const warnings = [...parsed.warnings];
    const { columns } = config;
    const defaultGroup = config.defaultGroup || DEFAULT_GROUP;
    const defaultRun = config.defaultRun || DEFAULT_RUN;
    const zScoreThreshold = config.zScoreThreshold ?? 3;
    const cvWarningThreshold = config.cvWarningThreshold ?? 10;
    const groupRules = parseGroupRules(config.groupRulesText);
    const excludeTerms = parseExcludeTerms(config.excludeTermsText);

    if (!columns.value) {
        warnings.push('Select a numeric response/value column before running QC.');
    }

    const rows = parsed.rows.map<SanitizedAssayRow>((source, index) => {
        const rawValue = cell(source, columns.value);
        const value = columns.value ? sanitizeNumericValue(rawValue) : null;
        const sampleId = cell(source, columns.sample) || `Row ${index + 2}`;
        const runId = cell(source, columns.run) || defaultRun;
        const rawGroupId = cell(source, columns.group) || defaultGroup;
        const groupId = applyGroupRules(rawGroupId, sampleId, groupRules, defaultGroup);
        const replicateId = cell(source, columns.replicate) || '';
        const flags: string[] = [];
        let included = true;
        let exclusionReason: string | undefined;

        if (value === null) {
            included = false;
            exclusionReason = `Non-numeric value in ${columns.value || 'value column'}`;
            flags.push('non_numeric_value');
        }

        const flagReason = columns.include ? flagRejectsRow(columns.include, cell(source, columns.include)) : null;
        if (flagReason) {
            included = false;
            exclusionReason = flagReason;
            flags.push('rejected_by_flag');
        }

        const row: SanitizedAssayRow = {
            rowNumber: index + 2,
            sampleId,
            runId,
            rawGroupId,
            groupId,
            replicateId,
            rawValue,
            value,
            included,
            exclusionReason,
            flags,
            source,
        };
        const manualReason = manualTermRejectsRow(row, excludeTerms);
        if (manualReason) {
            row.included = false;
            row.exclusionReason = manualReason;
            row.flags.push('manual_exclude');
        }
        return row;
    });

    annotateOutliers(rows, zScoreThreshold);

    const includedRows = rows.filter((row) => row.included && row.value !== null);
    const excludedRows = rows.filter((row) => !row.included);
    const numericRows = rows.filter((row) => row.value !== null);

    const summarizeGroupedRows = (
        grouped: Map<string, SanitizedAssayRow[]>,
        metadataFor: (key: string, groupRows: SanitizedAssayRow[]) => Pick<AssayQcStatRow, 'groupId' | 'runId'>,
    ): AssayQcStatRow[] => Array.from(grouped.entries())
        .map(([key, groupRows]) => summarizeValues(key, groupRows.map((row) => row.value as number), metadataFor(key, groupRows), cvWarningThreshold))
        .filter((row): row is AssayQcStatRow => row !== null)
        .sort((a, b) => a.key.localeCompare(b.key));

    const groupStats = summarizeGroupedRows(groupBy(includedRows, (row) => row.groupId), (groupId) => ({ groupId }));
    const runStats = summarizeGroupedRows(groupBy(includedRows, (row) => row.runId), (runId) => ({ runId }));
    const groupRunStats = summarizeGroupedRows(
        groupBy(includedRows, (row) => `${row.groupId}||${row.runId}`),
        (_key, groupRows) => ({ groupId: groupRows[0]?.groupId, runId: groupRows[0]?.runId }),
    );

    const crossRunStats = Array.from(groupBy(groupRunStats, (row) => row.groupId ?? defaultGroup).entries())
        .map(([groupId, stats]) => {
            const runMeans = stats
                .filter((row) => row.runId)
                .map((row) => ({ runId: row.runId as string, mean: row.mean, n: row.n }))
                .sort((a, b) => a.runId.localeCompare(b.runId));
            const values = runMeans.map((row) => row.mean);
            const meanOfRunMeans = values.length ? mean(values) : 0;
            const betweenRunSd = sampleSd(values);
            const betweenRunCvPercent = betweenRunSd === null || Math.abs(meanOfRunMeans) < EPSILON ? null : (betweenRunSd / Math.abs(meanOfRunMeans)) * 100;
            const warningFlags = betweenRunCvPercent !== null && betweenRunCvPercent > cvWarningThreshold
                ? [`between-run CV ${betweenRunCvPercent.toFixed(1)}% > ${cvWarningThreshold}%`]
                : [];
            return {
                groupId,
                nRuns: runMeans.length,
                totalN: stats.reduce((sum, row) => sum + row.n, 0),
                runMeans,
                meanOfRunMeans,
                betweenRunSd,
                betweenRunCvPercent,
                warningFlags,
            };
        })
        .sort((a, b) => a.groupId.localeCompare(b.groupId));

    return {
        rows,
        includedRows,
        excludedRows,
        groupStats,
        runStats,
        groupRunStats,
        crossRunStats,
        warnings,
        summary: {
            totalRows: rows.length,
            includedRows: includedRows.length,
            excludedRows: excludedRows.length,
            numericRows: numericRows.length,
            groupCount: new Set(includedRows.map((row) => row.groupId)).size,
            runCount: new Set(includedRows.map((row) => row.runId)).size,
            outlierFlagCount: rows.filter((row) => row.flags.some((flag) => flag.startsWith('z-score'))).length,
        },
    };
}

function csvCell(value: string | number | null | undefined): string {
    const text = value === null || value === undefined ? '' : String(value);
    if (/[",\n]/.test(text)) {
        return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
}

export function exportCleanedAssayRowsCsv(rows: SanitizedAssayRow[]): string {
    const headers = ['rowNumber', 'included', 'sampleId', 'runId', 'rawGroupId', 'groupId', 'replicateId', 'rawValue', 'value', 'exclusionReason', 'flags'];
    const body = rows.map((row) => [
        row.rowNumber,
        row.included ? 'yes' : 'no',
        row.sampleId,
        row.runId,
        row.rawGroupId,
        row.groupId,
        row.replicateId,
        row.rawValue,
        row.value ?? '',
        row.exclusionReason ?? '',
        row.flags.join('|'),
    ].map(csvCell).join(','));
    return [headers.join(','), ...body].join('\n');
}
