import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  buildActionLabel,
  ToolsInstallerView,
  type ToolsInstallerViewProps,
  type ToolsOverview,
} from '../src/components/ToolsInstaller';
import { licensesToAccept, type ToolPlan } from '../src/components/toolsInstallerPlan';

const overview: ToolsOverview = {
  selection_made: true,
  container_runtime_present: true,
  modules: [
    { module: 'robot', title: 'Liquid-handler robot control', first_party: true, selected: true, state: 'included', tools: [] },
    { module: 'ont', title: 'Nanopore sequencing (ONT)', first_party: false, selected: true, state: 'needs-tools', tools: [] },
    { module: 'design', title: 'Protein and binder design', first_party: false, selected: false, state: 'no-recipe-yet', tools: [] },
  ],
  tools: [
    { tool: 'dorado', name: 'Dorado', label: 'starting-point', modules: ['ont'], status: 'failed',
      images: [{ filename: 'dorado.sif', present: false }], weights: [],
      step: 'build:dorado.sif', reason_code: 'image_build_failed', message: 'apptainer build exited 1',
      upstream: { homepage: 'https://github.com/nanoporetech/dorado' }, license: { id: 'ONT-PL-1.0' } },
    { tool: 'boltz2', name: 'Boltz-2', label: 'starting-point', modules: ['structure_prediction'], status: 'not-installed',
      images: [], weights: [] },
  ],
};

const plan: ToolPlan = {
  steps: [
    { step: 'build:dorado.sif', approx_size_gb: 4, already_present: false },
    { step: 'weights:hac', approx_size_gb: 0.1, already_present: false, method: 'container', license: { id: 'ONT-PL-1.0' } },
    { step: 'weights:secret', already_present: false, method: 'manual', license: { id: 'PRIVATE' }, instructions: 'Place the file yourself.' },
  ],
  blockers: [],
};

function render(overrides: Partial<ToolsInstallerViewProps> = {}) {
  const noop = () => undefined;
  return renderToStaticMarkup(React.createElement(ToolsInstallerView, {
    overview, openTool: null, plan: null, logLines: [], busy: false, error: null, accepted: [], adoptPath: '',
    onToggleModule: noop, onOpen: noop, onAccept: noop, onAdoptPath: noop,
    onBuild: noop, onAdopt: noop, onVerify: noop, onRemove: noop, ...overrides,
  }));
}

test('tools screen lists chosen parts and only the tools they need, collapsed', () => {
  const html = render();
  assert.match(html, /Liquid-handler robot control/);
  assert.match(html, /Included/);
  assert.match(html, /No recipe yet/);
  assert.match(html, /Dorado/);
  assert.match(html, /data-tool-status="failed"/);
  assert.doesNotMatch(html, /Boltz-2/, 'tools for parts that were not chosen stay out of the list');
  assert.doesNotMatch(html, /Use existing image/, 'details stay collapsed until a tool is opened');
});

test('every tool is listed before any choice is made', () => {
  const html = render({ overview: { ...overview, selection_made: false } });
  assert.match(html, /Boltz-2/);
});

test('an opened failed tool shows the reason, the plan, the licence gate and the build output', () => {
  const html = render({ openTool: 'dorado', plan, logLines: ['FATAL: no space left'] });
  assert.match(html, /Failed at build:dorado\.sif \(image_build_failed\): apptainer build exited 1/);
  assert.match(html, /build dorado\.sif, about 4 GB/);
  assert.match(html, /Place the file yourself\./);
  assert.match(html, /I have read and accept/);
  assert.match(html, /FATAL: no space left/);
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Retry build<\/button>/, 'build waits for the licence');
  const acceptedHtml = render({ openTool: 'dorado', plan, accepted: ['ONT-PL-1.0'] });
  assert.doesNotMatch(acceptedHtml, /<button type="button"[^>]*disabled=""[^>]*>Retry build<\/button>/);
});

