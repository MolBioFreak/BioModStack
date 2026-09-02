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
    PCRProduct,
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
        setLoading(true);
        setError(null);
        try {
            const res = await fetchNucleotideSequences(params);
            return res.data;
        } catch (e) {
            setError(getErrorMessage(e));
            return [];
        } finally {
            setLoading(false);
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

// ═══════════════════════════════════════════════════════════════════════════════
// MOLBIO OPERATIONS
// ═══════════════════════════════════════════════════════════════════════════════

export function useMolBioOperations() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);


    // PCR
    const pcr = useCallback(async (params: {
        sequence?: string;
        sequence_id?: string;
        primer_fwd: string;
        primer_rev: string;
    }): Promise<PCRProduct | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/molbio/pcr', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    // Ligation
    const ligate = useCallback(async (params: {
        fragments: { sequence: string; left_overhang: string; right_overhang: string }[];
    }): Promise<{ ligated_sequence: string } | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/molbio/ligate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    return {
        loading,
        error,
        pcr,
        ligate
    };
}
