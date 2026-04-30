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

export interface QpcrManualReplicateGroupInput {
  id: string;
  label: string;
  wellPositions: string[];
  targetName?: string | null;
}

export interface QpcrManualReplicateGroupAnalytics {
  id: string;
  label: string;
  targetName: string;
  wellPositions: string[];
  n: number;
  ctMean: number | null;
  ctSd: number | null;
  ctCvPercent: number | null;
  ctRange: number | null;
  quantityMean: number | null;
  quantitySd: number | null;
  quantityCvPercent: number | null;
  recoveryPercent: number | null;
  ctMeanLabel: string;
  ctSdLabel: string;
  ctCvPercentLabel: string;
  ctRangeLabel: string;
  quantityMeanLabel: string;
  quantitySdLabel: string;
  quantityCvPercentLabel: string;
  recoveryPercentLabel: string;
  sampleLabel: string;
  taskLabel: string;
  status: 'ok' | 'incomplete' | 'mixed_target' | 'no_ct';
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

export type QpcrChannelRole = 'target' | 'internal_positive_control' | 'control' | 'unknown';

export interface QpcrChannelAnalyticsRow {
  key: string;
  targetName: string;
  reporter: string;
  passiveReference: string;
  normalizationLabel: string;
  role: QpcrChannelRole;
  rows: number;
  detectedRows: number;
  wellCount: number;
  sampleCount: number;
  taskLabel: string;
  ctMean: number | null;
  ctMeanLabel: string;
  ctRangeLabel: string;
  thresholdLabel: string;
  baselineLabel: string;
}

export interface QpcrDilutionWorksheetInput {
  dilutionFactor?: number | string | null;
  expectedQuantity?: number | string | null;
}

export interface QpcrDilutionWorksheetRow {
  key: string;
  sampleName: string;
  targetName: string;
  task: string;
  reporter: string;
  passiveReference: string;
  n: number;
  wellPositions: string[];
  meanEstimatedQuantity: number | null;
  correctedQuantity: number | null;
  percentDetection: number | null;
  quantityCvPercent: number | null;
  meanEstimatedQuantityLabel: string;
  correctedQuantityLabel: string;
  percentDetectionLabel: string;
  quantityCvPercentLabel: string;
  dilutionFactor: string;
  expectedQuantity: string;
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

export function normalizeQpcrWellSelection(values: unknown[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const position = normalizeQpcrWellPosition(value);
    if (!position || seen.has(position)) continue;
    seen.add(position);
    out.push(position);
  }
  return out;
}

export function toggleQpcrWellSelection(current: string[], position: unknown, additive = true): string[] {
  const normalized = normalizeQpcrWellPosition(position);
  if (!normalized) return normalizeQpcrWellSelection(current);
  if (!additive) return [normalized];
  const selected = normalizeQpcrWellSelection(current);
  if (selected.includes(normalized)) {
    const next = selected.filter((value) => value !== normalized);
    return next.length > 0 ? next : [normalized];
  }
  return [...selected, normalized];
}

export function makeQpcrManualReplicateGroupFromSelection(
  id: string,
  label: string,
  selectedWellPositions: string[],
  targetName?: string | null,
): QpcrManualReplicateGroupInput | null {
  const positions = normalizeQpcrWellSelection(selectedWellPositions);
  if (positions.length === 0) return null;
  return { id, label: cleanText(label) ?? id, wellPositions: positions, targetName: cleanText(targetName) };
}

export function buildQpcrReplicateGroupAnalytics(
  wells: QpcrWellEntry[],
  groups: QpcrManualReplicateGroupInput[],
  options: { targetName?: string | null; assaySummary?: QpcrAssaySummaryLike | null } = {},
): QpcrManualReplicateGroupAnalytics[] {
  const activeTarget = cleanText(options.targetName);
  return groups.map((group) => {
    const positions = normalizeQpcrWellSelection(group.wellPositions);
    const explicitTarget = cleanText(group.targetName) ?? activeTarget;
    const rows = wells
      .filter((well) => {
        const position = normalizeQpcrWellPosition(well.well_position);
        if (!position || !positions.includes(position)) return false;
        return !explicitTarget || cleanText(well.target_name) === explicitTarget;
      })
      .sort(compareQpcrEntries);
    const targets = uniqueText(rows.map((row) => row.target_name));
    const ctValues = rows.map((row) => toFiniteNumber(row.ct)).filter((value): value is number => value !== null);
    const knownQuantities = rows.map((row) => toFiniteNumber(row.quantity)).filter((value): value is number => value !== null);
    const estimatedQuantities = (options.assaySummary?.quantities ?? [])
      .filter((row) => {
        const position = normalizeQpcrWellPosition(row.well_position);
        if (!position || !positions.includes(position)) return false;
        return !explicitTarget || cleanText(row.target_name) === explicitTarget;
      })
      .map((row) => toFiniteNumber(row.estimated_quantity))
      .filter((value): value is number => value !== null);
    const quantityValues = estimatedQuantities.length > 0 ? estimatedQuantities : knownQuantities;
    const ctMean = finiteMean(ctValues);
    const ctSd = sampleSd(ctValues);
    const ctCvPercent = coefficientOfVariationPercent(ctValues);
    const ctRange = ctValues.length ? Math.max(...ctValues) - Math.min(...ctValues) : null;
    const quantityMean = finiteMean(quantityValues);
    const quantitySd = sampleSd(quantityValues);
    const quantityCvPercent = coefficientOfVariationPercent(quantityValues);
    const expectedQuantity = knownQuantities.find((value) => value !== 0) ?? null;
    const recoveryPercent = quantityMean !== null && expectedQuantity !== null ? (quantityMean / expectedQuantity) * 100 : null;
    const mixedTargets = targets.length > 1 && !explicitTarget;
    const status: QpcrManualReplicateGroupAnalytics['status'] = mixedTargets
      ? 'mixed_target'
      : ctValues.length === 0
        ? 'no_ct'
        : ctValues.length < 3
          ? 'incomplete'
          : 'ok';

    return {
      id: group.id,
      label: cleanText(group.label) ?? group.id,
      targetName: explicitTarget ?? collapsedLabel(targets, 'All targets'),
      wellPositions: positions,
      n: ctValues.length,
      ctMean,
      ctSd,
      ctCvPercent,
      ctRange,
      quantityMean,
      quantitySd,
      quantityCvPercent,
      recoveryPercent,
      ctMeanLabel: formatQpcrMetric(ctMean),
      ctSdLabel: formatQpcrMetric(ctSd),
      ctCvPercentLabel: formatPercentLabel(ctCvPercent),
      ctRangeLabel: formatQpcrMetric(ctRange),
      quantityMeanLabel: formatQpcrMetric(quantityMean, 3),
      quantitySdLabel: formatQpcrMetric(quantitySd, 3),
      quantityCvPercentLabel: formatPercentLabel(quantityCvPercent),
      recoveryPercentLabel: formatPercentLabel(recoveryPercent),
      sampleLabel: collapsedLabel(uniqueText(rows.map((row) => row.sample_name)), 'No sample'),
      taskLabel: collapsedLabel(uniqueText(rows.map((row) => row.task)), 'No task'),
      status,
    };
  });
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

export function buildQpcrChannelAnalytics(wells: QpcrWellEntry[]): QpcrChannelAnalyticsRow[] {
  const groups = new Map<string, QpcrWellEntry[]>();
  for (const well of wells) {
    const target = cleanText(well.target_name) ?? 'Unknown target';
    const reporter = cleanText(well.reporter) ?? 'Unknown reporter';
    const passiveReference = cleanText(well.passive_reference) ?? 'No passive reference';
    const key = `${target}\u001f${reporter}\u001f${passiveReference}`;
    const rows = groups.get(key) ?? [];
    rows.push(well);
    groups.set(key, rows);
  }

  return [...groups.entries()].map(([key, rows]) => {
    const targetName = cleanText(rows[0]?.target_name) ?? 'Unknown target';
    const reporter = cleanText(rows[0]?.reporter) ?? 'Unknown reporter';
    const passiveReference = cleanText(rows[0]?.passive_reference) ?? 'No passive reference';
    const ctValues = rows.map((row) => toFiniteNumber(row.ct)).filter((value): value is number => value !== null);
    const ctSummary = summarizeCtValues(ctValues);
    const thresholds = rows.map((row) => toFiniteNumber(row.threshold)).filter((value): value is number => value !== null);
    const baselineStarts = rows.map((row) => toFiniteNumber(row.baseline_start)).filter((value): value is number => value !== null);
    const baselineEnds = rows.map((row) => toFiniteNumber(row.baseline_end)).filter((value): value is number => value !== null);
    const baselineLabel = baselineStarts.length || baselineEnds.length
      ? `${formatQpcrMetric(finiteMean(baselineStarts), 0)}–${formatQpcrMetric(finiteMean(baselineEnds), 0)}`
      : '--';

    return {
      key,
      targetName,
      reporter,
      passiveReference,
      normalizationLabel: passiveReference === 'No passive reference' ? 'No passive normalization reported' : `${passiveReference} passive normalization`,
      role: classifyQpcrChannelRole(targetName, rows),
      rows: rows.length,
      detectedRows: ctValues.length,
      wellCount: new Set(rows.map((row) => normalizeQpcrWellPosition(row.well_position)).filter(Boolean)).size,
      sampleCount: uniqueText(rows.map((row) => row.sample_name)).length,
      taskLabel: collapsedLabel(uniqueText(rows.map((row) => row.task)), 'No task'),
      ctMean: ctSummary.mean,
      ctMeanLabel: ctSummary.meanLabel,
      ctRangeLabel: ctSummary.rangeLabel,
      thresholdLabel: thresholds.length ? formatQpcrMetric(finiteMean(thresholds), 3) : '--',
      baselineLabel,
    };
  });
}

export function makeQpcrDilutionGroupKey(row: Pick<QpcrQuantityRow, 'sample_name' | 'target_name' | 'task'>): string {
  const sample = cleanText(row.sample_name) ?? 'Unknown sample';
  const target = cleanText(row.target_name) ?? 'Unknown target';
  const task = cleanText(row.task) ?? 'UNKNOWN';
  return `${sample}\u001f${target}\u001f${task}`;
}

export function buildQpcrDilutionWorksheetRows(
  wells: QpcrWellEntry[],
  assaySummary: QpcrAssaySummaryLike | null | undefined,
  inputs: Record<string, QpcrDilutionWorksheetInput> = {},
): QpcrDilutionWorksheetRow[] {
  const groups = new Map<string, QpcrQuantityRow[]>();
  for (const row of assaySummary?.quantities ?? []) {
    const estimatedQuantity = toFiniteNumber(row.estimated_quantity);
    if (estimatedQuantity === null) continue;
    const key = makeQpcrDilutionGroupKey(row);
    const rows = groups.get(key) ?? [];
    rows.push(row);
    groups.set(key, rows);
  }

  const wellEntryLookup = new Map<string, QpcrWellEntry[]>();
  for (const well of wells) {
    const position = normalizeQpcrWellPosition(well.well_position);
    if (!position) continue;
    const rows = wellEntryLookup.get(position) ?? [];
    rows.push(well);
    wellEntryLookup.set(position, rows);
  }

  return [...groups.entries()].map(([key, rows]) => {
    const quantities = rows.map((row) => toFiniteNumber(row.estimated_quantity)).filter((value): value is number => value !== null);
    const meanEstimatedQuantity = finiteMean(quantities);
    const quantityCvPercent = coefficientOfVariationPercent(quantities);
    const input = inputs[key] ?? {};
    const dilutionFactorValue = toFiniteNumber(input.dilutionFactor);
    const expectedQuantityValue = toFiniteNumber(input.expectedQuantity);
    const correctedQuantity = meanEstimatedQuantity !== null && dilutionFactorValue !== null
      ? meanEstimatedQuantity * dilutionFactorValue
      : null;
    const percentDetection = correctedQuantity !== null && expectedQuantityValue !== null && expectedQuantityValue !== 0
      ? (correctedQuantity / expectedQuantityValue) * 100
      : null;
    const wellPositions = uniqueNormalizedWellPositions(rows.map((row) => row.well_position));
    const matchedWellEntries = wellPositions.flatMap((position) => wellEntryLookup.get(position) ?? [])
      .filter((entry) => rowMatchesEntry(rows[0] ?? {}, entry));
    const reporter = collapsedLabel(uniqueText(matchedWellEntries.map((entry) => entry.reporter)), '--');
    const passiveReference = collapsedLabel(uniqueText(matchedWellEntries.map((entry) => entry.passive_reference)), '--');

    return {
      key,
      sampleName: cleanText(rows[0]?.sample_name) ?? 'Unknown sample',
      targetName: cleanText(rows[0]?.target_name) ?? 'Unknown target',
      task: cleanText(rows[0]?.task) ?? 'UNKNOWN',
      reporter,
      passiveReference,
      n: quantities.length,
      wellPositions,
      meanEstimatedQuantity,
      correctedQuantity,
      percentDetection,
      quantityCvPercent,
      meanEstimatedQuantityLabel: formatQpcrMetric(meanEstimatedQuantity, 3),
      correctedQuantityLabel: formatQpcrMetric(correctedQuantity, 3),
      percentDetectionLabel: formatPercentLabel(percentDetection),
      quantityCvPercentLabel: formatPercentLabel(quantityCvPercent),
      dilutionFactor: input.dilutionFactor === null || input.dilutionFactor === undefined ? '' : String(input.dilutionFactor),
      expectedQuantity: input.expectedQuantity === null || input.expectedQuantity === undefined ? '' : String(input.expectedQuantity),
    };
  });
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

function coefficientOfVariationPercent(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = finiteMean(values);
  if (mean === null || mean === 0) return null;
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1);
  return (Math.sqrt(variance) / Math.abs(mean)) * 100;
}

function sampleSd(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = finiteMean(values);
  if (mean === null) return null;
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function uniqueNormalizedWellPositions(values: unknown[]): string[] {
  return uniqueText(values.map((value) => normalizeQpcrWellPosition(value))).sort(compareWellPositions);
}

function compareWellPositions(a: string, b: string): number {
  const rowDelta = a.charCodeAt(0) - b.charCodeAt(0);
  if (rowDelta !== 0) return rowDelta;
  return Number(a.slice(1)) - Number(b.slice(1));
}

function classifyQpcrChannelRole(targetName: string, rows: QpcrWellEntry[]): QpcrChannelRole {
  const target = targetName.toUpperCase();
  const rowText = rows.map((row) => `${row.sample_name ?? ''} ${row.target_name ?? ''} ${row.task ?? ''}`).join(' ').toUpperCase();
  if (/\b(IPC|IAC|INTERNAL\s+POSITIVE|INTERNAL\s+CONTROL)\b/.test(`${target} ${rowText}`)) {
    return 'internal_positive_control';
  }
  if (/\b(NTC|NEGATIVE|NO\s+TEMPLATE|POSITIVE\s+CONTROL|CONTROL)\b/.test(`${target} ${rowText}`)) {
    return 'control';
  }
  if (targetName === 'Unknown target') return 'unknown';
  return 'target';
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
  row: { sample_name?: string | null; target_name?: string | null; task?: string | null; quantity?: number | string | null },
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
