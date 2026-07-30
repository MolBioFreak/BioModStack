import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BoltzApiProviderStatus } from '../../src/lib/api';
import { buildBoltzApiStructureRequest } from '../../src/components/structurePredictionUiState';
import { BoltzApiNativeSettingsPanel } from '../../src/components/BoltzApiNativeSettings';

afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
});

const providerStatus: BoltzApiProviderStatus = {
    available: true,
    cli_available: true,
    credential_configured: true,
    model: 'boltz-2.1',
    message: 'Provider configured.',
    capabilities: {
        contract_version: 'bms.boltz_api.capabilities.v1',
        entities: { status: 'supported', types: ['protein', 'dna', 'rna', 'ligand_ccd', 'ligand_smiles'] },
        msa: { status: 'supported', provider_default: 'omit', disable_value: { type: 'empty' } },
        num_samples: { status: 'supported', minimum: 1, maximum: 10 },
        templates: { status: 'unavailable_pending_schema_verification' },
        unsupported_local_controls: {
            diffusion_sampling_steps: 'unsupported',
            recycling_steps: 'unsupported',
            potentials: 'unsupported',
            denoiser_chunking: 'unsupported',
            gpu_pinning: 'unsupported',
            parallelism: 'unsupported',
            oom_retry: 'unsupported',
            conditioning: 'unsupported',
        },
    },
    cli_update: {
        check_status: 'unavailable_pending_official_feed_verification',
        installed_version: '0.35.0',
        latest_version: null,
        source: 'boltz_api_static_cli',
        release_feed_url: null,
        release_url: null,
        checked_at: null,
    },
};

describe('Boltz API native controls', () => {
    it('whitelists only provider-native fields into the remote request', () => {
        const request = buildBoltzApiStructureRequest({
            name: 'remote-complex',
            clientRequestId: 'request-123',
            sequence: 'ACDE',
            primaryChainId: 'A',
            complexComponents: [{ id: 'B', type: 'ligand', ccd: 'ATP' }],
            numSamples: 50,
            useMsa: true,
            localControls: {
                pinnedGpus: [0, 1],
                diffusionSamplingSteps: 200,
                recyclingSteps: 3,
                usePotentials: true,
                denoiserChunkLimit: 2,
            },
        });

        expect(request).toEqual({
            name: 'remote-complex',
            client_request_id: 'request-123',
            model: 'boltz-2.1',
            sequence: 'ACDE',
            primary_chain_id: 'A',
            complex_components: [{ type: 'ligand_ccd', chain_ids: ['B'], value: 'ATP' }],
            num_samples: 10,
            use_msa: true,
        });
        expect(request).not.toHaveProperty('pinned_gpus');
        expect(request).not.toHaveProperty('boltz_sampling_steps');
        expect(request).not.toHaveProperty('boltz_recycling_steps');
    });

    it('reveals read-only unavailable update status without invoking an updater', async () => {
        const fetchSpy = vi.spyOn(globalThis, 'fetch');
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<BoltzApiNativeSettingsPanel status={providerStatus} />);
        });

        expect(container.textContent).toContain('Boltz API–native settings');
        expect(container.textContent).toContain('Templates are unavailable pending provider schema verification.');
        const updateButton = container.querySelector<HTMLButtonElement>('[data-bms-boltz-api-update-status]');
        expect(updateButton).toBeTruthy();

        await act(async () => {
            updateButton?.click();
        });

        expect(fetchSpy).not.toHaveBeenCalled();
        expect(container.textContent).toContain('Update availability is unavailable pending official feed verification.');
        expect(container.textContent).toContain('Installed CLI version: 0.35.0');
        expect(container.textContent).not.toContain('An update is available');
        expect(container.textContent).not.toContain('CLI is current');

        await act(async () => { root.unmount(); });
    });
});
