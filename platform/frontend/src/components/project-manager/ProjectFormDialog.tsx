import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
    archiveProject,
    createProject,
    getProject,
    projectManagerErrorMessage,
    restoreProject,
    updateProject,
    type HierarchyMutationResult,
    type ProjectStatus,
} from '../../lib/projectManager';

interface ProjectFormDialogProps {
    open: boolean;
    mode: 'create' | 'edit';
    project?: {
        id: string;
        name: string;
        description: string;
        status?: ProjectStatus;
        head_generation: number;
    } | null;
    defaultScope?: 'global' | 'ngs_molbio_local';
    onClose: () => void;
    onSaved: (result: HierarchyMutationResult) => void;
}

const STATUS_OPTIONS: ProjectStatus[] = ['active', 'draft', 'on_hold', 'completed'];

export function ProjectFormDialog({
    open,
    mode,
    project,
    defaultScope = 'global',
    onClose,
    onSaved,
}: ProjectFormDialogProps) {
    const queryClient = useQueryClient();
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [status, setStatus] = useState<ProjectStatus>('active');

    useEffect(() => {
        if (!open) return;
        if (mode === 'edit' && project) {
            setName(project.name);
            setDescription(project.description || '');
            setStatus(project.status && project.status !== 'archived' ? project.status : 'active');
        } else {
            setName('');
            setDescription('');
            setStatus('active');
        }
    }, [open, mode, project]);

    const mutation = useMutation({
        mutationFn: async () => {
            const trimmedName = name.trim();
            if (!trimmedName) throw new Error('Project name is required.');
            if (mode === 'create') {
                return createProject({
                    schema: 'bms.project.v2',
                    name: trimmedName,
                    description: description.trim(),
                    research_objective: description.trim(),
                    status,
                    project_scope: defaultScope,
                    tags: [],
                    change_summary: 'Created project',
                });
            }
            if (!project) throw new Error('Project is missing.');
            return updateProject(project.id, {
                expected_head_generation: project.head_generation,
                name: trimmedName,
                description: description.trim(),
                research_objective: description.trim(),
                status,
                change_summary: 'Updated project details',
            });
        },
        onSuccess: async (result) => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['projects-index'] }),
                queryClient.invalidateQueries({ queryKey: ['project-hub'] }),
                queryClient.invalidateQueries({ queryKey: ['project-manager'] }),
            ]);
            onSaved(result);
        },
    });

    if (!open) return null;

    return (
        <div
            className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-4"
            onMouseDown={(event) => { if (event.currentTarget === event.target && !mutation.isPending) onClose(); }}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-labelledby="project-form-dialog-title"
                className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 text-slate-100 shadow-2xl"
            >
                <header className="flex items-center justify-between border-b border-slate-700 px-5 py-4">
                    <h2 id="project-form-dialog-title" className="text-lg font-semibold text-slate-100">
                        {mode === 'create' ? 'New Project' : 'Edit Project'}
                    </h2>
                    <button
                        type="button"
                        disabled={mutation.isPending}
                        onClick={onClose}
                        className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
                    >
                        Close
                    </button>
                </header>
                <form
                    className="space-y-4 p-5"
                    onSubmit={(event) => {
                        event.preventDefault();
                        mutation.mutate();
                    }}
                >
                    <label className="block text-xs font-semibold text-slate-200">
                        Name
                        <input
                            aria-label="Project name"
                            required
                            value={name}
                            maxLength={255}
                            onChange={(event) => setName(event.target.value)}
                            placeholder="e.g. TNF-alpha Binder Work"
                            className="mt-1.5 w-full rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none"
                        />
                    </label>
                    <label className="block text-xs font-semibold text-slate-200">
                        Description
                        <textarea
                            aria-label="Project description"
                            rows={3}
                            value={description}
                            onChange={(event) => setDescription(event.target.value)}
                            placeholder="Optional project objective or notes"
                            className="mt-1.5 w-full rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none"
                        />
                    </label>
                    <label className="block text-xs font-semibold text-slate-200">
                        Status
                        <select
                            aria-label="Project status"
                            value={status}
                            onChange={(event) => setStatus(event.target.value as ProjectStatus)}
                            className="mt-1.5 w-full rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
                        >
                            {STATUS_OPTIONS.map((option) => (
                                <option key={option} value={option}>{option.replace('_', ' ')}</option>
                            ))}
                        </select>
                    </label>
                    {mutation.isError && (
                        <p role="alert" className="rounded-lg border border-red-500/50 bg-red-950/60 px-3 py-2 text-xs text-red-200">
                            {projectManagerErrorMessage(mutation.error)}
                        </p>
                    )}
                    <div className="flex justify-end gap-2 border-t border-slate-700 pt-4">
                        <button
                            type="button"
                            disabled={mutation.isPending}
                            onClick={onClose}
                            className="rounded-lg border border-slate-600 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800"
                        >
                            Cancel
                        </button>
                        <button
                            type="submit"
                            disabled={mutation.isPending || !name.trim()}
                            className="rounded-lg bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
                        >
                            {mutation.isPending ? 'Saving…' : mode === 'create' ? 'Create Project' : 'Save changes'}
                        </button>
                    </div>
                </form>
            </section>
        </div>
    );
}

