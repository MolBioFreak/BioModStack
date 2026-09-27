import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { BindCraft2NativeResults, type BindCraft2NativePage } from '../src/components/BindCraft2NativeResults';

test('BC2 result view keeps unknown state, missing counts and explicit draw join visible', () => {
  const page: BindCraft2NativePage = {
    schema: 'bindcraft2.native-readback.v1', arm: null, stage: 'retained', offset: 0,
    limit: 50, total: 1, metadata: null,
    accounting: { claimed_attempts: null, scored_draws: 2, retained_sequences: 1 },
    arms: [{ name: null, accounting: { claimed_attempts: null } }],
    rows: [{ design: 't_seq0', scored_design: 't_candidate2', target_state: null,
      values: { i_pTM: '0.9', Interface_Residues: '' } }],
  };
  const html = renderToStaticMarkup(React.createElement(BindCraft2NativeResults, { page, onPage: () => {} }));
  assert.match(html, /t_candidate2/);
  assert.match(html, /i_pTM/);
  assert.match(html, /Unknown \/ not emitted/);
  assert.doesNotMatch(html, /Selection unavailable/);
});
