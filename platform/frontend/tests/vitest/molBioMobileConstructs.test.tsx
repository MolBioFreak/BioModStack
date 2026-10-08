import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MobileMolBioWorkspace } from '../../src/components/MolBioToolkit/MobileMolBioWorkspace';
import { SequenceLibrary } from '../../src/components/MolBioToolkit/MolBioToolkitV2';

const recent = [{
  id: 'seq-1', name: 'Recent construct', length: 5584,
  sequence_type: 'dna', is_circular: true, feature_count: 3,
}];
const demos = [{
  name: 'Demo plasmid', sequence: 'AAAACCGGTTTT', circular: true,
  sequenceType: 'dna', features: [], primers: [],
}];

describe('mobile SequenceLibrary integration', () => {
  let root: Root | undefined;
  let host: HTMLDivElement | undefined;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    host?.remove();
    root = undefined;
    host = undefined;
  });

  it('bounds the real shelf and gives every mobile control a 48 px target', async () => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <MobileMolBioWorkspace
        constructName="PL931"
        digestIdentity="workspace-1:recent-1"
        hasSequence
        constructPickerOpen
        surface="map"
        onBack={vi.fn()}
        onOpenConstructs={vi.fn()}
        onSurfaceChange={vi.fn()}
        constructs={(
          <SequenceLibrary
            mobile
            sequences={recent as never}
            demos={demos as never}
            demoLoading={false}
            selectedId="seq-1"
            onSelect={vi.fn()}
            onRefresh={vi.fn()}
            onLoadDemo={vi.fn()}
            loading={false}
            width={800}
          />
        )}
        map={<div>map</div>}
        sequence={<div>sequence</div>}
        details={<div>details</div>}
        digest={<div>digest</div>}
        qc={<div>qc</div>}
      />,
    ));

    const library = host.querySelector<HTMLElement>('[data-molbio-construct-library="true"]');
    expect(library).toBeTruthy();
    expect(library?.className).toContain('h-full');
    expect(library?.className).toContain('min-h-0');
    expect(library?.style.width).toBe('100%');
    const scroll = host.querySelector<HTMLElement>('[data-molbio-scroll-region="construct-shelf"]');
    expect(scroll?.className).toContain('min-h-0');
    expect(scroll?.className).toContain('overflow-y-auto');

    const demoToggle = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Demo Plasmids'));
    expect(demoToggle).toBeTruthy();
    await act(async () => demoToggle?.click());

    const labels = ['Refresh recent constructs', 'Demo Plasmids', 'Demo plasmid', 'Recent construct'];
    for (const label of labels) {
      const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
        (candidate) => candidate.title === label || candidate.textContent?.includes(label),
      );
      expect(button, label).toBeTruthy();
      expect(button?.dataset.molbioMobileTouchTarget, label).toBe('true');
      expect(button?.className, label).toContain('min-h-12');
    }
  });
});
