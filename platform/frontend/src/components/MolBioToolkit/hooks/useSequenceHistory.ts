/**
 * useSequenceHistory - Undo/Redo functionality for sequence data
 * 
 * Provides history tracking for sequence edits including annotations.
 */

import { useReducer, useCallback } from 'react';
import type { SequenceData } from '../types';

// ═══════════════════════════════════════════════════════════════════════════════
// TYPES
// ═══════════════════════════════════════════════════════════════════════════════

export interface HistoryEntry {
    id: string;
    action: 'set' | 'undo' | 'redo' | 'reset';
    label: string;
    timestamp: string;
    summary: string;
}

export interface HistoryState {
    past: SequenceData[];
    present: SequenceData;
    future: SequenceData[];
    journal: HistoryEntry[];
}

type HistoryAction =
    | { type: 'SET'; payload: SequenceData; label?: string }
    | { type: 'UNDO' }
    | { type: 'REDO' }
    | { type: 'RESET'; payload: SequenceData; label?: string }
    | { type: 'HYDRATE'; payload: HistoryState };

function summarizeSequenceData(data: SequenceData): string {
    const length = data.sequence.length.toLocaleString();
    const features = data.features.length;
    const primers = (data.primers || []).length;
    return `${data.name || 'Untitled'} • ${length} nt • ${features} features • ${primers} primers`;
}

