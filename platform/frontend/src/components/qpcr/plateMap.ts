export const QPCR_PLATE_ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] as const;
export const QPCR_PLATE_COLUMNS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] as const;

export type QpcrPlateStatus = 'empty' | 'sample' | 'standard' | 'ntc' | 'review' | 'mixed';

export interface QpcrWellEntry {
  well_position?: string | null;
  sample_name?: string | null;
  target_name?: string | null;
  task?: string | null;
  ct?: number | string | null;
  quantity?: number | string | null;
  ct_status?: string | null;
  reporter?: string | null;
  quencher?: string | null;
  passive_reference?: string | null;
  threshold?: number | string | null;
  baseline_start?: number | string | null;
  baseline_end?: number | string | null;
  ct_source?: string | null;
  group?: string | null;
  [key: string]: unknown;
}

export interface QpcrQuantityRow {
  well_position?: string | null;
  sample_name?: string | null;
  target_name?: string | null;
  task?: string | null;
  ct?: number | string | null;
  estimated_quantity?: number | string | null;
  log10_estimated_quantity?: number | string | null;
  [key: string]: unknown;
}

export interface QpcrReplicateQcRow {
  well_positions?: unknown;
  sample_name?: string | null;
  target_name?: string | null;
  task?: string | null;
  quantity?: number | string | null;
  n?: number | string | null;
  ct_mean?: number | string | null;
  ct_sd?: number | string | null;
  ct_cv_percent?: number | string | null;
  ct_range?: number | string | null;
  [key: string]: unknown;
}

export interface QpcrSpikeRecoveryRow {
  well_position?: string | null;
  sample_name?: string | null;
  target_name?: string | null;
  recovery_percent?: number | string | null;
  [key: string]: unknown;
}

export interface QpcrAssaySummaryLike {
  quantities?: QpcrQuantityRow[];
  replicate_qc?: QpcrReplicateQcRow[];
  spike_recovery?: QpcrSpikeRecoveryRow[];
}

export interface QpcrCtSummary {
  count: number;
  mean: number | null;
  min: number | null;
  max: number | null;
  range: number | null;
  meanLabel: string;
  minLabel: string;
  maxLabel: string;
  rangeLabel: string;
}

export interface QpcrPlateWellSummary {
  position: string;
  row: string;
  column: number;
  entries: QpcrWellEntry[];
  samples: string[];
  targets: string[];
  tasks: string[];
  sampleLabel: string;
  targetLabel: string;
  taskLabel: string;
  status: QpcrPlateStatus;
  ctCount: number;
  ctSummary: QpcrCtSummary;
  ctMeanLabel: string;
}

export interface QpcrSelectedWellAnalytics extends QpcrPlateWellSummary {
  quantities: QpcrQuantityRow[];
  replicateQc: QpcrReplicateQcRow[];
  spikeRecovery: QpcrSpikeRecoveryRow[];
}

export interface QpcrAssayReviewMetrics {
  parsedRows: number;
  populatedWells: number;
  targetCount: number;
  standardRows: number;
  sampleRows: number;
  ntcRows: number;
  reviewRows: number;
  replicateGroups: number;
  replicateCvMean: number | null;
  replicateCvMax: number | null;
  replicateCvMeanLabel: string;
  replicateCvMaxLabel: string;
  spikeRecoveryCount: number;
  spikeRecoveryMean: number | null;
  spikeRecoveryMin: number | null;
  spikeRecoveryMax: number | null;
  spikeRecoveryMeanLabel: string;
  spikeRecoveryRangeLabel: string;
}

export function normalizeQpcrWellPosition(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim().toUpperCase();
  const match = /^([A-H])\s*0*([1-9]|1[0-2])$/.exec(raw);
  if (!match) return null;
  return `${match[1]}${Number(match[2])}`;
}

export function formatQpcrMetric(value: unknown, digits = 3, empty = '--'): string {
  const numberValue = toFiniteNumber(value);
  if (numberValue === null) {
    if (value === null || value === undefined || value === '') return empty;
    return String(value);
  }
  const abs = Math.abs(numberValue);
  if ((abs >= 100000 || (abs > 0 && abs < 0.001)) && digits >= 2) {
    return numberValue.toExponential(Math.min(digits, 4));
  }
  return numberValue.toFixed(digits);
}

export function buildQpcrTargetOptions(wells: QpcrWellEntry[]): string[] {
  return uniqueText(wells.map((well) => well.target_name));
}

