import { useMemo, useState } from 'react';
import type { ProjectTreeNode, ProjectMapNode } from '../../lib/projectManager';
import { displayLabel } from './projectManagerState';
type SearchNode = Omit<ProjectTreeNode, 'node_type'> & { node_type: ProjectTreeNode['node_type'] | ProjectMapNode['node_type'] };

interface ProjectTreeProps {
    nodes: ProjectTreeNode[];
    mapNodes?: ProjectMapNode[];
    selectedNodeKey: string;
    onSelect: (nodeKey: string) => void;
    onClose?: () => void;
}

export function ProjectTree({ nodes, mapNodes = [], selectedNodeKey, onSelect, onClose }: ProjectTreeProps) {
    const [query, setQuery] = useState('');
    // Store only explicit choices; refreshed/new nodes retain sensible defaults.
    const [expansion, setExpansion] = useState<Record<string, boolean>>({});
    const expanded = useMemo(() => new Set(nodes.filter((node) => expansion[node.node_key] ?? node.node_type !== 'virtual_folder').map((node) => node.node_key)), [nodes, expansion]);
    const nodeByKey = useMemo(() => new Map(nodes.map((node) => [node.node_key, node])), [nodes]);
    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase();
        // Search the existing bounded map as well as the hierarchy. Plans are
        // map records, not hierarchy children; do not invent another catalog.
        const candidates: SearchNode[] = needle ? [...nodes, ...mapNodes.filter(node => !nodeByKey.has(node.node_key)).map(node => ({
            ...node, subject_id: typeof node.canonical_identity.entity_id === 'string' ? node.canonical_identity.entity_id : null,
            parent_node_key: node.parent_node_key ?? null, lifecycle_state: node.normalized_state, has_children: false,
        }))] : nodes;
        return candidates.filter((node) => {
            if (needle && !node.label.toLowerCase().includes(needle) && !node.node_type.toLowerCase().includes(needle)) return false;
            let parent = node.parent_node_key;
            while (!needle && parent) {
                if (!expanded.has(parent)) return false;
                parent = nodeByKey.get(parent)?.parent_node_key ?? null;
            }
            return true;
        });
    }, [expanded, nodeByKey, nodes, mapNodes, query]);

    const depthFor = (node: SearchNode) => {
        let depth = 0;
        let parent = node.parent_node_key;
        while (parent && depth < 4) {
            depth += 1;
            parent = nodeByKey.get(parent)?.parent_node_key ?? null;
        }
        return depth;
    };
    const toggle = (node: SearchNode) => {
        setExpansion((current) => ({ ...current, [node.node_key]: !expanded.has(node.node_key) }));
        if (node.node_type === 'virtual_folder') onSelect(node.node_key);
    };

    return (
        <aside aria-label="Project tree" className="flex h-full min-h-0 flex-col border-r border-border-primary bg-surface-secondary">
            <div className="flex items-start justify-between gap-3 border-b border-border-primary px-4 py-3">
                <div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Research map</p><h2 className="mt-1 text-sm font-semibold text-content">Project tree</h2></div>
                {onClose && <button type="button" onClick={onClose} className="rounded-md border border-border-primary px-2 py-1 text-[10px] text-content-secondary">Close</button>}
            </div>
            <div className="p-3"><input aria-label="Filter Project tree" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a Project item" className="w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" /></div>
            {query.trim() && <p className="px-3 pb-2 text-[10px] text-content-muted">Searching loaded tree and map records. Change focus or map page for more items.</p>}
            <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
                {visible.map((node) => {
                    const selected = node.node_key === selectedNodeKey;
                    const depth = depthFor(node);
                    const isFolder = node.node_type === 'virtual_folder';
                    const hasExpandableContent = node.has_children || isFolder;
                    const isExpanded = expanded.has(node.node_key);
                    const initial = node.node_type === 'project' ? 'P' : node.node_type === 'global_experiment' ? 'G' : node.node_type === 'domain_experiment' ? 'D' : '›';
                    return (
                        <div key={node.node_key} style={{ paddingLeft: `${depth * 14}px` }} className="mb-1">
                            <div className={`flex rounded-lg border ${selected ? 'border-accent bg-accent/10' : 'border-transparent hover:border-border-primary hover:bg-surface'}`}>
                                {hasExpandableContent && <button type="button" aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${node.label}`} aria-expanded={isExpanded} onClick={() => toggle(node)} className="w-7 shrink-0 rounded-l-lg text-content-muted focus:ring-2 focus:ring-accent">{isExpanded ? '−' : '+'}</button>}
                                <button type="button" onClick={() => onSelect(node.node_key)} aria-current={selected ? 'true' : undefined} className="min-w-0 flex-1 rounded-r-lg px-2 py-2 text-left outline-none focus:ring-2 focus:ring-accent">
                                    <span className="flex items-start gap-2"><span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded text-[9px] font-bold ${selected ? 'bg-accent text-white' : 'bg-surface-tertiary text-content-muted'}`}>{initial}</span><span className="min-w-0 flex-1"><span title={node.label} className={`block text-xs font-semibold text-content ${query.trim() ? 'break-words' : 'truncate'}`}>{node.label}</span><span className="mt-0.5 block truncate text-[9px] uppercase tracking-[0.12em] text-content-muted">{node.lifecycle_state ? displayLabel(node.lifecycle_state) : displayLabel(node.node_type)}{Object.keys(node.counts).length ? ` · ${Object.values(node.counts).reduce((sum, value) => sum + value, 0)}` : ''}</span></span></span>
                                </button>
                            </div>
                            {isFolder && isExpanded && <p className="px-8 py-1 text-[9px] text-content-muted">First bounded page · persisted hierarchy unchanged</p>}
                        </div>
                    );
                })}
                {!visible.length && <p className="px-3 py-8 text-center text-xs text-content-muted">No Project items match this filter.</p>}
            </nav>
        </aside>
    );
}