function nextHistoryEntry(
    action: HistoryEntry['action'],
    label: string,
    payload: SequenceData,
): HistoryEntry {
    return {
        id: `hist_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
        action,
        label,
        timestamp: new Date().toISOString(),
        summary: summarizeSequenceData(payload),
    };
}

export function createHistoryState(present: SequenceData, label = 'Load sequence'): HistoryState {
    return {
        past: [],
        present,
        future: [],
        journal: [nextHistoryEntry('reset', label, present)],
    };
}

// ═══════════════════════════════════════════════════════════════════════════════
// REDUCER
// ═══════════════════════════════════════════════════════════════════════════════

function historyReducer(state: HistoryState, action: HistoryAction): HistoryState {
    switch (action.type) {
        case 'SET':
            // Don't add to history if identical to present
            if (sequenceDataEquals(action.payload, state.present)) {
                return state;
            }
            return {
                past: [...state.past, state.present].slice(-50), // Keep last 50 states
                present: action.payload,
                future: [],
                journal: [...state.journal, nextHistoryEntry('set', action.label || 'Edit sequence', action.payload)].slice(-200),
            };

        case 'UNDO':
            if (state.past.length === 0) return state;
            {
                const present = state.past[state.past.length - 1];
                return {
                    past: state.past.slice(0, -1),
                    present,
                    future: [state.present, ...state.future],
                    journal: [...state.journal, nextHistoryEntry('undo', 'Undo', present)].slice(-200),
                };
            }

        case 'REDO':
            if (state.future.length === 0) return state;
            return {
                past: [...state.past, state.present],
                present: state.future[0],
                future: state.future.slice(1),
                journal: [...state.journal, nextHistoryEntry('redo', 'Redo', state.future[0])].slice(-200),
            };

        case 'RESET':
            return {
                past: [],
                present: action.payload,
                future: [],
                journal: [nextHistoryEntry('reset', action.label || 'Load sequence', action.payload)],
            };

        case 'HYDRATE':
            return action.payload;

        default:
            return state;
    }
}

function featureEquals(a: SequenceData['features'][number], b: SequenceData['features'][number]): boolean {
    const aSegments = a.segments || [{ start: a.start, end: a.end }];
    const bSegments = b.segments || [{ start: b.start, end: b.end }];
    if (aSegments.length !== bSegments.length) return false;
    for (let index = 0; index < aSegments.length; index += 1) {
        if (aSegments[index].start !== bSegments[index].start || aSegments[index].end !== bSegments[index].end) {
            return false;
        }
    }
    return (
        a.id === b.id &&
        a.name === b.name &&
        a.type === b.type &&
        a.start === b.start &&
        a.end === b.end &&
        a.strand === b.strand &&
        a.color === b.color &&
        a.description === b.description &&
        JSON.stringify(a.notes ?? null) === JSON.stringify(b.notes ?? null) &&
        JSON.stringify(a.qualifiers ?? null) === JSON.stringify(b.qualifiers ?? null) &&
        JSON.stringify(a.provenance ?? null) === JSON.stringify(b.provenance ?? null)
    );
}

function primerEquals(a: NonNullable<SequenceData['primers']>[number], b: NonNullable<SequenceData['primers']>[number]): boolean {
    return (
        a.id === b.id &&
        a.name === b.name &&
        a.sequence === b.sequence &&
        a.sequenceType === b.sequenceType &&
        a.start === b.start &&
        a.end === b.end &&
        a.strand === b.strand &&
        a.tm === b.tm &&
        a.gc_percent === b.gc_percent &&
        a.tm_algorithm === b.tm_algorithm &&
        a.tm_salt_correction === b.tm_salt_correction &&
        JSON.stringify(a.tm_settings ?? null) === JSON.stringify(b.tm_settings ?? null) &&
        JSON.stringify(a.notes ?? null) === JSON.stringify(b.notes ?? null) &&
        JSON.stringify(a.provenance ?? null) === JSON.stringify(b.provenance ?? null) &&
        JSON.stringify(a.sites ?? null) === JSON.stringify(b.sites ?? null)
    );
}

function translationEquals(a: NonNullable<SequenceData['translations']>[number], b: NonNullable<SequenceData['translations']>[number]): boolean {
    return (
        a.start === b.start &&
        a.end === b.end &&
        a.strand === b.strand &&
        a.frame === b.frame
    );
}

function analysisTrackEquals(a: NonNullable<SequenceData['analysisTracks']>[number], b: NonNullable<SequenceData['analysisTracks']>[number]): boolean {
    if (
        a.id !== b.id ||
        a.name !== b.name ||
        a.kind !== b.kind ||
        a.description !== b.description ||
        a.color !== b.color ||
        a.sourceFormat !== b.sourceFormat ||
        a.sourceName !== b.sourceName ||
        a.sourceUrl !== b.sourceUrl ||
        a.normalization !== b.normalization ||
        a.minValue !== b.minValue ||
        a.maxValue !== b.maxValue ||
        a.createdAt !== b.createdAt
    ) {
        return false;
    }

    if (a.values.length !== b.values.length) return false;
    for (let index = 0; index < a.values.length; index += 1) {
        if (a.values[index] !== b.values[index]) {
            return false;
        }
    }

    return true;
}

function sequenceDataEquals(a: SequenceData, b: SequenceData): boolean {
    if (a === b) return true;
    if (
        a.name !== b.name ||
        a.description !== b.description ||
        a.sequence !== b.sequence ||
        a.circular !== b.circular ||
        a.sequenceType !== b.sequenceType ||
        a.organism !== b.organism ||
        a.accession !== b.accession ||
        a.sourceFile !== b.sourceFile ||
        a.parentId !== b.parentId ||
        a.operation !== b.operation ||
        a.version !== b.version ||
        JSON.stringify(a.operationParams ?? null) !== JSON.stringify(b.operationParams ?? null)
    ) {
        return false;
    }

    if (a.features.length !== b.features.length) return false;
    for (let index = 0; index < a.features.length; index += 1) {
        if (!featureEquals(a.features[index], b.features[index])) {
            return false;
        }
    }

    const aPrimers = a.primers || [];
    const bPrimers = b.primers || [];
    if (aPrimers.length !== bPrimers.length) return false;
    for (let index = 0; index < aPrimers.length; index += 1) {
        if (!primerEquals(aPrimers[index], bPrimers[index])) {
            return false;
        }
    }

    const aTranslations = a.translations || [];
    const bTranslations = b.translations || [];
    if (aTranslations.length !== bTranslations.length) return false;
    for (let index = 0; index < aTranslations.length; index += 1) {
        if (!translationEquals(aTranslations[index], bTranslations[index])) {
            return false;
        }
    }

    const aTracks = a.analysisTracks || [];
    const bTracks = b.analysisTracks || [];
    if (aTracks.length !== bTracks.length) return false;
    for (let index = 0; index < aTracks.length; index += 1) {
        if (!analysisTrackEquals(aTracks[index], bTracks[index])) {
            return false;
        }
    }

    return true;
}

// ═══════════════════════════════════════════════════════════════════════════════
// HOOK
// ═══════════════════════════════════════════════════════════════════════════════

export function useSequenceHistory(initialState: SequenceData) {
    const [state, dispatch] = useReducer(historyReducer, createHistoryState(initialState, 'Initialize workspace'));

    const set = useCallback((value: SequenceData, label?: string) => {
        dispatch({ type: 'SET', payload: value, label });
    }, []);

    const undo = useCallback(() => {
        dispatch({ type: 'UNDO' });
    }, []);

    const redo = useCallback(() => {
        dispatch({ type: 'REDO' });
    }, []);

    const reset = useCallback((value: SequenceData, label?: string) => {
        dispatch({ type: 'RESET', payload: value, label });
    }, []);

    const hydrate = useCallback((value: HistoryState) => {
        dispatch({ type: 'HYDRATE', payload: value });
    }, []);

    return {
        sequenceData: state.present,
        set,
        undo,
        redo,
        reset,
        hydrate,
        historyState: state,
        historyJournal: state.journal,
        canUndo: state.past.length > 0,
        canRedo: state.future.length > 0,
        historyLength: state.past.length
    };
}
