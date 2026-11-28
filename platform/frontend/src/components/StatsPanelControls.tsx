import { mountedApplicationBase } from '../lib/sourceResultReturn';
import { useState } from 'react';

export interface SummaryRecomputeOptions {
    histogram_bins: number;
    shared_bins: boolean;
}

export interface CorrelationRecomputeOptions {
    method: 'pearson' | 'spearman' | 'kendall';
    p_adjust_method: 'benjamini_hochberg' | 'holm' | 'bonferroni' | 'none';
}

interface BaseProps {
    statsRunId?: string | null;
    stale?: boolean;
    status?: string | null;
    reason?: string | null;
    isRecomputing?: boolean;
}

export type StatsPanelControlsProps =
    | (BaseProps & {
        kind: 'summary';
        histogramBins?: number | null;
        sharedBins?: boolean | null;
        onRecompute?: (options: SummaryRecomputeOptions) => void;
    })
    | (BaseProps & {
        kind: 'correlation';
        method?: string | null;
        pAdjustMethod?: string | null;
        onRecompute?: (options: CorrelationRecomputeOptions) => void;
    });

const controlClass = 'rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)]';

export function StatsPanelControls(props: StatsPanelControlsProps) {
    const [showOptions, setShowOptions] = useState(false);
    const [histogramBins, setHistogramBins] = useState<number>(
        props.kind === 'summary' && typeof props.histogramBins === 'number' && props.histogramBins > 0
            ? props.histogramBins
            : 10,
    );
    const [sharedBins, setSharedBins] = useState<boolean>(
        props.kind === 'summary' ? Boolean(props.sharedBins) : false,
    );
    const [method, setMethod] = useState<'pearson' | 'spearman' | 'kendall'>(
        props.kind === 'correlation' && (props.method === 'spearman' || props.method === 'kendall')
            ? props.method
            : 'pearson',
    );
    const [pAdjustMethod, setPAdjustMethod] = useState<'benjamini_hochberg' | 'holm' | 'bonferroni' | 'none'>(
        props.kind === 'correlation' && (props.pAdjustMethod === 'holm' || props.pAdjustMethod === 'bonferroni' || props.pAdjustMethod === 'none')
            ? props.pAdjustMethod
            : 'benjamini_hochberg',
    );

    const isUnavailable = props.status === 'unavailable' || props.reason === 'stats_toolkit_unavailable';
    const openHref = props.statsRunId ? `${mountedApplicationBase()}stats?run_id=${encodeURIComponent(props.statsRunId)}` : null;

    const triggerRecompute = () => {
        if (!props.onRecompute) return;
        if (props.kind === 'summary') {
            props.onRecompute({ histogram_bins: histogramBins, shared_bins: sharedBins });
        } else {
            props.onRecompute({ method, p_adjust_method: pAdjustMethod });
        }
        setShowOptions(false);
    };

    return (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-stats-panel-controls={props.kind}>
            {props.stale && (
                <span
                    className="rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-300"
                    data-stats-stale="true"
                >
                    Stale
                </span>
            )}
            {isUnavailable && (
                <span
                    role="status"
                    className="rounded border border-rose-500/40 bg-rose-500/15 px-1.5 py-0.5 font-medium text-rose-300"
                    data-stats-unavailable="true"
                >
                    Stats unavailable
                </span>
            )}
            {openHref && (
                <a
                    href={openHref}
                    className="rounded border border-[var(--border-color)] px-2 py-1 text-xs text-[var(--link,var(--accent-primary))] underline hover:opacity-90"
                    data-open-in-stats="true"
                >
                    Open in Stats
                </a>
            )}
            {props.onRecompute && (
                <div className="inline-flex flex-wrap items-center gap-1.5">
                    <button
                        type="button"
                        className={controlClass}
                        disabled={props.isRecomputing}
                        onClick={() => setShowOptions((prev) => !prev)}
                        aria-expanded={showOptions}
                    >
                        {props.isRecomputing ? 'Recomputing…' : 'Recompute'}
                    </button>
                    {showOptions && (
                        <div className="inline-flex flex-wrap items-center gap-1.5 rounded border border-[var(--border-color)] bg-[var(--bg-secondary)] px-2 py-1">
                            {props.kind === 'summary' ? (
                                <>
                                    <label className="inline-flex items-center gap-1">
                                        <span>Bins</span>
                                        <select
                                            aria-label="Histogram bins"
                                            className={controlClass}
                                            value={histogramBins}
                                            onChange={(event) => setHistogramBins(Number(event.target.value))}
                                        >
                                            {[5, 10, 15, 20, 30, 50].map((bins) => (
                                                <option key={bins} value={bins}>{bins}</option>
                                            ))}
                                        </select>
                                    </label>
                                    <label className="inline-flex items-center gap-1">
                                        <input
                                            type="checkbox"
                                            aria-label="Shared bins"
                                            checked={sharedBins}
                                            onChange={(event) => setSharedBins(event.target.checked)}
                                        />
                                        <span>Shared bins</span>
                                    </label>
                                </>
                            ) : (
                                <>
                                    <label className="inline-flex items-center gap-1">
                                        <span>Method</span>
                                        <select
                                            aria-label="Correlation method"
                                            className={controlClass}
                                            value={method}
                                            onChange={(event) => setMethod(event.target.value as 'pearson' | 'spearman' | 'kendall')}
                                        >
                                            <option value="pearson">Pearson</option>
                                            <option value="spearman">Spearman</option>
                                            <option value="kendall">Kendall</option>
                                        </select>
                                    </label>
                                    <label className="inline-flex items-center gap-1">
                                        <span>Adjust</span>
                                        <select
                                            aria-label="P-value adjustment"
                                            className={controlClass}
                                            value={pAdjustMethod}
                                            onChange={(event) => setPAdjustMethod(event.target.value as 'benjamini_hochberg' | 'holm' | 'bonferroni' | 'none')}
                                        >
                                            <option value="benjamini_hochberg">BH</option>
                                            <option value="holm">Holm</option>
                                            <option value="bonferroni">Bonferroni</option>
                                            <option value="none">None</option>
                                        </select>
                                    </label>
                                </>
                            )}
                            <button
                                type="button"
                                className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                                disabled={props.isRecomputing}
                                onClick={triggerRecompute}
                            >
                                Run
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
