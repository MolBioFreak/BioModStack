import {test} from 'node:test';
import assert from 'node:assert/strict';
import {api, fetchJobDesignMetrics} from '../src/lib/api';

test('raw API consumer rejects a new-contract boolean masquerading as a measurement', async () => {
    api.defaults.adapter = async config => ({config, status:200, statusText:'OK', headers:{}, data:[{
        id:'a', name:'A', contract_revision:1, source_job_id:'j', cohort_key:'v1:p:j',
        metrics:{ptm: true}, metric_states:{ptm:{state:'ok', value:true, reason_code:null}},
        metric_descriptors:{ptm:{metric_id:'ptm',source:'Design.ptm',scope:'overall',unit:'dimensionless',direction:'higher'}}
    }]});
    await assert.rejects(fetchJobDesignMetrics('j'));
});