export function buildQpcrPlateMap(
  wells: QpcrWellEntry[],
  options: { targetName?: string | null } = {},
): QpcrPlateWellSummary[] {
  const targetName = cleanText(options.targetName);
  const grouped = new Map<string, QpcrWellEntry[]>();

  for (const well of wells) {
    const position = normalizeQpcrWellPosition(well.well_position);
    if (!position) continue;
    if (targetName && cleanText(well.target_name) !== targetName) continue;
    const entries = grouped.get(position) ?? [];
    entries.push(well);
    grouped.set(position, entries);
  }

  const summaries: QpcrPlateWellSummary[] = [];
  for (const row of QPCR_PLATE_ROWS) {
    for (const column of QPCR_PLATE_COLUMNS) {
      const position = `${row}${column}`;
      summaries.push(summarizePlateWell(position, grouped.get(position) ?? []));
    }
  }
  return summaries;
}

export function buildSelectedQpcrWellAnalytics(
  position: unknown,
  wells: QpcrWellEntry[],
  assaySummary: QpcrAssaySummaryLike | null | undefined,
  targetName?: string | null,
): QpcrSelectedWellAnalytics {
  const normalizedPosition = normalizeQpcrWellPosition(position) ?? 'A1';
  const cleanTarget = cleanText(targetName);
  const entries = wells
    .filter((well) => normalizeQpcrWellPosition(well.well_position) === normalizedPosition)
    .filter((well) => !cleanTarget || cleanText(well.target_name) === cleanTarget)
    .sort(compareQpcrEntries);
  const base = summarizePlateWell(normalizedPosition, entries);
  return {
    ...base,
    quantities: (assaySummary?.quantities ?? []).filter((row) => matchesSelectedWell(row, normalizedPosition, entries)),
    replicateQc: (assaySummary?.replicate_qc ?? []).filter((row) => matchesReplicateQc(row, normalizedPosition, entries)),
    spikeRecovery: (assaySummary?.spike_recovery ?? []).filter((row) => matchesSelectedWell(row, normalizedPosition, entries)),
  };
}

export function buildQpcrAssayReviewMetrics(
  wells: QpcrWellEntry[],
  assaySummary: QpcrAssaySummaryLike | null | undefined,
): QpcrAssayReviewMetrics {
  const plateMap = buildQpcrPlateMap(wells);
  const taskText = (well: QpcrWellEntry) => (cleanText(well.task) ?? '').toUpperCase();
  const standardRows = wells.filter((well) => taskText(well).includes('STANDARD')).length;
  const ntcRows = wells.filter((well) => taskText(well).includes('NTC') || (cleanText(well.sample_name) ?? '').toUpperCase().includes('NTC')).length;
  const reviewRows = wells.filter((well) => cleanText(well.ct_status)).length;
  const sampleRows = Math.max(0, wells.length - standardRows - ntcRows);
  const replicateCvValues = (assaySummary?.replicate_qc ?? [])
    .map((row) => toFiniteNumber(row.ct_cv_percent))
    .filter((value): value is number => value !== null);
  const spikeRecoveryValues = (assaySummary?.spike_recovery ?? [])
    .map((row) => toFiniteNumber(row.recovery_percent))
    .filter((value): value is number => value !== null);
  const replicateCvMean = finiteMean(replicateCvValues);
  const replicateCvMax = replicateCvValues.length ? Math.max(...replicateCvValues) : null;
  const spikeRecoveryMean = finiteMean(spikeRecoveryValues);
  const spikeRecoveryMin = spikeRecoveryValues.length ? Math.min(...spikeRecoveryValues) : null;
  const spikeRecoveryMax = spikeRecoveryValues.length ? Math.max(...spikeRecoveryValues) : null;

  return {
    parsedRows: wells.length,
    populatedWells: plateMap.filter((well) => well.entries.length > 0).length,
    targetCount: uniqueText(wells.map((well) => well.target_name)).length,
    standardRows,
    sampleRows,
    ntcRows,
    reviewRows,
    replicateGroups: assaySummary?.replicate_qc?.length ?? 0,
    replicateCvMean,
    replicateCvMax,
    replicateCvMeanLabel: formatPercentLabel(replicateCvMean),
    replicateCvMaxLabel: formatPercentLabel(replicateCvMax),
    spikeRecoveryCount: assaySummary?.spike_recovery?.length ?? 0,
    spikeRecoveryMean,
    spikeRecoveryMin,
    spikeRecoveryMax,
    spikeRecoveryMeanLabel: formatPercentLabel(spikeRecoveryMean),
    spikeRecoveryRangeLabel: formatPercentRangeLabel(spikeRecoveryMin, spikeRecoveryMax),
  };
}

