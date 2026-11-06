#!/usr/bin/env python3
"""Use the installed Boltz CLI with BMS's pinned chemistry grouping repair."""
from lib.boltz_fidelity import install_native_chemistry_fix

# Top-level installation is intentional: multiprocessing spawn imports this
# entrypoint before loading native parser workers.
install_native_chemistry_fix()

if __name__ == '__main__':
    from boltz.main import cli
    cli()
