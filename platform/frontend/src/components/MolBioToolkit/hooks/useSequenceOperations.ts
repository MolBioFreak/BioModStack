/**
 * useSequenceOperations - API hooks for MolBio operations
 * 
 * Wraps calls to /api/sequences and /api/molbio endpoints.
 */

import { useState, useCallback } from 'react';
import type {
    NucleotideSequenceResponse,
    NucleotideSequenceListItem,
    DigestFragment,
    PCRProduct,
    Feature,
    Primer
} from '../types';

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE CRUD
// ═══════════════════════════════════════════════════════════════════════════════

export function useSequenceOperations() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // List all sequences
    const listSequences = useCallback(async (
        limit = 100,
        offset = 0
    ): Promise<NucleotideSequenceListItem[]> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/sequences?limit=${limit}&offset=${offset}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
            return [];
        } finally {
            setLoading(false);
        }
    }, []);

    // Get single sequence
    const getSequence = useCallback(async (
        id: string
    ): Promise<NucleotideSequenceResponse | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/sequences/${id}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    // Create new sequence
    const createSequence = useCallback(async (data: {
        name: string;
        sequence: string;
        description?: string;
        sequence_type?: 'dna' | 'rna';
        is_circular?: boolean;
        features?: Feature[];
        primers?: Primer[];
    }): Promise<NucleotideSequenceResponse | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/sequences', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
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

    // Update sequence
    const updateSequence = useCallback(async (
        id: string,
        data: Partial<{
            name: string;
            sequence: string;
            description: string;
            sequence_type: 'dna' | 'rna';
            is_circular: boolean;
            features: Feature[];
            primers: Primer[];
        }>
    ): Promise<NucleotideSequenceResponse | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/sequences/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
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

    // Delete sequence
    const deleteSequence = useCallback(async (id: string): Promise<boolean> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/sequences/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return true;
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
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

    // Restriction digest
    const digest = useCallback(async (params: {
        sequence?: string;
        sequence_id?: string;
        enzymes: { name: string; site?: string }[];
        is_circular?: boolean;
    }): Promise<{ fragments: DigestFragment[] } | null> => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/molbio/digest', {
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
        digest,
        pcr,
        ligate
    };
}
