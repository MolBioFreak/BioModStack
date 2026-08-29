import React, { act, useState } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { FrustraMpnnSettingsPanel } from '../../src/components/frustrampnn/FrustraMpnnSettingsPanel';
import { buildAntibodyContinuationParamOverrides } from '../../src/components/antibodyContinuationParams';
import {
    CANONICAL_FRUSTRAMPNN_SETTINGS,
    hydrateFrustraMpnnSettings,
    type FrustraMpnnRequestedSettings,
} from '../../src/components/frustrampnn/frustraMpnnSettingsState';

const source = () => readFileSync('src/components/ResultsViewer.tsx', 'utf8');

const mount = async (node: React.ReactNode) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(node));
    return { container, root };
};

function ContinuationHarness({
    persisted,
    onSubmit,
}: {
    persisted: unknown;
    onSubmit: (payload: Record<string, unknown>) => void;
}) {
    const [enabled, setEnabled] = useState(true);
    const [settings, setSettings] = useState<FrustraMpnnRequestedSettings>(() => hydrateFrustraMpnnSettings(persisted));
    return <div>
        <label>
            <input
                data-continuation-frustrampnn-enabled
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.currentTarget.checked)}
            />
            FrustraMPNN
        </label>
        {enabled ? <FrustraMpnnSettingsPanel value={settings} onChange={setSettings} /> : null}
        <button
            type="button"
            onClick={() => onSubmit(buildAntibodyContinuationParamOverrides({}, enabled, settings))}
        >
            Continue
        </button>
    </div>;
}

afterEach(() => {
    document.body.replaceChildren();
});

describe('ResultsViewer antibody continuation FrustraMPNN contract', () => {
    it('composes the authoritative settings panel and canonical launch serialization', () => {
        const resultsViewer = source();
        expect(resultsViewer).toContain("from './frustrampnn/FrustraMpnnSettingsPanel.js'");
        expect(resultsViewer).toContain('hydrateFrustraMpnnSettings');
        expect(resultsViewer).toContain('buildAntibodyContinuationParamOverrides');
        expect(resultsViewer).toContain('settingsControl={frustrampnnSettingsControl}');
        expect(resultsViewer).toMatch(/frustrampnn_settings:\s*frustrampnnSettings/);
    });

    it('round-trips v2 batching controls into the complete continuation payload', async () => {
        const submissions: Record<string, unknown>[] = [];
        const persisted = {
            ...CANONICAL_FRUSTRAMPNN_SETTINGS,
            batching_enabled: true,
            structures_per_job: 17,
        };
        const { container, root } = await mount(<ContinuationHarness persisted={persisted} onSubmit={(payload) => submissions.push(payload)} />);

        const enabled = container.querySelector<HTMLInputElement>('[data-continuation-frustrampnn-enabled]');
        const batching = container.querySelector<HTMLInputElement>('[data-frustrampnn-batching-enabled]');
        const count = container.querySelector<HTMLInputElement>('[data-frustrampnn-structures-number]');
        expect(enabled?.checked).toBe(true);
        expect(batching?.checked).toBe(true);
        expect(count?.value).toBe('17');

        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(count, '23');
            count!.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Continue')!.click());

        expect(submissions).toHaveLength(1);
        expect(submissions[0]).toMatchObject({
            run_frustrampnn: true,
            frustrampnn_requiredness: 'required',
            frustrampnn_settings: {
                schema_name: 'frustrampnn_settings',
                schema_version: 2,
                batching_enabled: true,
                structures_per_job: 23,
            },
        });
        await act(async () => root.unmount());
    });

    it('hydrates legacy v1 settings to disabled batching and preserves disabled submission behavior', async () => {
        const legacy = {
            schema_name: 'frustrampnn_settings',
            schema_version: 1,
            protein_selection: CANONICAL_FRUSTRAMPNN_SETTINGS.protein_selection,
            source_structure: CANONICAL_FRUSTRAMPNN_SETTINGS.source_structure,
            classification_policy: CANONICAL_FRUSTRAMPNN_SETTINGS.classification_policy,
        };
        const submissions: Record<string, unknown>[] = [];
        const { container, root } = await mount(<ContinuationHarness persisted={legacy} onSubmit={(payload) => submissions.push(payload)} />);

        expect(container.querySelector<HTMLInputElement>('[data-frustrampnn-batching-enabled]')?.checked).toBe(false);
        expect(container.querySelector<HTMLInputElement>('[data-frustrampnn-structures-number]')?.value).toBe('1');
        await act(async () => container.querySelector<HTMLInputElement>('[data-continuation-frustrampnn-enabled]')!.click());
        expect(container.querySelector('[data-frustrampnn-settings-panel]')).toBeNull();
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Continue')!.click());
        expect(submissions).toEqual([{ run_frustrampnn: false }]);
        await act(async () => root.unmount());
    });
});
