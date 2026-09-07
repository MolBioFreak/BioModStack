import { useMemo, useState } from 'react';
import type { ProjectTreeNode } from '../../lib/projectManager';
import { displayLabel } from './projectManagerState';

interface ProjectTreeProps {
    nodes: ProjectTreeNode[];
    selectedNodeKey: string;
    onSelect: (nodeKey: string) => void;
    onClose?: () => void;
}

export function ProjectTree({ nodes, selectedNodeKey, onSelect, onClose }: ProjectTreeProps) {
    const [query, setQuery] = useState('');
    // Store only explicit choices; refreshed/new nodes retain sensible defaults.
    const [expansion, setExpansion] = useState<Record<string, boolean>>({});
    const expanded = useMemo(() => new Set(nodes.filter((node) => expansion[node.node_key] ?? node.node_type !== 'virtual_folder').map((node) => node.node_key)), [nodes, expansion]);
    const nodeByKey = useMemo(() => new Map(nodes.map((node) => [node.node_key, node])), [nodes]);
    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return nodes.filter((node) => {
            if (needle && !node.label.toLowerCase().includes(needle) && !node.node_type.toLowerCase().includes(needle)) return false;
            let parent = node.parent_node_key;
            while (!needle && parent) {
                if (!expanded.has(parent)) return false;
                parent = nodeByKey.get(parent)?.parent_node_key ?? null;
            }
            return true;
        });
    }, [expanded, nodeByKey, nodes, query]);

    const depthFor = (node: ProjectTreeNode) => {
        let depth = 0;
        let parent = node.parent_node_key;
        while (parent && depth < 4) {
            depth += 1;
            parent = nodeByKey.get(parent)?.parent_node_key ?? null;
        }
        return depth;
    };
    const toggle = (node: ProjectTreeNode) => {
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
                                    <span className="flex items-start gap-2"><span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded text-[9px] font-bold ${selected ? 'bg-accent text-white' : 'bg-surface-tertiary text-content-muted'}`}>{initial}</span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-content">{node.label}</span><span className="mt-0.5 block truncate text-[9px] uppercase tracking-[0.12em] text-content-muted">{node.lifecycle_state ? displayLabel(node.lifecycle_state) : displayLabel(node.node_type)}{Object.keys(node.counts).length ? ` · ${Object.values(node.counts).reduce((sum, value) => sum + value, 0)}` : ''}</span></span></span>
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
