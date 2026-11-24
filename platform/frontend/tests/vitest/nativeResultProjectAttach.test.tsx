import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const dialog = vi.hoisted(() => ({ props: [] as Array<Record<string, any>> }));
vi.mock('../../src/components/project-manager/ProjectAttachmentDialog', () => ({
    ProjectAttachmentDialog: (props: Record<string, any>) => { dialog.props.push(props); return props.open ? <div data-testid="attach-dialog" /> : null; },
}));
import { NativeResultProjectAttach } from '../../src/components/ngs/NativeResultProjectAttach';

let container: HTMLDivElement;
let root: Root;
beforeEach(() => { dialog.props.length = 0; container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); });

it('attaches the run by the workflow-aware analysis adapter, which needs no construct-QC manifest', async () => {
    await act(async () => root.render(<NativeResultProjectAttach jobId="job-9" label="Clone run" />));
    expect(container.querySelector('[data-testid="attach-dialog"]')).toBeNull();
    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="native-result-add-to-project"]')!.click());
    expect(container.querySelector('[data-testid="attach-dialog"]')).not.toBeNull();
    const last = dialog.props[dialog.props.length - 1];
    expect(last.source).toEqual({ adapterId: 'bms.ngs.analysis-reference.adapter.v1', entityId: 'job-9', label: 'Clone run', availability: 'available' });
    await act(async () => last.onClose());
    expect(container.querySelector('[data-testid="attach-dialog"]')).toBeNull();
});
