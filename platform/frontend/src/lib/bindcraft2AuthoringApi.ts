import { api } from './api';
import type { BC2Request } from '../components/BindCraft2Settings';

export interface BC2CampaignPreview {
    preview_digest: string;
    requested_settings: BC2Request;
    effective_settings: Record<string, unknown>;
    warnings?: unknown[];
    blockers?: unknown[];
    note?: string;
    [key: string]: unknown;
}

/** The model-owned compiler previews the same scientific request used at launch. */
export async function previewBindCraft2Campaign(settings: BC2Request): Promise<BC2CampaignPreview> {
    return (await api.post<BC2CampaignPreview>('/api/models/bindcraft2/campaign/preview', {
        model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings },
    })).data;
}
