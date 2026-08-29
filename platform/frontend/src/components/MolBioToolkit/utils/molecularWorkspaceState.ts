import { useEffect, useState } from 'react';

export const MOLECULAR_WORKSPACE_STORAGE_KEY = 'bms.molbio.workspaces.v1';

export type MolecularWorkspaceLens = 'current' | 'historical';
export type DirtyWorkspaceChoice = 'save' | 'discard' | 'stay';

export interface MolecularWorkspaceViewContext {
    activePanel?: string;
    viewMode?: string;
    displayStrand?: string;
}

export interface PersistedMolecularWorkspace {
    id: string;
    sequenceId: string;
    lens: MolecularWorkspaceLens;
    exactRevisionId?: string;
    viewContext: MolecularWorkspaceViewContext;
}

export type MolecularOpenRequest =
    | { kind: 'none' }
    | { kind: 'current'; sequenceId: string }
    | { kind: 'exact'; sequenceId: string; revisionId: string }
    | { kind: 'invalid'; reason: 'revision_without_sequence' };

export function resolveExactMolecularAuthority(
    requestApproved: boolean,
    hasExactRequest: boolean,
    hasActiveExactLens: boolean,
): boolean {
    return (requestApproved && hasExactRequest) || hasActiveExactLens;
}

export function useMolecularWorkspaceRestoreEffect<T>(
    enabled: boolean,
    load: () => Promise<T>,
    publish: (value: T) => void,
): { restoring: boolean; error: string | null } {
    const [state, setState] = useState<{ restoring: boolean; error: string | null }>({
        restoring: false,
        error: null,
    });

    useEffect(() => {
        if (!enabled) return undefined;
        let cancelled = false;
        setState({ restoring: true, error: null });
        void load().then((value) => {
            if (cancelled) return;
            publish(value);
            setState({ restoring: false, error: null });
        }).catch((error: unknown) => {
            if (cancelled) return;
            setState({
                restoring: false,
                error: error instanceof Error ? error.message : 'Molecular workspace restoration failed.',
            });
        });
        return () => {
            cancelled = true;
        };
    }, [enabled, load, publish]);

    return state;
}

export async function loadMolecularWorkspaceCurrentSequence<T>(
    sequenceId: string,
    fetchSequence: (sequenceId: string) => Promise<T>,
): Promise<T | null> {
    try {
        return await fetchSequence(sequenceId);
    } catch {
        return null;
    }
}

export interface RestoredMolecularWorkspaceIdentity {
    tabs: PersistedMolecularWorkspace[];
    activeWorkspaceId: string | null;
    invalidCount: number;
    notice: string | null;
}

function trimmedParam(params: URLSearchParams, key: string): string | null {
    return params.get(key)?.trim() || null;
}

export function resolveMolecularOpenRequest(params: URLSearchParams): MolecularOpenRequest {
    const sequenceId = trimmedParam(params, 'molbio_sequence_id');
    const revisionId = trimmedParam(params, 'molbio_revision_id');
    if (revisionId && !sequenceId) return { kind: 'invalid', reason: 'revision_without_sequence' };
    if (sequenceId && revisionId) return { kind: 'exact', sequenceId, revisionId };
    if (sequenceId) return { kind: 'current', sequenceId };
    return { kind: 'none' };
}

export function molecularWorkspaceId(sequenceId: string): string {
    return `molecular_sequence_${encodeURIComponent(sequenceId)}`;
}

export function upsertStableMolecularWorkspace<T extends { id: string; sequenceId: string | null }>(
    workspaces: T[],
    next: T,
): T[] {
    const existingIndex = workspaces.findIndex((workspace) => workspace.sequenceId === next.sequenceId);
    if (existingIndex < 0) return [...workspaces, next];
    return workspaces.map((workspace, index) => index === existingIndex ? next : workspace);
}

export async function runDirtyWorkspaceTransition(
    dirty: boolean,
    choice: DirtyWorkspaceChoice,
    save: () => Promise<boolean>,
): Promise<boolean> {
    if (!dirty) return true;
    if (choice === 'stay') return false;
    if (choice === 'discard') return true;
    return save();
}

export function serializeMolecularWorkspaceIdentity(
    tabs: PersistedMolecularWorkspace[],
    activeWorkspaceId: string | null,
): string {
    return JSON.stringify({ version: 1, activeWorkspaceId, tabs });
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function optionalString(value: unknown): string | undefined {
    return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function parsePersistedTab(value: unknown): PersistedMolecularWorkspace | null {
    if (!isRecord(value)) return null;
    const sequenceId = optionalString(value.sequenceId);
    const id = optionalString(value.id);
    const lens = value.lens;
    const exactRevisionId = optionalString(value.exactRevisionId);
    if (!sequenceId || !id || id !== molecularWorkspaceId(sequenceId)) return null;
    if (lens !== 'current' && lens !== 'historical') return null;
    if (lens === 'historical' && !exactRevisionId) return null;
    const rawViewContext = isRecord(value.viewContext) ? value.viewContext : {};
    return {
        id,
        sequenceId,
        lens,
        ...(lens === 'historical' ? { exactRevisionId } : {}),
        viewContext: {
            activePanel: optionalString(rawViewContext.activePanel),
            viewMode: optionalString(rawViewContext.viewMode),
            displayStrand: optionalString(rawViewContext.displayStrand),
        },
    };
}

export function deserializeMolecularWorkspaceIdentity(raw: string | null): RestoredMolecularWorkspaceIdentity {
    if (!raw) return { tabs: [], activeWorkspaceId: null, invalidCount: 0, notice: null };
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return {
            tabs: [],
            activeWorkspaceId: null,
            invalidCount: 1,
            notice: 'Some saved molecular workspaces could not be restored and were skipped.',
        };
    }
    if (!isRecord(parsed) || parsed.version !== 1 || !Array.isArray(parsed.tabs)) {
        return {
            tabs: [],
            activeWorkspaceId: null,
            invalidCount: 1,
            notice: 'Some saved molecular workspaces could not be restored and were skipped.',
        };
    }

    const tabs: PersistedMolecularWorkspace[] = [];
    const sequenceIds = new Set<string>();
    let invalidCount = 0;
    for (const rawTab of parsed.tabs) {
        const tab = parsePersistedTab(rawTab);
        if (!tab || sequenceIds.has(tab.sequenceId)) {
            invalidCount += 1;
            continue;
        }
        sequenceIds.add(tab.sequenceId);
        tabs.push(tab);
    }
    const requestedActiveId = optionalString(parsed.activeWorkspaceId) ?? null;
    const activeWorkspaceId = requestedActiveId && tabs.some((tab) => tab.id === requestedActiveId)
        ? requestedActiveId
        : (tabs[0]?.id ?? null);
    if (requestedActiveId && requestedActiveId !== activeWorkspaceId) invalidCount += 1;
    return {
        tabs,
        activeWorkspaceId,
        invalidCount,
        notice: invalidCount > 0
            ? 'Some saved molecular workspaces could not be restored and were skipped.'
            : null,
    };
}
