import { viewerOk, viewerUnsupported, type ViewerResult } from '../../contracts/viewerResults.js';
import type { MetricLayer } from '../../metrics/metricContracts.js';
import { MetricRegistry } from '../../metrics/MetricRegistry.js';

export interface MetricLayerViewState {
    readonly metricId: string;
    readonly visible: boolean;
    readonly opacity: number;
    readonly order: number;
    readonly palette?: string;
}

export class MetricLayerExtension {
    readonly registry = new MetricRegistry();
    private readonly viewState = new Map<string, MetricLayerViewState>();

    add(layer: MetricLayer, state?: Partial<Omit<MetricLayerViewState, 'metricId'>>): ViewerResult<MetricLayerViewState> {
        const registered = this.registry.register(layer);
        if (registered.status !== 'ok') return registered;
        const next: MetricLayerViewState = {
            metricId: layer.descriptor.id,
            visible: state?.visible ?? true,
            opacity: Math.max(0, Math.min(1, state?.opacity ?? 1)),
            order: state?.order ?? this.viewState.size,
            palette: state?.palette,
        };
        this.viewState.set(next.metricId, next);
        return viewerOk(next);
    }

    update(metricId: string, patch: Partial<Omit<MetricLayerViewState, 'metricId'>>): ViewerResult<MetricLayerViewState> {
        const current = this.viewState.get(metricId);
        if (!current || !this.registry.get(metricId)) return viewerUnsupported(`Unknown metric layer ${metricId}`, 'metric-layer');
        const next = {
            ...current,
            ...patch,
            opacity: Math.max(0, Math.min(1, patch.opacity ?? current.opacity)),
        };
        this.viewState.set(metricId, next);
        return viewerOk(next);
    }

    remove(metricId: string): void { this.registry.unregister(metricId); this.viewState.delete(metricId); }
    list(): readonly MetricLayerViewState[] { return [...this.viewState.values()].sort((a, b) => a.order - b.order); }
}