function summarizePlateWell(position: string, entries: QpcrWellEntry[]): QpcrPlateWellSummary {
  const normalized = normalizeQpcrWellPosition(position) ?? position;
  const row = normalized.slice(0, 1);
  const column = Number(normalized.slice(1));
  const sortedEntries = [...entries].sort(compareQpcrEntries);
  const samples = uniqueText(sortedEntries.map((entry) => entry.sample_name));
  const targets = uniqueText(sortedEntries.map((entry) => entry.target_name));
  const tasks = uniqueText(sortedEntries.map((entry) => entry.task));
  const ctValues = sortedEntries.map((entry) => toFiniteNumber(entry.ct)).filter((value): value is number => value !== null);
  const ctSummary = summarizeCtValues(ctValues);
  return {
    position: normalized,
    row,
    column,
    entries: sortedEntries,
    samples,
    targets,
    tasks,
    sampleLabel: collapsedLabel(samples, 'Empty'),
    targetLabel: collapsedLabel(targets, 'No target'),
    taskLabel: collapsedLabel(tasks, 'No task'),
    status: classifyPlateWell(sortedEntries, tasks, ctValues),
    ctCount: ctSummary.count,
    ctSummary,
    ctMeanLabel: ctSummary.count > 0 ? ctSummary.meanLabel : 'Undet.',
  };
}

function summarizeCtValues(values: number[]): QpcrCtSummary {
  if (values.length === 0) {
    return {
      count: 0,
      mean: null,
      min: null,
      max: null,
      range: null,
      meanLabel: 'Undet.',
      minLabel: '--',
      maxLabel: '--',
      rangeLabel: '--',
    };
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  return {
    count: values.length,
    mean,
    min,
    max,
    range,
    meanLabel: formatQpcrMetric(mean),
    minLabel: formatQpcrMetric(min),
    maxLabel: formatQpcrMetric(max),
    rangeLabel: formatQpcrMetric(range),
  };
}

function classifyPlateWell(entries: QpcrWellEntry[], tasks: string[], ctValues: number[]): QpcrPlateStatus {
  if (entries.length === 0) return 'empty';
  const hasCtStatus = entries.some((entry) => cleanText(entry.ct_status));
  const upperTasks = tasks.map((task) => task.toUpperCase());
  const isNtc = upperTasks.some((task) => task.includes('NTC')) || entries.some((entry) => cleanText(entry.sample_name)?.toUpperCase().includes('NTC'));
  if (isNtc && ctValues.length > 0) return 'review';
  if (hasCtStatus) return 'review';
  if (tasks.length > 1) return 'mixed';
  if (upperTasks.some((task) => task.includes('STANDARD'))) return 'standard';
  if (isNtc) return 'ntc';
  return 'sample';
}

function collapsedLabel(values: string[], empty: string): string {
  if (values.length === 0) return empty;
  if (values.length === 1) return values[0];
  return `${values[0]} +${values.length - 1}`;
}

function uniqueText(values: unknown[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const text = cleanText(value);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

function cleanText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text ? text : null;
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function finiteMean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatPercentLabel(value: number | null): string {
  return value === null ? '--' : `${formatQpcrMetric(value, 2)}%`;
}

function formatPercentRangeLabel(min: number | null, max: number | null): string {
  if (min === null || max === null) return '--';
  return `${formatQpcrMetric(min, 2)}–${formatQpcrMetric(max, 2)}%`;
}

function compareQpcrEntries(a: QpcrWellEntry, b: QpcrWellEntry): number {
  return `${cleanText(a.target_name) ?? ''}|${cleanText(a.task) ?? ''}|${cleanText(a.sample_name) ?? ''}`.localeCompare(
    `${cleanText(b.target_name) ?? ''}|${cleanText(b.task) ?? ''}|${cleanText(b.sample_name) ?? ''}`,
  );
}

function matchesSelectedWell(
  row: QpcrQuantityRow | QpcrSpikeRecoveryRow,
  position: string,
  entries: QpcrWellEntry[],
): boolean {
  const rowPosition = normalizeQpcrWellPosition(row.well_position);
  if (rowPosition === position) return true;
  return entries.some((entry) => rowMatchesEntry(row, entry));
}

function matchesReplicateQc(row: QpcrReplicateQcRow, position: string, entries: QpcrWellEntry[]): boolean {
  const wellPositions = Array.isArray(row.well_positions) ? row.well_positions : [];
  if (wellPositions.some((wellPosition) => normalizeQpcrWellPosition(wellPosition) === position)) return true;
  return entries.some((entry) => rowMatchesEntry(row, entry));
}

function rowMatchesEntry(
  row: Pick<QpcrWellEntry, 'sample_name' | 'target_name' | 'task' | 'quantity'>,
  entry: QpcrWellEntry,
): boolean {
  const rowSample = cleanText(row.sample_name);
  const rowTarget = cleanText(row.target_name);
  if (!rowSample || !rowTarget) return false;
  if (rowSample !== cleanText(entry.sample_name) || rowTarget !== cleanText(entry.target_name)) return false;
  const rowTask = cleanText(row.task);
  if (rowTask && rowTask !== cleanText(entry.task)) return false;
  const rowQuantity = toFiniteNumber(row.quantity);
  const entryQuantity = toFiniteNumber(entry.quantity);
  if (rowQuantity !== null && entryQuantity !== null && Math.abs(rowQuantity - entryQuantity) > 1e-9) return false;
  return true;
}
