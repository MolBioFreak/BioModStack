import type { ProjectManagerReadModel, ProjectTreeNode } from '../../lib/projectManager';

export function focusIdFromReadModel(summary: ProjectManagerReadModel): string {
    const focus = summary.tree.nodes.find((node) => node.node_key === summary.map.focus_node_key);
    return focus?.subject_id ?? summary.project.id;
}

export function globalExperimentForNode(summary: ProjectManagerReadModel, nodeKey: string): string | null {
    const nodes = new Map(summary.tree.nodes.map((node) => [node.node_key, node]));
    let current: ProjectTreeNode | undefined = nodes.get(nodeKey);
    const visited = new Set<string>();
    while (current && !visited.has(current.node_key)) {
        visited.add(current.node_key);
        if (current.node_type === 'global_experiment') return current.subject_id;
        current = current.parent_node_key ? nodes.get(current.parent_node_key) : undefined;
    }
    return null;
}

export function selectedDomainContext(summary: ProjectManagerReadModel): { globalExperimentId: string; domainExperimentId: string } | null {
    if (summary.selection.node_type !== 'domain_experiment') return null;
    const domain = summary.tree.nodes.find((node) => node.node_key === summary.selection.node_key);
    if (!domain?.subject_id || !domain.parent_node_key) return null;
    const globalExperiment = summary.tree.nodes.find((node) => node.node_key === domain.parent_node_key);
    if (!globalExperiment?.subject_id || globalExperiment.node_type !== 'global_experiment') return null;
    return { globalExperimentId: globalExperiment.subject_id, domainExperimentId: domain.subject_id };
}

export function parentDomainForSelection(summary: ProjectManagerReadModel): { globalExperimentId: string; domainExperimentId: string } | null {
    const direct = selectedDomainContext(summary);
    if (direct) return direct;
    const edges = summary.map.edges.filter((edge) => edge.target_node_key === summary.selection.node_key);
    for (const edge of edges) {
        const source = summary.tree.nodes.find((node) => node.node_key === edge.source_node_key);
        if (source?.node_type === 'domain_experiment' && source.subject_id && source.parent_node_key) {
            const globalExperiment = summary.tree.nodes.find((node) => node.node_key === source.parent_node_key);
            if (globalExperiment?.subject_id) {
                return { globalExperimentId: globalExperiment.subject_id, domainExperimentId: source.subject_id };
            }
        }
    }
    return null;
}

export function displayLabel(value: string): string {
    return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function compactCountLabel(counts: Record<string, number>): string {
    const entries = Object.entries(counts).filter(([, value]) => Number.isFinite(value));
    if (!entries.length) return '';
    return entries.map(([key, value]) => `${value} ${key.replaceAll('_', ' ')}`).join(' · ');
}

export function valueText(value: unknown): string {
    if (value === null || value === undefined || value === '') return 'Not provided';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) {
        return value.map((item) => typeof item === 'object' && item !== null && 'label' in item ? String(item.label) : valueText(item)).join(', ') || 'None';
    }
    return JSON.stringify(value);
}
