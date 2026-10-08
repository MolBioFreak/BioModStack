import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { isDedicatedLauncherTemplate } from '../src/components/jobSubmissionTemplateState.js';
import { WORKFLOW_MODEL_INVENTORY } from '../src/components/workflowModelInventory.js';

const readSource = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8');
const retiredBinderId = 'bind' + 'craft';
const forbiddenTokens = [retiredBinderId];

test('BindCraft is absent from active launcher and inventory surfaces', () => {
  const submissionSource = readSource('src', 'components', 'JobSubmission.tsx').toLowerCase();

  for (const token of forbiddenTokens) {
    assert.equal(isDedicatedLauncherTemplate(token), false);
    assert.equal(WORKFLOW_MODEL_INVENTORY.some((entry) => entry.workflowId === token), false);
    assert.equal(submissionSource.includes(token), false);
  }
});
