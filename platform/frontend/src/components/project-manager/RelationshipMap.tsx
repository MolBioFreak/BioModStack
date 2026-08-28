import { useMemo, useRef } from 'react';
import type { ProjectMapNode, ProjectManagerReadModel } from '../../lib/projectManager';
import { compactCountLabel, displayLabel } from './projectManagerState';

interface RelationshipMapProps {
    summary: ProjectManagerReadModel;
    selectedNodeKey: string;
    onSelect: (node: ProjectMapNode) => void;
    onLoadMore?: () => void;
}

function MapNodeButton({ node, selected, onSelect, region }: {
    node: ProjectMapNode;
    selected: boolean;
    onSelect: (node: ProjectMapNode) => void;
    region?: string;
}) {
    const countLabel = compactCountLabel(node.counts);
    const issue = node.reconciliation.state !== 'current';
    return (
        <button
            type="button"
            aria-label={`Select ${node.label}`}
            aria-pressed={selected}
            data-node-region={region}
            onClick={() => onSelect(node)}
            className="w-full rounded-xl border p-3 text-left shadow-sm outline-none transition hover:-translate-y-0.5 hover:shadow-lg focus:ring-2 focus:ring-accent"
            style={{
                backgroundColor: selected ? 'color-mix(in srgb, var(--accent-primary) 16%, var(--card-bg))' : 'var(--card-bg)',
                borderColor: selected ? 'var(--accent-primary)' : issue ? 'var(--warning)' : 'var(--border-primary)',
            }}
        >
            <span className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                    <span className="block text-[10px] font-semibold uppercase tracking-[0.16em] text-content-muted">{displayLabel(node.node_type)}</span>
                    <span className="mt-1 block truncate text-sm font-semibold text-content">{node.label}</span>
                </span>
                <span className="shrink-0 rounded-full border border-border-primary px-2 py-0.5 text-[10px] text-content-secondary">{displayLabel(node.normalized_state)}</span>
            </span>
            {(countLabel || issue) && (
                <span className="mt-2 block text-[10px] text-content-muted">
                    {issue ? `${displayLabel(node.reconciliation.state)}${node.reconciliation.reason ? ` · ${node.reconciliation.reason}` : ''}` : countLabel}
                </span>
            )}
        </button>
    );
}

