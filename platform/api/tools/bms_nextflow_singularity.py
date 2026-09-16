#!/usr/bin/env python3
"""Private Nextflow SingularityBuilder CLI bridge, not Singularity software.

Installed only in a managed release's nextflow/container-bin. The BMS Nextflow
launcher scopes PATH to this directory. Actual SIF tools remain real Apptainer;
this adapter neither acquires images nor pretends to implement isolation flags.
"""
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ['--version'] and (not args or args[0] != 'exec'):
        raise ValueError('BMS Nextflow adapter supports only exec and --version; no image acquisition')
    private_bin = Path(__file__).resolve().parent
    release = private_bin.parent.parent
    # SingularityBuilder deliberately resets the environment and prefixes the
    # explicit whitelist. Restore those inherited runtime fields for our driver.
    environment = dict(os.environ)
    explicit = json.loads(environment.get('SINGULARITYENV_BMS_NEXTFLOW_EXPLICIT_ENV', '[]'))
    if not isinstance(explicit, list) or any(not isinstance(k, str) or not k.startswith('SINGULARITYENV_') for k in explicit):
        raise ValueError('invalid Nextflow environment forwarding')
    for name, value in tuple(environment.items()):
        if name.startswith('SINGULARITYENV_'):
            environment[name[len('SINGULARITYENV_'):]] = value
            if name not in explicit:
                # Generated forwarding is inherited host state, not an explicit
                # override to apply after sourcing the selected SIF environment.
                environment.pop(name, None)
    environment.pop('BMS_NEXTFLOW_EXPLICIT_ENV', None)
    # Never shadow a real CLI used by native consumers or the SIF extractor.
    environment['PATH'] = os.pathsep.join(part for part in environment.get('PATH', '').split(os.pathsep)
                                        if part and Path(part).resolve() != private_bin)
    os.execve(str(release / 'bin/bms-container'), ['bms-container', *args], environment)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(125)
