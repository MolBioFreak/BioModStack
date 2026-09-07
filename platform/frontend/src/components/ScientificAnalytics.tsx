import { useState } from 'react';
import type { ScientificCohort, ScientificPoint } from '../lib/scientificAnalytics';

/** The server owns native values, compatible cohorts and complete-case pairs. */
export function ScientificAnalytics({points, cohorts}:{points:ScientificPoint[];cohorts:ScientificCohort[]}) {
    const [sortRmsd,setSortRmsd]=useState(false);
    const keys=[...new Set(points.map(point=>point.cohort_key))];
    return <section aria-label="Scientific result analytics">
        <h2>Scientific result analytics</h2>
        <p>Revision 1. Each cohort uses compatible native metrics. Statistics describe this loaded selection.</p>
        <button onClick={()=>setSortRmsd(!sortRmsd)}>Sort by RMSD (missing last)</button>
        {keys.map(key=>{
            const rows=points.filter(point=>point.cohort_key===key);
            const cohort=cohorts.find(cohort=>cohort.cohort_key===key);
            const ordered=sortRmsd?[...rows].sort((a,b)=>{
                const av=a.metric_states.rmsd_overall,bv=b.metric_states.rmsd_overall;
                if(av?.state!=='ok')return bv?.state==='ok'?1:0;
                if(bv?.state!=='ok')return -1;
                return av.value-bv.value;
            }):rows;
            return <section key={key} aria-label={`Cohort ${key}`}>
                <h3>{key}</h3>
                {cohort ? Object.entries(cohort.pairs).map(([name,pair])=>{
                    const xd=cohort.metrics[pair.x_metric].descriptor,yd=cohort.metrics[pair.y_metric].descriptor;
                    const xmax=Math.max(1,...pair.points.map(p=>p.x)),xmin=Math.min(0,...pair.points.map(p=>p.x));
                    const ymax=Math.max(1,...pair.points.map(p=>p.y)),ymin=Math.min(0,...pair.points.map(p=>p.y));
                    return <div key={name}>
                        <svg role="img" aria-label={`${pair.x_metric} versus ${pair.y_metric} complete-case scatter`} viewBox="0 0 640 350" style={{width:'100%',maxWidth:640}}>
                            <path d="M60 20V290H610" fill="none" stroke="currentColor"/>
                            <text x="180" y="335" fill="currentColor">{pair.x_metric} ({xd.unit})</text>
                            <text x="5" y="15" fill="currentColor">{pair.y_metric} ({yd.unit})</text>
                            <text x="50" y="310" fill="currentColor">{xmin}</text><text x="560" y="310" fill="currentColor">{xmax}</text>
                            <text x="10" y="290" fill="currentColor">{ymin}</text><text x="10" y="35" fill="currentColor">{ymax}</text>
                            {pair.points.map(p=><circle key={p.id} data-candidate-id={p.id} data-x={p.x} data-y={p.y} cx={60+(p.x-xmin)/(xmax-xmin)*540} cy={290-(p.y-ymin)/(ymax-ymin)*250} r="4" fill="#60a5fa"><title>{`${p.id}: ${p.x}, ${p.y}`}</title></circle>)}
                        </svg>
                        <p>Complete pairs: {pair.pair_count}. Excluded: {pair.excluded_count}. Correlation: {pair.correlation.state==='ok'?pair.correlation.value:pair.correlation.reason_code}</p>
                    </div>;
                }):<p>Paired statistics are unavailable for this response.</p>}
                <table><thead><tr><th>Candidate</th><th>Metric / scope / unit</th><th>Value or reason</th><th>Source</th></tr></thead>
                    <tbody>{ordered.flatMap(row=>Object.entries(row.metric_states).map(([metric,state])=><tr key={`${row.id}:${metric}`}>
                        <td>{row.name} ({row.id})</td><td>{metric} / {row.metric_descriptors[metric].scope} / {row.metric_descriptors[metric].unit}</td>
                        <td>{state.state==='ok'?state.value:`${state.state}: ${state.reason_code}`}</td>
                        <td>{row.source_job_id}: {row.metric_sources[metric]?.artifact_sha256 ?? 'Source unavailable'}</td>
                    </tr>))}</tbody>
                </table>
                {cohort&&<table><caption>Observed measurements only</caption><thead><tr><th>Metric</th><th>Observed</th><th>Unavailable</th><th>Invalid</th><th>Mean</th></tr></thead>
                    <tbody>{Object.entries(cohort.metrics).map(([metric,s])=><tr key={metric}><td>{metric}</td><td>{s.observed_count}</td><td>{s.unavailable_count}</td><td>{s.invalid_count}</td><td>{s.statistics?.avg??s.reason_code}</td></tr>)}</tbody>
                </table>}
            </section>;
        })}
    </section>;
}