test('detected tools remain listed even when no optional modules are selected and module states stay distinct', () => {
  const detectedOverview: ToolsOverview = {
    selection_made: true,
    container_runtime_present: true,
    modules: [
      { module: 'core', title: 'Core', first_party: true, selected: true, state: 'included', tools: [] },
      { module: 'ont', title: 'Nanopore sequencing (ONT)', first_party: false, selected: false, state: 'present-unverified', tools: [{ tool: 'dorado', status: 'present-unverified' }] },
      { module: 'protein', title: 'Protein design', first_party: false, selected: false, state: 'ready', tools: [{ tool: 'boltz2', status: 'ready' }] },
      { module: 'md', title: 'Molecular dynamics', first_party: false, selected: false, state: 'needs-tools', tools: [{ tool: 'gromacs', status: 'not-installed' }] },
    ],
    tools: [
      {
        tool: 'dorado', name: 'Dorado', label: 'starting-point', modules: ['ont'], status: 'present-unverified',
        images: [{ filename: 'dorado.sif', present: true, sif: true }], weights: [],
      },
      {
        tool: 'boltz2', name: 'Boltz-2', label: 'starting-point', modules: ['protein'], status: 'ready',
        images: [{ filename: 'boltz2.sif', present: true, sif: true }], weights: [],
      },
      {
        tool: 'gromacs', name: 'GROMACS', label: 'starting-point', modules: ['md'], status: 'not-installed',
        images: [{ filename: 'gromacs.sif', present: false, sif: true }], weights: [],
      },
    ],
  };
  const html = render({ overview: detectedOverview });
  assert.match(html, /Dorado/);
  assert.match(html, /Boltz-2/);
  assert.doesNotMatch(html, /GROMACS/, 'uninstalled tool from unselected module stays hidden');
  assert.match(html, /Present, not checked/);
  assert.match(html, /Checked/);
  assert.match(html, /Missing assets/);
});

test('build action label accurately reflects image reuse vs missing images', () => {
  assert.equal(buildActionLabel({
    tool: 'a', name: 'A', label: 'starting-point', modules: ['ont'], status: 'present-unverified',
    images: [{ filename: 'a.sif', present: true, sif: true }], weights: [],
  }), 'Finish setup (reuses image)');
  assert.equal(buildActionLabel({
    tool: 'b', name: 'B', label: 'starting-point', modules: ['ont'], status: 'incomplete',
    images: [{ filename: 'one.sif', present: true, sif: true }, { filename: 'two.sif', present: false, sif: true }], weights: [],
  }), 'Build missing');
  assert.equal(buildActionLabel({
    tool: 'c', name: 'C', label: 'starting-point', modules: ['ont'], status: 'failed',
    images: [{ filename: 'c.sif', present: false, sif: true }], weights: [],
  }), 'Retry build');
  assert.equal(buildActionLabel({
    tool: 'd', name: 'D', label: 'starting-point', modules: ['ont'], status: 'not-installed',
    images: [{ filename: 'd.sif', present: false, sif: true }], weights: [],
  }), 'Build');
});

test('multi-SIF tools expose a target image picker and non-SIF tools hide adoption', () => {
  const multiOverview: ToolsOverview = {
    ...overview,
    selection_made: false,
    tools: [
      {
        tool: 'multi', name: 'Multi SIF', label: 'starting-point', modules: ['ont'], status: 'incomplete',
        images: [
          { filename: 'one.sif', present: true, sif: true },
          { filename: 'two.sif', present: false, sif: true },
        ],
        weights: [],
      },
      {
        tool: 'signal', name: 'Signal Docker', label: 'starting-point', modules: ['ont'], status: 'ready',
        images: [{ filename: 'example/signal:1', present: true, kind: 'docker', sif: false }],
        weights: [],
      },
    ],
  };
  const multiHtml = render({ overview: multiOverview, openTool: 'multi', plan: { steps: [], blockers: [] } });
  assert.match(multiHtml, /aria-label="Target image"/);
  assert.match(multiHtml, /<option value="one\.sif"[^>]*>one\.sif<\/option>/);
  assert.match(multiHtml, /<option value="two\.sif"[^>]*>two\.sif<\/option>/);
  const dockerHtml = render({ overview: multiOverview, openTool: 'signal', plan: { steps: [], blockers: [] } });
  assert.doesNotMatch(dockerHtml, /Use existing image/);
});

test('licences to accept skip manual, optional unselected, and already present steps', () => {
  assert.deepEqual(licensesToAccept(plan), [{ id: 'ONT-PL-1.0', url: undefined }]);
  const withOptional: ToolPlan = {
    ...plan,
    steps: [
      ...plan.steps,
      {
        step: 'weights:optional_sup',
        already_present: false,
        required: false,
        selected_by_default: false,
        method: 'url',
        license: { id: 'OPTIONAL-1.0', url: 'https://example.org/opt' },
      },
    ],
  };
  assert.deepEqual(licensesToAccept(withOptional), [{ id: 'ONT-PL-1.0', url: undefined }]);
  const present: ToolPlan = { ...plan, steps: plan.steps.map((step) => ({ ...step, already_present: true })) };
  assert.deepEqual(licensesToAccept(present), []);
});

test('a missing container runtime is stated', () => {
  assert.match(render({ overview: { ...overview, container_runtime_present: false } }), /Apptainer is not installed/);
});
