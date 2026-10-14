"""Native publication location, without changing the Job's execution owner.

The receiving scope is entered only after returned-byte verification and the
attempt write fence. Reopened archives use the existing retained-result owner.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_receiving: ContextVar[tuple | None] = ContextVar('binder_publication_receiving', default=None)


def _identity(job):
    return (str(job.id), job.retry_count or 0, job.remote_attempt_id,
            job.execution_bundle_sha256)


@contextmanager
def receiving_native_root(job, root):
    token = _receiving.set((_identity(job), Path(root).absolute()))
    try:
        yield
    finally:
        _receiving.reset(token)


def returned_native_root(job):
    """Return remote native ownership if present; leave local policy to its owner."""
    current = _receiving.get()
    if current is not None and current[0] == _identity(job):
        return current[1]
    if (job.provenance or {}).get('remote_diagnostics', {}).get('state') == 'returned':
        from services.remote_execution.executor import retained_result_view
        archive, relative, _, _ = retained_result_view(job, diagnostics=True)
        return archive / relative
    return None


def publication_root(job, supplied=None):
    owned = returned_native_root(job)
    if owned is None:
        if not job.output_dir:
            raise ValueError('Native publication has no Job output owner')
        owned = Path(job.output_dir).absolute()
    root = Path(supplied).absolute() if supplied is not None else owned
    if root != owned or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('Native publication root differs from Job owner')
    return root


def relocate_native_path(job, root, path):
    """Relocate a declared runtime path, never guess by basename or search."""
    path = Path(path)
    if not path.is_absolute():
        return root / path
    if path.is_relative_to(root):
        return path
    # Only authenticated receiving/readback grants relocation authority. A
    # stale runtime path on an ordinary local Job still fails model containment.
    if returned_native_root(job) != root:
        return path
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    origins = [job.output_dir, receipt.get('native_output_directory')]
    for origin in origins:
        if origin and path.is_relative_to(Path(origin)):
            return root / path.relative_to(Path(origin))
    return path  # Existing model containment check rejects an unrelated root.
