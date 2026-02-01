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
            if (JSON.stringify(action.payload) === JSON.stringify(state.present)) {
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
