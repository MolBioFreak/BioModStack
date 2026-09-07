export type MetricState = {state:'ok'; value:number; reason_code:null} | {state:'unavailable'|'invalid'; value:null; reason_code:string};
export interface MetricDescriptor {metric_id:string; source:'canonical_artifact'; scope:string; unit:string; direction:'higher'|'lower'|'none'; producer_version:string; derivation_version:string}
export interface MetricSource {artifact_sha256:string; candidate_id:string; document_id:string}
export interface ScientificPoint {
    id:string; name:string; contract_revision:1; source_job_id:string; cohort_key:string;
    metrics:Record<string,number>; metric_states:Record<string,MetricState>;
    metric_descriptors:Record<string,MetricDescriptor>; metric_sources:Record<string,MetricSource|null>;
}
export interface ScientificCohort {
    cohort_key:string; design_ids:string[];
    metrics:Record<string,{observed_count:number; unavailable_count:number; invalid_count:number; descriptor:MetricDescriptor; statistics:Record<'min'|'max'|'avg'|'median'|'std_dev',number>|null; reason_code:string|null}>;
    pairs:Record<string,{x_metric:string;y_metric:string;pair_count:number;excluded_count:number;excluded_ids:string[];points:{id:string;x:number;y:number}[];correlation:MetricState}>;
}
const allowed=new Set(['design_ptm','affinity_probability','filter_rmsd','complex_plddt','plddt','plddt_mean','iptm','plddt_overall','plddt_binder','pae_overall','pae_interaction','rmsd_overall','rmsd_binder','ptm','conf_score','rog','mpnn_score','fampnn_psce','ligand_iptm','affinity_score','binder_probability','frustration_pct_high','maturation_interface_score','maturation_rmsd','maturation_delta_interface','maturation_selected_interface_score','maturation_selected_rmsd','maturation_nonselected_rmsd','ppiflow_objective_score','ppiflow_primary_loop_rmsd']);
function requireThat(condition:unknown):asserts condition {if(!condition) throw new Error('Invalid scientific analytics contract');}
function object(value:unknown):Record<string,unknown> {requireThat(value!==null&&typeof value==='object'&&!Array.isArray(value));return value as Record<string,unknown>;}
function keys(value:Record<string,unknown>,expected:string[]) {requireThat(Object.keys(value).sort().join('|')===[...expected].sort().join('|'));}
function text(value:unknown):asserts value is string {requireThat(typeof value==='string'&&value.trim().length>0);}
function finite(value:unknown):asserts value is number {requireThat(typeof value==='number'&&Number.isFinite(value));}
function count(value:unknown):asserts value is number {finite(value);requireThat(Number.isSafeInteger(value)&&value>=0);}
function identities(value:unknown):string[] {requireThat(Array.isArray(value));value.forEach(text);requireThat(new Set(value).size===value.length);return value;}
function sameDescriptor(a:MetricDescriptor,b:MetricDescriptor) {return Object.keys(a).every(k=>a[k as keyof MetricDescriptor]===b[k as keyof MetricDescriptor]);}
export function parseMetricState(value:unknown):MetricState {
    const row=object(value);keys(row,['state','value','reason_code']);
    if(row.state==='ok'){finite(row.value);requireThat(row.reason_code===null);}
    else{requireThat(row.state==='unavailable'||row.state==='invalid');requireThat(row.value===null);text(row.reason_code);}
    return value as MetricState;
}
function descriptor(value:unknown,key:string):MetricDescriptor {
    const row=object(value);keys(row,['metric_id','source','scope','unit','direction','producer_version','derivation_version']);
    requireThat(allowed.has(key)&&row.metric_id===key&&row.source==='canonical_artifact');
    text(row.scope);text(row.unit);text(row.producer_version);text(row.derivation_version);
    requireThat(row.direction==='higher'||row.direction==='lower'||row.direction==='none');
    return value as MetricDescriptor;
}
export function parseScientificPoint(value:unknown):ScientificPoint {
    const row=object(value);keys(row,['id','name','contract_revision','source_job_id','cohort_key','metrics','metric_states','metric_descriptors','metric_sources']);
    requireThat(row.contract_revision===1);text(row.id);text(row.name);text(row.source_job_id);text(row.cohort_key);
    requireThat(row.cohort_key.startsWith('v1:')&&row.cohort_key.endsWith(`:${row.source_job_id}`));
    const states=object(row.metric_states),descriptors=object(row.metric_descriptors),metrics=object(row.metrics),sources=object(row.metric_sources);
    keys(descriptors,Object.keys(states));keys(sources,Object.keys(states));
    const observed:string[]=[];let binding:string|undefined;
    for(const [key,value] of Object.entries(states)) {
        const d=descriptor(descriptors[key],key),state=parseMetricState(value);
        if(state.state==='ok'){observed.push(key);requireThat(metrics[key]===state.value);requireThat(d.producer_version!=='unverified'&&d.derivation_version!=='unverified');}
        if(sources[key]===null){requireThat(state.state!=='ok');continue;}
        const source=object(sources[key]);keys(source,['artifact_sha256','candidate_id','document_id']);
        text(source.artifact_sha256);requireThat(/^[a-f0-9]{64}$/.test(source.artifact_sha256));text(source.candidate_id);text(source.document_id);
        const current=JSON.stringify([source.candidate_id,source.document_id]);requireThat(binding===undefined||binding===current);binding=current;
    }
    keys(metrics,observed);return value as ScientificPoint;
}
export function parseMetricPoints(value:unknown):Array<ScientificPoint|{id:string;name:string;metrics:Record<string,number>;contract_revision?:null}> {
    requireThat(Array.isArray(value));const ids=new Set<string>();
    for(const item of value){const row=object(item);text(row.id);requireThat(!ids.has(row.id));ids.add(row.id);
        if(row.contract_revision!=null)parseScientificPoint(row);
        else{
            for(const key of ['source_job_id','cohort_key','metric_states','metric_descriptors','metric_sources'])requireThat(row[key]==null);
            text(row.name);Object.values(object(row.metrics)).forEach(finite);
        }}
    return value as ReturnType<typeof parseMetricPoints>;
}
export function parseScientificCohorts(value:unknown):ScientificCohort[] {
    requireThat(Array.isArray(value));const cohortKeys=new Set<string>();const allIds=new Set<string>();
    for(const item of value) {
        const row=object(item);keys(row,['cohort_key','design_ids','metrics','pairs']);text(row.cohort_key);
        requireThat(!cohortKeys.has(row.cohort_key));cohortKeys.add(row.cohort_key);
        const ordered=identities(row.design_ids),ids=new Set(ordered);for(const id of ids){requireThat(!allIds.has(id));allIds.add(id);}
        const metrics=object(row.metrics);
        for(const [key,value] of Object.entries(metrics)) {
            const m=object(value);keys(m,['observed_count','unavailable_count','invalid_count','descriptor','statistics','reason_code']);
            count(m.observed_count);count(m.unavailable_count);count(m.invalid_count);requireThat(m.observed_count+m.unavailable_count+m.invalid_count===ids.size);descriptor(m.descriptor,key);
            if(m.observed_count===0){requireThat(m.statistics===null);text(m.reason_code);}
            else{const s=object(m.statistics);keys(s,['min','max','avg','median','std_dev']);Object.values(s).forEach(finite);requireThat(m.reason_code===null);requireThat((s.min as number)<=(s.avg as number)&&(s.avg as number)<=(s.max as number)&&(s.min as number)<=(s.median as number)&&(s.median as number)<=(s.max as number)&&(s.std_dev as number)>=0);}
        }
        for(const [key,value] of Object.entries(object(row.pairs))) {
            const p=object(value);keys(p,['x_metric','y_metric','pair_count','excluded_count','excluded_ids','points','correlation']);text(p.x_metric);text(p.y_metric);
            requireThat(p.x_metric!==p.y_metric&&key===`${p.x_metric}_vs_${p.y_metric}`&&Object.hasOwn(metrics,p.x_metric)&&Object.hasOwn(metrics,p.y_metric));
            count(p.pair_count);count(p.excluded_count);const excluded=identities(p.excluded_ids);requireThat(excluded.length===p.excluded_count);
            requireThat(Array.isArray(p.points)&&p.points.length===p.pair_count&&p.pair_count+p.excluded_count===ids.size);
            const seen=new Set<string>();
            for(const value of p.points){const point=object(value);keys(point,['id','x','y']);text(point.id);requireThat(ids.has(point.id)&&!seen.has(point.id));seen.add(point.id);finite(point.x);finite(point.y);}
            for(const id of excluded){requireThat(ids.has(id)&&!seen.has(id));seen.add(id);}
            const state=parseMetricState(p.correlation);requireThat(state.state!=='ok'||(p.pair_count>=3&&state.value>=-1&&state.value<=1));
        }
    }
    return value as ScientificCohort[];
}
export function validateScientificEnvelope<T>(value:T):T {
    const row=object(value);
    const cohorts=row.scientific_cohorts===undefined?[]:parseScientificCohorts(row.scientific_cohorts);
    const points=row.points===undefined?[]:parseMetricPoints(row.points).filter((p):p is ScientificPoint=>p.contract_revision===1);
    const byId=new Map(points.map(p=>[p.id,p]));
    if(row.points!==undefined)for(const cohort of cohorts) {
        const members=cohort.design_ids.map(id=>{const p=byId.get(id);requireThat(p&&p.cohort_key===cohort.cohort_key);return p;});
        for(const p of members){keys(p.metric_descriptors,Object.keys(cohort.metrics));for(const [key,m] of Object.entries(cohort.metrics))requireThat(sameDescriptor(m.descriptor,p.metric_descriptors[key]));}
        for(const pair of Object.values(cohort.pairs)) {
            const expected=members.filter(p=>p.metric_states[pair.x_metric].state==='ok'&&p.metric_states[pair.y_metric].state==='ok');
            requireThat(expected.length===pair.points.length);
            expected.forEach((p,i)=>{const point=pair.points[i];requireThat(point.id===p.id&&point.x===p.metrics[pair.x_metric]&&point.y===p.metrics[pair.y_metric]);});
            const excluded=members.filter(p=>!expected.includes(p)).map(p=>p.id);
            requireThat(JSON.stringify(excluded)===JSON.stringify(pair.excluded_ids));
        }
    }
    return value;
}
