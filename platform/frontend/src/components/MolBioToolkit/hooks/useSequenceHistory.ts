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

interface HistoryState {
    past: SequenceData[];
    present: SequenceData;
    future: SequenceData[];
}

type HistoryAction =
    | { type: 'SET'; payload: SequenceData }
    | { type: 'UNDO' }
    | { type: 'REDO' }
    | { type: 'RESET'; payload: SequenceData };

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
                future: []
            };

        case 'UNDO':
            if (state.past.length === 0) return state;
            return {
                past: state.past.slice(0, -1),
                present: state.past[state.past.length - 1],
                future: [state.present, ...state.future]
            };

        case 'REDO':
            if (state.future.length === 0) return state;
            return {
                past: [...state.past, state.present],
                present: state.future[0],
                future: state.future.slice(1)
            };

        case 'RESET':
            return {
                past: [],
                present: action.payload,
                future: []
            };

        default:
            return state;
    }
}

function featureEquals(a: SequenceData['features'][number], b: SequenceData['features'][number]): boolean {
    return (
        a.id === b.id &&
        a.name === b.name &&
        a.type === b.type &&
        a.start === b.start &&
        a.end === b.end &&
        a.strand === b.strand &&
        a.color === b.color &&
        a.description === b.description &&
        JSON.stringify(a.notes ?? null) === JSON.stringify(b.notes ?? null)
    );
}

function primerEquals(a: NonNullable<SequenceData['primers']>[number], b: NonNullable<SequenceData['primers']>[number]): boolean {
    return (
        a.id === b.id &&
        a.name === b.name &&
        a.sequence === b.sequence &&
        a.start === b.start &&
        a.end === b.end &&
        a.strand === b.strand &&
        a.tm === b.tm &&
        a.gc_percent === b.gc_percent
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

function sequenceDataEquals(a: SequenceData, b: SequenceData): boolean {
    if (a === b) return true;
    if (
        a.name !== b.name ||
        a.description !== b.description ||
        a.sequence !== b.sequence ||
        a.circular !== b.circular ||
        a.sequenceType !== b.sequenceType
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

    return true;
}

// ═══════════════════════════════════════════════════════════════════════════════
// HOOK
// ═══════════════════════════════════════════════════════════════════════════════

export function useSequenceHistory(initialState: SequenceData) {
    const [state, dispatch] = useReducer(historyReducer, {
        past: [],
        present: initialState,
        future: []
    });

    const set = useCallback((value: SequenceData) => {
        dispatch({ type: 'SET', payload: value });
    }, []);

    const undo = useCallback(() => {
        dispatch({ type: 'UNDO' });
    }, []);

    const redo = useCallback(() => {
        dispatch({ type: 'REDO' });
    }, []);

    const reset = useCallback((value: SequenceData) => {
        dispatch({ type: 'RESET', payload: value });
    }, []);

    return {
        sequenceData: state.present,
        set,
        undo,
        redo,
        reset,
        canUndo: state.past.length > 0,
        canRedo: state.future.length > 0,
        historyLength: state.past.length
    };
}
