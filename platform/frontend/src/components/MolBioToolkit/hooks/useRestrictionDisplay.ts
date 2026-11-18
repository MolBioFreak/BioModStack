import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_VISIBILITY } from '../sequenceViewerConstants';
import { molecularWorkspaceId } from '../utils/molecularWorkspaceState';
import { DEFAULT_MAPPED_ENZYMES } from '../utils/restrictionLabels';

interface RestrictionDisplay {
    selectedEnzymes: string[];
    cutSitesVisible: boolean;
    groupSameSiteLabels: boolean;
}

const DEFAULT_DISPLAY: RestrictionDisplay = {
    selectedEnzymes: DEFAULT_MAPPED_ENZYMES,
    cutSitesVisible: DEFAULT_VISIBILITY.cutsites,
    groupSameSiteLabels: true,
};

const storageKey = (sequenceId: string) => `bms.molbio.restriction-display.v1:${encodeURIComponent(sequenceId)}`;

function readDisplay(sequenceId: string): RestrictionDisplay {
    try {
        const raw = window.localStorage.getItem(storageKey(sequenceId));
        if (!raw) return DEFAULT_DISPLAY;
        const saved: Partial<RestrictionDisplay> | null = JSON.parse(raw);
        return {
            selectedEnzymes: Array.isArray(saved?.selectedEnzymes)
                && saved.selectedEnzymes.every(id => typeof id === 'string' && id.length > 0)
                ? saved.selectedEnzymes : DEFAULT_DISPLAY.selectedEnzymes,
            cutSitesVisible: typeof saved?.cutSitesVisible === 'boolean'
                ? saved.cutSitesVisible : DEFAULT_DISPLAY.cutSitesVisible,
            groupSameSiteLabels: typeof saved?.groupSameSiteLabels === 'boolean'
                ? saved.groupSameSiteLabels : DEFAULT_DISPLAY.groupSameSiteLabels,
        };
    } catch {
        return DEFAULT_DISPLAY;
    }
}

function storeDisplay(sequenceId: string, display: RestrictionDisplay) {
    try {
        window.localStorage.setItem(storageKey(sequenceId), JSON.stringify(display));
    } catch {
        // The in-memory display remains usable when browser storage is unavailable.
    }
}

export function useRestrictionDisplay(workspaceId: string, sequenceId: string | null) {
    const [displays, setDisplays] = useState(() => new Map<string, RestrictionDisplay>());
    const latestDisplays = useRef(displays);
    const scope = useMemo(() => ({ workspaceId, sequenceId }), [workspaceId, sequenceId]);
    const activeScope = useRef(scope);
    useLayoutEffect(() => {
        activeScope.current = scope;
    }, [scope]);

    const savedDisplay = useMemo(() => sequenceId ? readDisplay(sequenceId) : DEFAULT_DISPLAY, [sequenceId]);
    const display = displays.get(workspaceId) ?? savedDisplay;

    const updateDisplay = useCallback((changes: Partial<RestrictionDisplay>) => {
        // A deferred map action from a workspace the user left must not change the current map.
        if (activeScope.current !== scope) return;
        const previous = latestDisplays.current.get(scope.workspaceId) ?? savedDisplay;
        const updated = { ...previous, ...changes };
        if (updated.selectedEnzymes === previous.selectedEnzymes
            && updated.cutSitesVisible === previous.cutSitesVisible
            && updated.groupSameSiteLabels === previous.groupSameSiteLabels) return;
        const next = new Map(latestDisplays.current).set(scope.workspaceId, updated);
        latestDisplays.current = next;
        setDisplays(next);
        if (scope.sequenceId) storeDisplay(scope.sequenceId, updated);
    }, [savedDisplay, scope]);

    const retainDraftDisplay = useCallback((draftWorkspaceId: string, savedSequenceId: string) => {
        const draft = latestDisplays.current.get(draftWorkspaceId);
        if (!draft) return;
        const next = new Map(latestDisplays.current);
        next.delete(draftWorkspaceId);
        next.set(molecularWorkspaceId(savedSequenceId), draft);
        latestDisplays.current = next;
        setDisplays(next);
        storeDisplay(savedSequenceId, draft);
    }, []);

    return { display, updateDisplay, retainDraftDisplay };
}
