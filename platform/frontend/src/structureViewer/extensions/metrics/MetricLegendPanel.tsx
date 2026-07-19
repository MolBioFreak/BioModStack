import type { MetricDescriptor, MetricLayer } from '../../metrics/metricContracts.js';

const formatRange = (descriptor: MetricDescriptor): string => descriptor.valueRange
    ? `${descriptor.valueRange[0]}–${descriptor.valueRange[1]}${descriptor.units ? ` ${descriptor.units}` : ''}`
    : descriptor.units ?? 'unitless';

export interface MetricLegendPanelProps {
    readonly layer: MetricLayer;
    readonly visible?: boolean;
    readonly opacity?: number;
    readonly onVisibilityChange?: (visible: boolean) => void;
    readonly onOpacityChange?: (opacity: number) => void;
    readonly onReset?: () => void;
}

export function MetricLegendPanel({ layer, visible = true, opacity = 1, onVisibilityChange, onOpacityChange, onReset }: MetricLegendPanelProps) {
    const { descriptor } = layer;
    const isStructureScalar = descriptor.dimension === 'structure-scalar';
    const scalarValue = isStructureScalar ? layer.values[0]?.value : undefined;
    const missing = new Map<string, number>();
    for (const value of layer.values) {
        if (value.missingness) missing.set(value.missingness, (missing.get(value.missingness) ?? 0) + 1);
    }
    const parameters = descriptor.provenance.parameters
        ? Object.entries(descriptor.provenance.parameters).map(([key, value]) => `${key}=${String(value)}`).join(', ')
        : null;
    return (
        <section className="rounded border border-slate-700 bg-slate-900/95 p-3 text-xs text-slate-200" aria-label={`${descriptor.label} metric legend`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="font-semibold">{descriptor.label}</div>
                    <div className="text-slate-400">{formatRange(descriptor)} · {descriptor.direction.replaceAll('_', ' ')}</div>
                </div>
                {!isStructureScalar && <div className="flex items-center gap-2"><label className="flex items-center gap-1"><input type="checkbox" checked={visible} onChange={(event) => onVisibilityChange?.(event.target.checked)} /> Show</label><button type="button" className="rounded border border-slate-600 px-1" onClick={onReset}>Reset</button></div>}
            </div>
            {isStructureScalar && (
                <div className="mt-3 rounded border border-blue-500/20 bg-blue-500/10 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-slate-400">Scalar value</div>
                    <div className="font-mono text-lg font-semibold text-blue-100">
                        {typeof scalarValue === 'number' ? scalarValue.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(scalarValue ?? '—')}{descriptor.units ? ` ${descriptor.units}` : ''}
                    </div>
                </div>
            )}
            {!isStructureScalar && <div className="mt-2 h-2 rounded" style={{ background: `linear-gradient(to right, ${(descriptor.palette?.colors ?? ['#2563eb', '#f8fafc', '#dc2626']).join(', ')})` }} aria-hidden="true" />}
            {!isStructureScalar && <label className="mt-2 flex items-center gap-2">Opacity
                <input aria-label={`${descriptor.label} opacity`} type="range" min={0} max={1} step={0.05} value={opacity} onChange={(event) => onOpacityChange?.(Number(event.target.value))} />
            </label>}
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 text-slate-400">
                {descriptor.description && <><dt>Description</dt><dd>{descriptor.description}</dd></>}
                {descriptor.semantics && <><dt>Semantics</dt><dd>{descriptor.semantics}</dd></>}
                {descriptor.formula && <><dt>Formula</dt><dd className="font-mono">{descriptor.formula}</dd></>}
                <dt>Source</dt><dd>{descriptor.provenance.source}</dd>
                {descriptor.provenance.sourceVersion && <><dt>Version</dt><dd>{descriptor.provenance.sourceVersion}</dd></>}
                {descriptor.provenance.workflowId && <><dt>Workflow</dt><dd>{descriptor.provenance.workflowId}</dd></>}
                {descriptor.provenance.jobId && <><dt>Job</dt><dd>{descriptor.provenance.jobId}</dd></>}
                {descriptor.provenance.artifactId && <><dt>Artifact</dt><dd>{descriptor.provenance.artifactId}</dd></>}
                {descriptor.provenance.artifactSha256 && <><dt>SHA-256</dt><dd className="break-all font-mono">{descriptor.provenance.artifactSha256}</dd></>}
                {parameters && <><dt>Parameters</dt><dd>{parameters}</dd></>}
                <dt>Missing</dt><dd>{missing.size > 0 ? [...missing].map(([reason, count]) => `${reason}: ${count}`).join(', ') : 'none'}; never interpolated</dd>
            </dl>
        </section>
    );
}
