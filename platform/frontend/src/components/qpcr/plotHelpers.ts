export type QpcrRawImportTab = 'heatmap' | 'curves' | 'table' | 'stdcurve';

type PlotlyLikePayload = {
  data?: unknown[];
};

type QpcrRawImportResponseLike = {
  results_plotly_json?: PlotlyLikePayload | null;
  amplification_plotly_json?: PlotlyLikePayload | null;
  standard_curve_plotly_json?: PlotlyLikePayload | null;
};

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
