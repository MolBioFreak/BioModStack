export type QpcrRawImportTab = 'heatmap' | 'curves' | 'table' | 'stdcurve';

type PlotlyLikePayload = {
  data?: unknown[];
};

type QpcrRawImportResponseLike = {
  results_plotly_json?: PlotlyLikePayload | null;
  amplification_plotly_json?: PlotlyLikePayload | null;
  standard_curve_plotly_json?: PlotlyLikePayload | null;
};

type PlotlyTraceLike = Record<string, unknown>;

function hasPlotData(payload: PlotlyLikePayload | null | undefined): boolean {
  return Array.isArray(payload?.data) && payload.data.length > 0;
}

export function resolveQpcrInitialTab(response: QpcrRawImportResponseLike): QpcrRawImportTab {
  if (hasPlotData(response.standard_curve_plotly_json)) {
    return 'stdcurve';
  }
  if (hasPlotData(response.amplification_plotly_json)) {
    return 'curves';
  }
  return 'heatmap';
}

export function highlightSelectedWellStandardCurvePoints(
  data: unknown[],
  selectedWellPosition: unknown,
  targetName?: string | null,
): unknown[] {
  const selectedWell = normalizeWellToken(selectedWellPosition);
  if (!selectedWell) return data;

  const highlightedPoints: Array<{ x: unknown; y: unknown; text: unknown }> = [];
  for (const trace of data) {
    if (!isTrace(trace)) continue;
    const mode = String(trace.mode ?? '').toLowerCase();
    if (!mode.includes('markers')) continue;
    if (mode.includes('lines') && !mode.includes('markers')) continue;
    if (!traceMatchesTarget(trace, targetName)) continue;

    const xValues = asArray(trace.x);
    const yValues = asArray(trace.y);
    const textValues = asArray(trace.text);
    const pointCount = Math.max(xValues.length, yValues.length, textValues.length);
    for (let index = 0; index < pointCount; index += 1) {
      const text = textValues[index] ?? trace.name ?? '';
      if (!textMatchesWell(text, selectedWell)) continue;
      highlightedPoints.push({
        x: xValues[index],
        y: yValues[index],
        text,
      });
    }
  }

  if (highlightedPoints.length === 0) return data;
  return [
    ...data,
    {
      type: 'scatter',
      mode: 'markers',
      name: `Selected well ${selectedWell} on standard curve`,
      x: highlightedPoints.map((point) => point.x),
      y: highlightedPoints.map((point) => point.y),
      text: highlightedPoints.map((point) => point.text),
      marker: {
        color: '#fbbf24',
        size: 16,
        symbol: 'star-diamond',
        line: { color: '#111827', width: 2 },
      },
      hovertemplate: 'Selected %{text}<br>log10 quantity=%{x}<br>Cq=%{y}<extra></extra>',
    },
  ];
}

export function highlightSelectedWellAmplificationTraces(
  data: unknown[],
  selectedWellPosition: unknown,
  targetName?: string | null,
): unknown[] {
  const selectedWell = normalizeWellToken(selectedWellPosition);
  if (!selectedWell) return data;
  const hasSelectedTrace = data.some((trace) => isTrace(trace) && traceMatchesWell(trace, selectedWell) && traceMatchesTarget(trace, targetName));
  if (!hasSelectedTrace) return data;

  return data.map((trace) => {
    if (!isTrace(trace)) return trace;
    const selected = traceMatchesWell(trace, selectedWell) && traceMatchesTarget(trace, targetName);
    const line = isRecord(trace.line) ? { ...trace.line, width: selected ? 4 : (trace.line.width ?? 1) } : { width: selected ? 4 : 1 };
    return {
      ...trace,
      opacity: selected ? 1 : 0.12,
      line,
    };
  });
}

function isTrace(value: unknown): value is PlotlyTraceLike {
  return isRecord(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

function normalizeWellToken(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const match = /^([A-H])\s*0*([1-9]|1[0-2])$/i.exec(String(value).trim());
  if (!match) return null;
  return `${match[1].toUpperCase()}${Number(match[2])}`;
}

function textMatchesWell(value: unknown, selectedWell: string): boolean {
  const text = String(value ?? '').toUpperCase().replace(/([A-H])\s+0?([1-9]|1[0-2])/gi, (_match, row: string, column: string) => `${row}${Number(column)}`);
  const match = /^([A-H])([1-9]|1[0-2])$/.exec(selectedWell);
  if (!match) return false;
  const [, row, column] = match;
  return new RegExp(`(^|[^A-Z0-9])${escapeRegExp(row)}0?${escapeRegExp(column)}(?![0-9])`, 'i').test(text);
}

function traceMatchesWell(trace: PlotlyTraceLike, selectedWell: string): boolean {
  if (textMatchesWell(trace.name, selectedWell)) return true;
  return asArray(trace.text).some((value) => textMatchesWell(value, selectedWell));
}

function traceMatchesTarget(trace: PlotlyTraceLike, targetName?: string | null): boolean {
  const cleanTarget = String(targetName ?? '').trim().toLowerCase();
  if (!cleanTarget) return true;
  const haystacks = [trace.name, ...asArray(trace.text)].map((value) => String(value ?? '').toLowerCase());
  return haystacks.some((text) => text.includes(cleanTarget));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
