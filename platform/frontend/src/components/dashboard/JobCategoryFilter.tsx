import type { JobCategoryVisibility } from '../../lib/jobCategories';

interface Props {
    value: JobCategoryVisibility;
    onChange: (value: JobCategoryVisibility) => void;
}

/** Visibility changes only what is shown, never Job execution or cancellation. */
export function JobCategoryFilter({ value, onChange }: Props) {
    return (
        <details className="relative rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200">
            <summary className="cursor-pointer select-none">Job types</summary>
            <div className="absolute right-0 z-20 mt-2 min-w-44 space-y-2 rounded border border-slate-700 bg-slate-900 p-3 shadow-lg">
                <label className="flex items-center gap-2">
                    <input type="checkbox" checked={value.ngs}
                        onChange={event => onChange({ ...value, ngs: event.target.checked })} />
                    Show NGS
                </label>
                <label className="flex items-center gap-2">
                    <input type="checkbox" checked={value.alignments}
                        onChange={event => onChange({ ...value, alignments: event.target.checked })} />
                    Show Alignments
                </label>
            </div>
        </details>
    );
}
