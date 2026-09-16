import { useMemo, useRef } from 'react';
import type { ProjectMapNode, ProjectManagerReadModel } from '../../lib/projectManager';
import { compactCountLabel, displayLabel } from './projectManagerState';

interface RelationshipMapProps {
    summary: ProjectManagerReadModel;
    selectedNodeKey: string;
    onSelect: (node: ProjectMapNode) => void;
    onLoadMore?: () => void;
    onOpenWorkspace?: () => void;
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
            className={`w-full min-w-0 rounded-lg border text-left outline-none hover:bg-surface-secondary focus:ring-2 focus:ring-accent ${region === 'record' ? 'px-3 py-2' : 'p-3'}`}
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

export function RelationshipMap({ summary, selectedNodeKey, onSelect, onLoadMore, onOpenWorkspace }: RelationshipMapProps) {
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const index = useMemo(() => new Map(summary.map.nodes.map((node) => [node.node_key, node])), [summary.map.nodes]);
    const projectNode = summary.map.nodes.find((node) => node.node_type === 'project');
    const globalNodes = summary.map.nodes.filter((node) => node.node_type === 'global_experiment');
    const domainNodes = summary.map.nodes.filter((node) => node.node_type === 'domain_experiment');
    const groupedKeys = new Set([projectNode?.node_key, ...globalNodes.map((node) => node.node_key), ...domainNodes.map((node) => node.node_key)]);
    const evidenceNodes = summary.map.nodes.filter((node) => !groupedKeys.has(node.node_key));
    const hasSelectedTarget = index.has(selectedNodeKey);

    const evidenceByDomain = new Map<string, ProjectMapNode[]>();
    for (const domain of domainNodes) {
        const targets = summary.map.edges
            .filter((edge) => edge.source_node_key === domain.node_key)
            .map((edge) => index.get(edge.target_node_key))
            .filter((node): node is ProjectMapNode => Boolean(node) && !groupedKeys.has(node?.node_key));
        evidenceByDomain.set(domain.node_key, targets);
    }
    const assignedEvidence = new Set(Array.from(evidenceByDomain.values()).flat().map((node) => node.node_key));
    const unassignedEvidence = evidenceNodes.filter((node) => !assignedEvidence.has(node.node_key));

    const renderDomain = (domain: ProjectMapNode) => {
        const records = evidenceByDomain.get(domain.node_key) ?? [];
        const groups = [
            { label: 'Plans', items: records.filter((record) => record.node_type === 'workflow') },
            { label: 'Attached evidence', items: records.filter((record) => record.node_type !== 'workflow') },
        ];
        return <section key={domain.node_key} data-node-region="domain-experiment" className="min-w-0">
            <MapNodeButton node={domain} selected={selectedNodeKey === domain.node_key} onSelect={onSelect} />
            {domain.node_key === summary.selection.node_key && onOpenWorkspace && <button type="button" onClick={onOpenWorkspace} className="mt-2 rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent focus:ring-2 focus:ring-accent">Open workspace</button>}
            <div className="ml-3 space-y-3 border-l border-border-secondary py-3 pl-3">
                {groups.filter((group) => group.items.length).map((group) => <section key={group.label}>
                    <h3 className="mb-2 text-xs font-semibold text-content-secondary">{group.label} · {group.items.length}</h3>
                    <div className="space-y-1">{group.items.map((record) => <MapNodeButton key={record.node_key} node={record} selected={selectedNodeKey === record.node_key} onSelect={onSelect} region="record" />)}</div>
                </section>)}
                {!records.length && <p className="text-xs text-content-muted">No external records are attached to this Domain Experiment. Domain-owned data is separate from this relationship list.</p>}
            </div>
        </section>;
    };
    const fitToFocus = () => {
        viewportRef.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
    };

    return (
        <section aria-label="Relationship map" className="flex shrink-0 flex-col bg-surface">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-primary bg-surface-secondary px-4 py-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Primary work surface</p>
                    <h2 className="mt-1 text-sm font-semibold text-content">{summary.project.name} · relationship map</h2>
                </div>
                <div className="flex items-center gap-2">
                    <span className="rounded-full border border-border-primary px-2 py-1 text-[10px] text-content-secondary">{summary.map.nodes.length} nodes</span>
                    <button type="button" onClick={fitToFocus} disabled={!hasSelectedTarget} className="rounded-lg border border-border-primary bg-surface-tertiary px-3 py-1.5 text-xs font-medium text-content-secondary outline-none hover:text-content focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50">Show selected item</button>
                </div>
            </header>
            {!hasSelectedTarget && <p className="px-4 pt-3 text-xs text-content-muted">{selectedNodeKey.endsWith(':activity') ? 'Activity is shown in the records panel below.' : 'This selection has no card in the loaded map. See its records or inspector.'}</p>}
            <div ref={viewportRef} className="relative min-w-0 p-3 sm:p-5" tabIndex={0}>

                <div className="relative mx-auto flex min-w-0 w-full max-w-5xl flex-col gap-5">
                    {projectNode && (
                        <div className="mx-auto w-[min(100%,30rem)]">
                            <MapNodeButton node={projectNode} selected={selectedNodeKey === projectNode.node_key} onSelect={onSelect} region="project" />
                            <div aria-hidden="true" className="mx-auto h-5 w-px bg-border-secondary" />
                        </div>
                    )}

                    <div className="space-y-6">
                        {globalNodes.map((node) => {
                            const domains = domainNodes.filter((domain) => summary.tree.nodes.find((item) => item.node_key === domain.node_key)?.parent_node_key === node.node_key
                                || summary.map.edges.some((edge) => edge.source_node_key === node.node_key && edge.target_node_key === domain.node_key));
                            return <section key={node.node_key} data-node-region="global-experiment" className="min-w-0">
                                <div className="mx-auto max-w-lg"><MapNodeButton node={node} selected={selectedNodeKey === node.node_key} onSelect={onSelect} /></div>
                                <div aria-hidden="true" className="mx-auto h-5 w-px bg-border-secondary" />
                                <div className="grid gap-5 lg:grid-cols-2">
                                    {domains.map((domain) => renderDomain(domain))}
                                    {!domains.length && <p className="col-span-full text-center text-xs text-content-muted">This Global Experiment has no visible Domain Experiments.</p>}
                                </div>
                            </section>;
                        })}
                        {domainNodes.filter((domain) => !globalNodes.some((global) => summary.tree.nodes.find((item) => item.node_key === domain.node_key)?.parent_node_key === global.node_key
                            || summary.map.edges.some((edge) => edge.source_node_key === global.node_key && edge.target_node_key === domain.node_key))).map((domain) => renderDomain(domain))}
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
