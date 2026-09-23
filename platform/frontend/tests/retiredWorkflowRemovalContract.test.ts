import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { isDedicatedLauncherTemplate } from '../src/components/jobSubmissionTemplateState.js';
import { WORKFLOW_MODEL_INVENTORY } from '../src/components/workflowModelInventory.js';

const readSource = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8');
const retiredBinderId = 'bind' + 'craft';

test('retired binder v1 identifier stays absent while the BC2 draft routes through the binder launcher', () => {
  const submissionSource = readSource('src', 'components', 'JobSubmission.tsx').toLowerCase();
  const launcherSource = readSource('src', 'components', 'AntibodyDenovoTemplate.tsx').toLowerCase();
  assert.equal(isDedicatedLauncherTemplate(retiredBinderId), false);
  assert.equal(WORKFLOW_MODEL_INVENTORY.some((entry) => entry.workflowId === retiredBinderId), false);
  assert.equal(submissionSource.includes(`'${retiredBinderId}'`), false);
  assert.match(submissionSource, /bindcraft2: 'antibody_denovo'/);
  assert.match(launcherSource, /bindcraft2settings/);
  assert.match(launcherSource, /native-settings/);
});