export function RelationshipMap({ summary, selectedNodeKey, onSelect, onLoadMore }: RelationshipMapProps) {
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const index = useMemo(() => new Map(summary.map.nodes.map((node) => [node.node_key, node])), [summary.map.nodes]);
    const projectNode = summary.map.nodes.find((node) => node.node_type === 'project');
    const globalNodes = summary.map.nodes.filter((node) => node.node_type === 'global_experiment');
    const domainNodes = summary.map.nodes.filter((node) => node.node_type === 'domain_experiment');
    const groupedKeys = new Set([projectNode?.node_key, ...globalNodes.map((node) => node.node_key), ...domainNodes.map((node) => node.node_key)]);
    const evidenceNodes = summary.map.nodes.filter((node) => !groupedKeys.has(node.node_key));
    const focusedGlobal = index.get(summary.map.focus_node_key);

    const evidenceByDomain = new Map<string, ProjectMapNode[]>();
    for (const domain of domainNodes) {
        const targets = summary.map.edges
            .filter((edge) => edge.source_node_key === domain.node_key)
            .map((edge) => index.get(edge.target_node_key))
            .filter((node): node is ProjectMapNode => Boolean(node));
        evidenceByDomain.set(domain.node_key, targets);
    }
    const assignedEvidence = new Set(Array.from(evidenceByDomain.values()).flat().map((node) => node.node_key));
    const unassignedEvidence = evidenceNodes.filter((node) => !assignedEvidence.has(node.node_key));

    const fitToFocus = () => {
        viewportRef.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
    };

    return (
        <section aria-label="Relationship map" className="flex min-h-0 flex-1 flex-col bg-surface">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-primary bg-surface-secondary px-4 py-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Primary work surface</p>
                    <h2 className="mt-1 text-sm font-semibold text-content">{summary.project.name} · relationship map</h2>
                </div>
                <div className="flex items-center gap-2">
                    <span className="rounded-full border border-border-primary px-2 py-1 text-[10px] text-content-secondary">{summary.map.nodes.length} nodes</span>
                    <button type="button" onClick={fitToFocus} className="rounded-lg border border-border-primary bg-surface-tertiary px-3 py-1.5 text-xs font-medium text-content-secondary outline-none hover:text-content focus:ring-2 focus:ring-accent">Fit to focus</button>
                </div>
            </header>
            <div ref={viewportRef} className="relative min-h-[28rem] flex-1 overflow-auto p-5 sm:p-7" tabIndex={0}>
                <div aria-hidden="true" className="pointer-events-none absolute inset-0 opacity-30" style={{ backgroundImage: 'radial-gradient(var(--border-secondary) 0.8px, transparent 0.8px)', backgroundSize: '22px 22px' }} />
                <div className="relative mx-auto flex min-w-[34rem] max-w-5xl flex-col gap-5">
                    {projectNode && (
                        <div className="mx-auto w-[min(100%,30rem)]">
                            <MapNodeButton node={projectNode} selected={selectedNodeKey === projectNode.node_key} onSelect={onSelect} region="project" />
                            <div aria-hidden="true" className="mx-auto h-5 w-px bg-border-secondary" />
                        </div>
                    )}

                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {globalNodes.map((node) => {
                            const focused = node.node_key === focusedGlobal?.node_key;
                            return (
                                <div key={node.node_key} className={focused ? 'md:col-span-2 xl:col-span-3' : ''}>
                                    {focused ? (
                                        <section className="rounded-2xl border-2 border-accent/70 bg-surface-secondary/90 p-4 shadow-xl" data-node-region="global-experiment">
                                            <div className="mb-4">
                                                <MapNodeButton node={node} selected={selectedNodeKey === node.node_key} onSelect={onSelect} />
                                            </div>
                                            <div className="grid gap-4 lg:grid-cols-2">
                                                {domainNodes.map((domain) => (
                                                    <section key={domain.node_key} className="rounded-xl border border-border-secondary bg-surface p-3" data-node-region="domain-experiment">
                                                        <MapNodeButton node={domain} selected={selectedNodeKey === domain.node_key} onSelect={onSelect} />
                                                        <div className="mt-3 space-y-2 border-l border-border-secondary pl-3">
                                                            {(evidenceByDomain.get(domain.node_key) ?? []).map((record) => (
                                                                <MapNodeButton key={record.node_key} node={record} selected={selectedNodeKey === record.node_key} onSelect={onSelect} />
                                                            ))}
                                                            {!(evidenceByDomain.get(domain.node_key) ?? []).length && (
                                                                <p className="rounded-lg border border-dashed border-border-primary px-3 py-4 text-center text-xs text-content-muted">No external records are attached to this Domain Experiment. Domain-owned data is separate from this relationship list.</p>
                                                            )}
                                                        </div>
                                                    </section>
                                                ))}
                                                {!domainNodes.length && <p className="col-span-full rounded-xl border border-dashed border-border-primary p-6 text-center text-xs text-content-muted">This Global Experiment has no visible Domain Experiments.</p>}
                                            </div>
                                        </section>
                                    ) : (
                                        <MapNodeButton node={node} selected={selectedNodeKey === node.node_key} onSelect={onSelect} region="global-experiment" />
                                    )}
                                </div>
                            );
                        })}
                    </div>

                    {unassignedEvidence.length > 0 && (
                        <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
                            <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-content-muted">Related records</h3>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                {unassignedEvidence.map((node) => <MapNodeButton key={node.node_key} node={node} selected={selectedNodeKey === node.node_key} onSelect={onSelect} />)}
                            </div>
                        </section>
                    )}

                    {summary.map.truncated && (
                        <div className="rounded-xl border border-warning/50 bg-warning/10 p-4 text-center text-xs text-content-secondary">
                            The relationship map is bounded. More authorized nodes are available.
                            {onLoadMore && <button type="button" onClick={onLoadMore} className="ml-3 rounded-md border border-warning/60 px-2 py-1 font-semibold text-warning focus:ring-2 focus:ring-warning">Load next map page</button>}
                        </div>
                    )}
                </div>
            </div>
            <div className="border-t border-border-primary bg-surface-secondary px-4 py-2">
                <details>
                    <summary className="cursor-pointer text-[11px] font-semibold text-content-secondary">Accessible relationship list ({summary.map.edges.length})</summary>
                    <ul className="mt-2 grid gap-1 text-[11px] text-content-muted md:grid-cols-2">
                        {summary.map.edges.map((edge) => (
                            <li key={edge.edge_key}>{edge.accessible_label}: {index.get(edge.source_node_key)?.label ?? edge.source_node_key} → {index.get(edge.target_node_key)?.label ?? edge.target_node_key}</li>
                        ))}
                    </ul>
                </details>
            </div>
        </section>
    );
}
