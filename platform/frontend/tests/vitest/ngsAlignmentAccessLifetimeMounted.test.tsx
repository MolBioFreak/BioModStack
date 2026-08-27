import React, { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { AlignmentAccessPageLifetime } from '../../src/components/NGSToolkit';

describe('alignment capability page lifetime', () => {
    it('does not dispose the active Job during the StrictMode effect rehearsal', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const client = new QueryClient();
        const generation = { current: 0 };
        const dispose = vi.fn(async (_jobId: string) => undefined);
        client.setQueryData(['alignment-sessions', 'job-a'], { ready: true });

        await act(async () => {
            root.render(
                <StrictMode>
                    <AlignmentAccessPageLifetime
                        jobId="job-a"
                        queryClient={client}
                        recoveryGenerationRef={generation}
                        dispose={dispose}
                    />
                </StrictMode>,
            );
        });

        expect(dispose).not.toHaveBeenCalled();
        expect(client.getQueryData(['alignment-sessions', 'job-a'])).toEqual({ ready: true });
        expect(generation.current).toBe(0);

        await act(async () => root.unmount());
        expect(dispose).toHaveBeenCalledOnce();
        expect(client.getQueryData(['alignment-sessions', 'job-a'])).toBeUndefined();
        expect(generation.current).toBe(1);
        client.clear();
        document.body.replaceChildren();
    });

    it('revokes and removes each owned Job generation on switch and unmount', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const client = new QueryClient();
        const generation = { current: 0 };
        const dispose = vi.fn(async (_jobId: string) => undefined);
        client.setQueryData(['alignment-sessions', 'job-a'], { ready: true });
        client.setQueryData(['alignment-sessions', 'job-b'], { ready: true });

        await act(async () => {
            root.render(
                <AlignmentAccessPageLifetime
                    jobId="job-a"
                    queryClient={client}
                    recoveryGenerationRef={generation}
                    dispose={dispose}
                />,
            );
        });
        await act(async () => {
            root.render(
                <AlignmentAccessPageLifetime
                    jobId="job-b"
                    queryClient={client}
                    recoveryGenerationRef={generation}
                    dispose={dispose}
                />,
            );
        });

        expect(dispose).toHaveBeenCalledWith('job-a');
        expect(client.getQueryData(['alignment-sessions', 'job-a'])).toBeUndefined();
        expect(client.getQueryData(['alignment-sessions', 'job-b'])).toEqual({ ready: true });
        expect(generation.current).toBe(1);

        await act(async () => root.unmount());
        expect(dispose).toHaveBeenCalledWith('job-b');
        expect(client.getQueryData(['alignment-sessions', 'job-b'])).toBeUndefined();
        expect(generation.current).toBe(2);
        client.clear();
        document.body.replaceChildren();
    });
});
