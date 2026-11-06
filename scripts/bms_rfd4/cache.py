"""Thin placement adapter for native compile caches; never publishes JIT files.

Run before importing rfproteina (cuEquivariance/BioIR may read paths on import).
Native implementations retain their keys, file locks and atomic writes.
"""
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


SHARED = {'TORCHINDUCTOR_CACHE_DIR': 'inductor', 'TRITON_CACHE_DIR': 'triton',
          'BIOIR_KERNEL_CACHE_DIR': 'bioir-kernels'}
PRIVATE = {'CUEQ_TRITON_CACHE_DIR': 'cueq-tuning', 'CUTE_DSL_CACHE_DIR': 'cute',
           'XDG_CACHE_HOME': 'xdg'}
CACHE_KEYS = (*SHARED, *PRIVATE, 'BIOIR_TRITON_CACHE_DIR', 'CUDA_CACHE_PATH',
              'TORCH_EXTENSIONS_DIR', 'TORCH_COMPILE_CACHE_BLOB', 'BIOIR_CACHE')
PACKAGES = ('rfd4-proteina', 'torch', 'triton', 'bionemo-ir', 'cuequivariance',
            'cuequivariance-torch', 'cuequivariance-ops-cu13', 'cuequivariance-ops-torch-cu13',
            'nvidia-cutlass-dsl', 'nvidia-cuda-runtime', 'nvidia-cuda-nvrtc')


def runtime_namespace(request, environment):
    """Small installed metadata + existing immutable image identity, no SIF hash/import."""
    image = environment.get('BMS_RFD4_RUNTIME_IMAGE', environment.get('APPTAINER_CONTAINER', ''))
    digest = re.search(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', image)
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    gpu = None
    try:
        command = ['nvidia-smi', '--query-gpu=compute_cap,driver_version', '--format=csv,noheader']
        visible = environment.get('CUDA_VISIBLE_DEVICES')
        if visible:
            command += ['--id=' + visible]
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
        rows = sorted(set(row.strip() for row in result.stdout.splitlines() if row.strip()))
        if rows and all(re.fullmatch(r'\d+\.\d+,\s*[\d.]+', row) for row in rows):
            gpu = rows
    except (OSError, subprocess.SubprocessError):
        pass
    settings = request.get('settings', {})
    identity = {'image_sha256': digest.group() if digest else None,
                'versions': versions, 'gpu_architecture_driver': gpu,
                'python': list(sys.version_info[:3]), 'uid': os.getuid(),
                'groups': request.get('groups', {}),
                'configuration': {key: settings[key] for key in (
                    'nn', 'bioir', 'inference_backend', 'compile_lengths', 'precision',
                    'batch_size', 'torch_deterministic') if key in settings},
                # Native guards still own actual tensor-shape specialization.
                'compile_environment': {key: value for key, value in environment.items()
                    if (key.startswith(('TORCHINDUCTOR_', 'TRITON_', 'CUEQ_', 'BIOIR_', 'CUTE_DSL_'))
                        or key in ('TORCH_CUDA_ARCH_LIST', 'NVIDIA_TF32_OVERRIDE', 'CUBLAS_WORKSPACE_CONFIG'))
                    and key not in CACHE_KEYS}}
    namespace = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return namespace, identity


def writable_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    # A real tiny write, not os.access (which can lie on mounts/ACLs).
    with tempfile.TemporaryFile(dir=path):
        pass
    return str(path)


@contextmanager
def compile_environment(request, output):
    """Preserve explicit effective container overrides, fall back without gating execution."""
    before = dict(os.environ)
    namespace, identity = runtime_namespace(request, before)
    private = Path(output).resolve().parent / '.cache' / 'rfd4-compile' / str(os.getpid())
    shared = Path(before.get('BMS_RFD4_COMPILE_CACHE_ROOT', '/cache')) / 'rfd4' / 'compile' / str(os.getuid()) / namespace
    qualified = bool(before.get('BMS_RFD4_COMPILE_CACHE_ROOT')
                     and identity['image_sha256'] and identity['gpu_architecture_driver'])
    report = {'namespace': namespace, 'identity': identity,
              'inherited': {key: before[key] for key in CACHE_KEYS if key in before},
              'effective': {}, 'private_reasons': {}}
    changed = []
    try:
        for key, leaf in {**SHARED, **PRIVATE}.items():
            if key in before:  # Even an explicit empty value retains native semantics.
                report['effective'][key] = before[key]
                continue
            persistent = key in SHARED and qualified
            path = (shared if persistent else private) / leaf
            try:
                value = writable_directory(path)
            except OSError as exc:
                report['private_reasons'][key] = f'{type(exc).__name__}: {exc}'
                try:
                    value = writable_directory(private / leaf)
                except OSError:
                    # Keep the existing native fallback/error behavior, not a new refusal.
                    continue
                persistent = False
            os.environ[key] = value
            changed.append(key)
            report['effective'][key] = value
            if not persistent:
                report['private_reasons'].setdefault(key,
                    'native tuning/temporary cache kept process-private' if key in PRIVATE else
                    'worker cache bind, immutable image identity or GPU architecture unavailable; reuse not qualified')
        # Log through the task's existing stdout, not a new cache registry/receipt.
        print('RFD4 compile cache: ' + json.dumps(report, sort_keys=True), flush=True)
        yield report
    finally:
        for key in changed:
            os.environ.pop(key, None)
