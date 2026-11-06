import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { uploadFile } from '../../lib/api';

interface Pod5UploadProps {
    selectedDirectory: string;
    onSelectDirectory: (path: string) => void;
    onBusyChange: (busy: boolean) => void;
}

/** Upload into one isolated input folder; publish that folder only after every file succeeds. */
export function Pod5Upload({ selectedDirectory, onSelectDirectory, onBusyChange }: Pod5UploadProps) {
    const active = useRef(true);
    const [progress, setProgress] = useState({ done: 0, total: 0 });
    const upload = useMutation({
        mutationFn: async (files: File[]) => {
            if (!files.length || files.some((file) => !file.name.toLowerCase().endsWith('.pod5'))) {
                throw new Error('Choose one or more .pod5 files.');
            }
            if (new Set(files.map((file) => file.name.toLowerCase())).size !== files.length) {
                throw new Error('POD5 filenames must be unique within this upload.');
            }
            const destination = `inputs/ont-analysis/${crypto.randomUUID()}`;
            let selectedDirectory = '';
            for (let index = 0; index < files.length; index++) {
                if (!active.current) throw new Error('The upload view was closed.');
                const response = await uploadFile(destination, files[index]);
                if (!active.current) throw new Error('The upload view was closed.');
                if (typeof response.data?.path !== 'string') {
                    throw new Error('The server did not return a usable POD5 input folder.');
                }
                const path = response.data.path.replace(/\\/g, '/');
                const separator = path.lastIndexOf('/');
                if (separator < 1) throw new Error('The server did not return a usable POD5 input folder.');
                const directory = path.slice(0, separator);
                if (selectedDirectory && selectedDirectory !== directory) {
                    throw new Error('The uploaded files were not saved to the same input folder.');
                }
                selectedDirectory = directory;
                setProgress({ done: index + 1, total: files.length });
            }
            return selectedDirectory;
        },
        onMutate: (files) => {
            onSelectDirectory('');
            setProgress({ done: 0, total: files.length });
        },
        onSuccess: (directory) => { if (active.current) onSelectDirectory(directory); },
    });
    useEffect(() => {
        active.current = true;
        return () => { active.current = false; };
    }, []);
    useEffect(() => {
        onBusyChange(upload.isPending);
        return () => onBusyChange(false);
    }, [upload.isPending, onBusyChange]);

    return <div className="mt-3 space-y-2">
        <label className="block text-xs font-medium text-[var(--text-secondary)]">
            Or upload POD5 files
            <input
                aria-label="Upload POD5 files"
                type="file"
                accept=".pod5"
                multiple
                disabled={upload.isPending}
                className="mt-1 block w-full min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)] disabled:cursor-wait"
                onChange={(event) => {
                    const files = Array.from(event.target.files ?? []);
                    if (files.length) upload.mutate(files);
                    event.target.value = '';
                }}
            />
        </label>
        {upload.isPending && <p role="status" className="text-xs text-[var(--text-secondary)]">Uploading POD5 files: {progress.done} of {progress.total}. The input becomes available when all files finish.</p>}
        {upload.isSuccess && <p role="status" className="text-xs text-[var(--text-secondary)]">{progress.total} POD5 {progress.total === 1 ? 'file' : 'files'} uploaded.{selectedDirectory === upload.data ? ' The input folder above is selected.' : ''}</p>}
        {upload.isError && <p role="alert" className="text-xs text-[var(--text-error)]">POD5 upload failed: {upload.error instanceof Error ? upload.error.message : 'Check the connection and retry.'} No partial upload is selected for analysis.</p>}
    </div>;
}
