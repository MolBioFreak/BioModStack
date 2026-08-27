import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { FrustraMpnnStructureSelector } from '../../src/components/frustrampnn/FrustraMpnnStructureSelector';
import type { FrustraMpnnResultListItem } from '../../src/lib/frustraMpnnApi';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
afterEach(() => document.body.replaceChildren());

const result = (invocation: string, label: string, designId: string): FrustraMpnnResultListItem => ({
    invocation_id: invocation,
    operator_label: label,
    candidate_id: `candidate-${invocation}`,
    design_id: designId,
    source_artifact_id: `artifact-${invocation}`,
    source_artifact_sha256: 'a'.repeat(64),
    source_identity: {
        design_id: designId,
        artifact_id: `artifact-${invocation}`,
        artifact_sha256: 'a'.repeat(64),
        candidate_id: `candidate-${invocation}`,
    },
} as FrustraMpnnResultListItem);

describe('FrustraMPNN structure collection selector', () => {
    it('renders backend operator labels and exact design/artifact identities and selects one invocation', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const selected: string[] = [];
        await act(async () => root.render(<FrustraMpnnStructureSelector
            items={[result('one', 'Designed complex A', 'design-a'), result('two', 'Designed complex B', 'design-b')]}
            selectedInvocationId="one"
            onSelect={value => selected.push(value)}
        />));
        const selector = container.querySelector<HTMLSelectElement>('[aria-label="Structure"]')!;
        expect(selector).not.toBeNull();
        expect(Array.from(selector.options).map(option => option.textContent)).toEqual([
            'Designed complex A · Design design-a · Artifact artifact-one',
            'Designed complex B · Design design-b · Artifact artifact-two',
        ]);
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
            setter.call(selector, 'two');
            selector.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(selected).toEqual(['two']);
        await act(async () => root.unmount());
    });
});