interface ProjectLifecycleDialogProps {
    open: boolean;
    action: 'archive' | 'restore';
    project: { id: string; name: string; head_generation: number } | null;
    onClose: () => void;
    onCompleted: () => void;
}

export function ProjectLifecycleDialog({
    open,
    action,
    project,
    onClose,
    onCompleted,
}: ProjectLifecycleDialogProps) {
    const queryClient = useQueryClient();
    const mutation = useMutation({
        mutationFn: async () => {
            if (!project) throw new Error('Project is missing.');
            const current = await getProject(project.id).catch(() => null);
            const generation = current?.head_generation ?? project.head_generation;
            return action === 'archive'
                ? archiveProject(project.id, generation)
                : restoreProject(project.id, generation);
        },
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['projects-index'] }),
                queryClient.invalidateQueries({ queryKey: ['project-hub'] }),
                queryClient.invalidateQueries({ queryKey: ['project-manager'] }),
            ]);
            onCompleted();
        },
    });

    if (!open || !project) return null;

    return (
        <div
            className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-4"
            onMouseDown={(event) => { if (event.currentTarget === event.target && !mutation.isPending) onClose(); }}
        >
            <section
                role="dialog"
                aria-modal="true"
                aria-labelledby="project-lifecycle-title"
                className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 text-slate-100 shadow-2xl"
            >
                <header className="border-b border-slate-700 px-5 py-4">
                    <h2 id="project-lifecycle-title" className="text-lg font-semibold text-slate-100">
                        {action === 'archive' ? 'Archive Project' : 'Restore Project'}
                    </h2>
                </header>
                <div className="space-y-4 p-5">
                    <p className="text-sm text-slate-200">
                        {action === 'archive'
                            ? `Archive "${project.name}"? Underlying jobs, sequences, NGS data, and alignments stay intact and can be restored later.`
                            : `Restore "${project.name}" to the active projects list?`}
                    </p>
                    {mutation.isError && (
                        <p role="alert" className="rounded-lg border border-red-500/50 bg-red-950/60 px-3 py-2 text-xs text-red-200">
                            {projectManagerErrorMessage(mutation.error)}
                        </p>
                    )}
                    <div className="flex justify-end gap-2 border-t border-slate-700 pt-4">
                        <button
                            type="button"
                            disabled={mutation.isPending}
                            onClick={onClose}
                            className="rounded-lg border border-slate-600 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800"
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            disabled={mutation.isPending}
                            onClick={() => mutation.mutate()}
                            className={action === 'archive'
                                ? 'rounded-lg bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50'
                                : 'rounded-lg bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50'}
                        >
                            {mutation.isPending ? 'Working…' : action === 'archive' ? 'Confirm archive' : 'Restore Project'}
                        </button>
                    </div>
                </div>
            </section>
        </div>
    );
}
