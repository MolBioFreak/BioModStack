/** Presentation only: persisted names and scientific identities are never changed. */
export function predictionDisplayLabel(design: { name: string; native_sample?: { producer_sample?: unknown; producer_rank?: unknown } | null }, index?: number): string {
    if (!/[a-f0-9]{24,}|[a-f0-9]{8}-[a-f0-9-]{27,}/i.test(design.name) && design.name.length <= 80) return design.name;
    const sample = design.name.match(/(?:sample|prediction|model)[_-]?(\d+)/i)?.[1];
    const rank = design.native_sample?.producer_rank;
    return sample != null ? `Prediction ${sample}` : rank != null ? `Prediction · producer rank ${rank}` : index != null ? `Prediction ${index + 1}` : 'Selected prediction';
}
export function predictionJobLabel(job: { name: string; model_id: string }): string {
    const validation = job.name.match(/^(.+?)[-_]design_l\d+_[a-f0-9]+_candidate(\d+)$/i);
    if (validation) return `${validation[1].replace(/[-_]+/g, ' ')} · candidate ${validation[2]}`;
    if (!/[a-f0-9]{24,}|[a-f0-9]{8}-[a-f0-9-]{27,}/i.test(job.name)) return job.name;
    const model = ({ protenix: 'Protenix', boltz2: 'Boltz-2', boltz_cp_experimental: 'Fold-CP', esmfold2: 'ESMFold2', esmfold2_experimental: 'ESMFold2' } as Record<string, string>)[job.model_id];
    return model ? `${model} prediction` : job.name;
}
