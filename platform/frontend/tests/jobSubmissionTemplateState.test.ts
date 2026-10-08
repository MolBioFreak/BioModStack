import assert from 'node:assert/strict';
import test from 'node:test';

import { getDedicatedTemplateInitialValues, isDedicatedLauncherTemplate } from '../src/components/jobSubmissionTemplateState.js';

test('dedicated launcher templates all suppress the generic launcher chrome', () => {
    assert.equal(isDedicatedLauncherTemplate('mutagenesis'), true);
    assert.equal(isDedicatedLauncherTemplate('antibody_child'), false);
    assert.equal(isDedicatedLauncherTemplate('structure_prediction'), true);
    assert.equal(isDedicatedLauncherTemplate('boltz_cp_experimental'), false);
    assert.equal(isDedicatedLauncherTemplate('esmfold2'), false);
    assert.equal(isDedicatedLauncherTemplate('esmfold2_experimental'), false);
    assert.equal(isDedicatedLauncherTemplate('boltzgen_design'), false);
    assert.equal(isDedicatedLauncherTemplate('retired binder workflow'), false);
    assert.equal(isDedicatedLauncherTemplate('oligo_design'), true);
    assert.equal(isDedicatedLauncherTemplate('protein_local_redesign'), false);
    assert.equal(isDedicatedLauncherTemplate('protein_modification_experimental'), true);
    assert.equal(isDedicatedLauncherTemplate('unknown_template'), false);
    assert.equal(isDedicatedLauncherTemplate(null), false);
});

test('dedicated template seeds are returned as fresh top-level drafts, with unsupported and unseeded templates distinct', () => {
    const initial = getDedicatedTemplateInitialValues('conformational_mapping')!;
    assert.equal(initial.backend, 'protenix_v2_ensemble');
    assert.deepEqual(initial.ordered_seeds, [101, 202, 303, 404, 505]);
    initial.name = 'operator draft';
    const reopened = getDedicatedTemplateInitialValues('conformational_mapping')!;
    assert.notEqual(initial, reopened);
    assert.equal(reopened.name, 'Conformational mapping');
    assert.equal(getDedicatedTemplateInitialValues('structure_prediction'), undefined);
    assert.equal(getDedicatedTemplateInitialValues('unknown_template'), undefined);
});
