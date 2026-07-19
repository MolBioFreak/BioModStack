import assert from 'node:assert/strict';
import test from 'node:test';

import { getDedicatedTemplateInitialValues, isDedicatedLauncherTemplate } from '../src/components/jobSubmissionTemplateState.js';

test('dedicated launcher templates all suppress the generic launcher chrome', () => {
    assert.equal(isDedicatedLauncherTemplate('mutagenesis'), true);
    assert.equal(isDedicatedLauncherTemplate('antibody_denovo'), true);
    assert.equal(isDedicatedLauncherTemplate('structure_prediction'), true);
    assert.equal(isDedicatedLauncherTemplate('boltz_cp_experimental'), true);
    assert.equal(isDedicatedLauncherTemplate('esmfold2'), false);
    assert.equal(isDedicatedLauncherTemplate('esmfold2_experimental'), false);
    assert.equal(isDedicatedLauncherTemplate('boltzgen_design'), false);
    assert.equal(isDedicatedLauncherTemplate('bindcraft'), false);
    assert.equal(isDedicatedLauncherTemplate('oligo_design'), true);
    assert.equal(isDedicatedLauncherTemplate('protein_local_redesign'), true);
    assert.equal(isDedicatedLauncherTemplate('unknown_template'), false);
    assert.equal(isDedicatedLauncherTemplate(null), false);
});

test('dedicated template initial values seed canonical and compatibility structure variants with workflow identity', () => {
    assert.deepEqual(getDedicatedTemplateInitialValues('boltz_cp_experimental'), {
        template_model_id: 'boltz_cp_experimental',
        template_mode_id: 'design',
        structure_launch_variant: 'boltz_cp_experimental',
    });
    assert.equal(getDedicatedTemplateInitialValues('esmfold2_experimental'), undefined);
    assert.equal(getDedicatedTemplateInitialValues('esmfold2'), undefined);
    assert.equal(getDedicatedTemplateInitialValues('structure_prediction'), undefined);
    assert.equal(getDedicatedTemplateInitialValues('unknown_template'), undefined);
});
