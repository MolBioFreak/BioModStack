declare module 'react-plotly.js' {
    import * as Plotly from 'plotly.js';
    import * as React from 'react';

    interface PlotParams {
        data: Plotly.Data[];
        layout?: Partial<Plotly.Layout>;
        config?: Partial<Plotly.Config>;
        style?: React.CSSProperties;
        className?: string;
        useResizeHandler?: boolean;
        onInitialized?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
        onUpdate?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
        onPurge?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
        onError?: (err: Error) => void;
        onHover?: (event: Plotly.PlotHoverEvent) => void;
        onUnhover?: (event: Plotly.PlotMouseEvent) => void;
        onClick?: (event: Plotly.PlotMouseEvent) => void;
        onSelected?: (event: Plotly.PlotSelectionEvent) => void;
        onRelayout?: (event: Plotly.PlotRelayoutEvent) => void;
        onRestyle?: (event: Plotly.PlotRestyleEvent) => void;
        onRedraw?: () => void;
        onAnimated?: () => void;
        onAfterExport?: () => void;
        onAfterPlot?: () => void;
        onAutoSize?: () => void;
        onBeforeExport?: () => void;
        onClickAnnotation?: (event: Plotly.ClickAnnotationEvent) => void;
        onDeselect?: () => void;
        onDoubleClick?: () => void;
        onFramework?: () => void;
        onLegendClick?: (event: Plotly.LegendClickEvent) => boolean;
        onLegendDoubleClick?: (event: Plotly.LegendClickEvent) => boolean;
        onSliderChange?: (event: Plotly.SliderChangeEvent) => void;
        onSliderEnd?: (event: Plotly.SliderEndEvent) => void;
        onSliderStart?: (event: Plotly.SliderStartEvent) => void;
        onTransitioning?: () => void;
        onTransitionInterrupted?: () => void;
        frames?: Plotly.Frame[];
        revision?: number;
        debug?: boolean;
        divId?: string;
    }

    const Plot: React.ComponentType<PlotParams>;
    export default Plot;
}
