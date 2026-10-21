#!/usr/bin/env python3
"""Execute dl_binder_design with its zero-based target boundary corrected.

Pinned installed featurize uses binder indices [0, binderlen), so the first
non-binder index is binderlen. Keep all native features/initial guesses intact.
No source or checkpoint in the installed image is modified.
"""
from pathlib import Path
import sys


def corrected_source(source):
    return source.replace('int(i) > feat_holder.binderlen', 'int(i) >= feat_holder.binderlen')


def main():
    path = Path('/dl_binder_design/af2_initial_guess/predict.py')
    sys.path.insert(0, str(path.parent))
    sys.argv[0] = str(path)
    namespace = {'__name__': '__main__', '__file__': str(path), '__package__': None}
    exec(compile(corrected_source(path.read_text()), str(path), 'exec'), namespace)


if __name__ == '__main__':
    main()
