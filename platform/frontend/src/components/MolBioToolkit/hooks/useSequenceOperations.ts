/**
 * useSequenceOperations - API hooks for MolBio operations
 * 
 * Wraps calls to /api/sequences and /api/molbio endpoints.
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { isAxiosError } from 'axios';
import type {
    NucleotideSequenceResponse,
    NucleotideSequenceListItem,
} from '../types';
import {
    fetchNucleotideSequences,
    fetchNucleotideSequence,
    createNucleotideSequence,
    updateNucleotideSequence,
    deleteNucleotideSequence,
    type FetchNucleotideSequencesParams,
    type NucleotideSequenceCreate,
} from '../../../lib/api';
import { createLatestAsyncResourceController } from '../../../lib/latestAsyncResource';

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE CRUD
// ═══════════════════════════════════════════════════════════════════════════════

export function useSequenceOperations() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const getSequenceControllerRef = useRef(createLatestAsyncResourceController());
    const listControllerRef = useRef(createLatestAsyncResourceController());
    useEffect(() => () => listControllerRef.current.dispose(), []);

    useEffect(() => () => getSequenceControllerRef.current.dispose(), []);

    const getErrorMessage = (value: unknown): string => {
        if (isAxiosError(value)) {
            return value.response?.data?.detail || value.message;
        }
        if (value instanceof Error) {
            return value.message;
        }
        return 'Unknown error';
    };

    // List all sequences
    const listSequences = useCallback(async (
        params: FetchNucleotideSequencesParams = {}
    ): Promise<NucleotideSequenceListItem[]> => {
        const token = listControllerRef.current.begin();
        setLoading(true);
        setError(null);
        try {
            const res = await fetchNucleotideSequences(params);
            return res.data;
        } catch (e) {
            if (listControllerRef.current.isCurrent(token)) setError(getErrorMessage(e));
            return [];
        } finally {
            if (listControllerRef.current.isCurrent(token)) setLoading(false);
        }
    }, []);

    const invalidateGetSequence = useCallback(() => {
        getSequenceControllerRef.current.dispose();
        setLoading(false);
        setError(null);
    }, []);

    // Get single sequence
    const getSequence = useCallback(async (
        id: string
    ): Promise<NucleotideSequenceResponse | null> => {
        const requestToken = getSequenceControllerRef.current.begin();
        setLoading(true);
        setError(null);
        try {
            const res = await fetchNucleotideSequence(id);
            if (!getSequenceControllerRef.current.isCurrent(requestToken)) return null;
            return res.data as NucleotideSequenceResponse;
        } catch (e) {
            if (getSequenceControllerRef.current.isCurrent(requestToken)) {
                setError(getErrorMessage(e));
            }
            return null;
        } finally {
            if (getSequenceControllerRef.current.isCurrent(requestToken)) {
                setLoading(false);
            }
        }
    }, []);

    // Create new sequence
    const createSequence = useCallback(async (data: NucleotideSequenceCreate): Promise<NucleotideSequenceResponse | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await createNucleotideSequence(data);
            return res.data as NucleotideSequenceResponse;
        } catch (e) {
            setError(getErrorMessage(e));
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    // Update sequence
    const updateSequence = useCallback(async (
        id: string,
        data: Partial<NucleotideSequenceCreate>
    ): Promise<NucleotideSequenceResponse | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await updateNucleotideSequence(id, data);
            return res.data as NucleotideSequenceResponse;
        } catch (e) {
            setError(getErrorMessage(e));
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    // Delete sequence
    const deleteSequence = useCallback(async (id: string): Promise<boolean> => {
        setLoading(true);
        setError(null);
        try {
            await deleteNucleotideSequence(id);
            return true;
        } catch (e) {
            setError(getErrorMessage(e));
            return false;
        } finally {
            setLoading(false);
        }
    }, []);

    return {
        loading,
        error,
        listSequences,
        getSequence,
        invalidateGetSequence,
        createSequence,
        updateSequence,
        deleteSequence
    };
}
