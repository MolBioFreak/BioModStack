import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchFiles, uploadFile } from '../lib/api';

export interface FileBrowserProps {
    onSelect: (path: string) => void;
    onCancel: () => void;
    /** Optional extension filter; omitted preserves the historical all-files view. */
    accept?: string;
    title?: string;
}

export function FileBrowser({ onSelect, onCancel, accept, title = 'Select File' }: FileBrowserProps) {
    const [path, setPath] = useState('/');
    const fileInputRef = useRef<HTMLInputElement>(null);
    const queryClient = useQueryClient();

    const { data: files, error: browseError } = useQuery({
        queryKey: ['files', path],
        queryFn: () => fetchFiles(path),
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => uploadFile(path === '/' ? 'inputs' : path, file),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['files', path] });
        },
    });

    const extensions = accept?.split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
    const matches = (name: string) => !extensions?.length || extensions.some(ext => name.toLowerCase().endsWith(ext));

    const handleNavigate = (newPath: string) => {
        setPath(newPath);
    };

    const handleUp = () => {
        const parts = path.split('/').filter(p => p);
        parts.pop();
        setPath('/' + parts.join('/'));
    };

    const handleUploadClick = () => {
        if (fileInputRef.current) {
            fileInputRef.current.click();
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            uploadMutation.mutate(e.target.files[0]);
        }
        // Reset input
        e.target.value = '';
    };

    return (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl w-full max-w-2xl h-[80vh] flex flex-col shadow-2xl">
                <div className="p-4 border-b border-[var(--border-primary)] flex justify-between items-center bg-[var(--bg-tertiary)] rounded-t-xl">
                    <h3 className="font-semibold text-[var(--text-primary)]">{title}</h3>
                    <div className="flex gap-3 items-center">
                        <input
                            type="file"
                            accept={accept}
                            aria-label={`Upload ${title}`}
                            ref={fileInputRef}
                            onChange={handleFileChange}
                            className="hidden"
                        />
                        <button type="button"
                            onClick={handleUploadClick}
                            disabled={uploadMutation.isPending}
                            className="rounded-md border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-tertiary)]"
                        >
                            {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
                        </button>
                        <button type="button" aria-label="Close file browser" onClick={onCancel} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">✕</button>
                    </div>
                </div>

                <div className="p-2 border-b border-[var(--border-primary)] bg-[var(--bg-tertiary)] flex items-center gap-2">
                    <button type="button"
                        onClick={handleUp}
                        className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-50"
                        disabled={path === '/'}
                    >
                        Up
                    </button>
                    <input
                        type="text"
                        value={path}
                        aria-label="Current directory"
                        readOnly
                        className="flex-1 bg-transparent text-sm text-[var(--text-secondary)] outline-none"
                    />
                </div>

                <div className="flex-1 overflow-auto p-2">
                    {(browseError || uploadMutation.error) && <p role="alert" className="text-sm text-red-400">{String((browseError || uploadMutation.error)?.message || 'File operation failed')}</p>}
                    {files?.data.entries.filter((entry: UntypedApiValue) => entry.is_directory || matches(entry.name)).map((entry: UntypedApiValue) => (
                        <div
                            role="button"
                            tabIndex={0}
                            onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); entry.is_directory ? handleNavigate(entry.path) : onSelect(entry.path); } }}
                            key={entry.path}
                            onClick={() => entry.is_directory ? handleNavigate(entry.path) : onSelect(entry.path)}
                            className={`flex items-center gap-3 p-2 rounded cursor-pointer ${entry.is_directory
                                ? 'text-blue-400 hover:bg-blue-500/10'
                                : 'text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'
                                }`}
                        >
                            <span className={`inline-flex h-7 min-w-10 items-center justify-center rounded border text-[10px] font-semibold uppercase tracking-[0.14em] ${
                                entry.is_directory
                                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
                                    : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-primary)]'
                            }`}>
                                {entry.is_directory ? 'Dir' : 'File'}
                            </span>
                            <span className="flex-1 truncate">{entry.name}</span>
                            {!entry.is_directory && (
                                <span className="text-xs text-[var(--text-secondary)]">
                                    {(entry.size_bytes / 1024).toFixed(1)} KB
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
