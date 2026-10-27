"""Process-isolated bridge to the standalone native engine adapters."""
import json
import os
import sys
from pathlib import Path

from bms_alignment import AlignmentRequest, run_engine


def main():
    work = Path(sys.argv[1]).resolve()
    request = AlignmentRequest.model_validate_json((work / 'request.json').read_text())
    image = os.environ.get('BMS_FAMILY_MSA_SIF', '').strip()
    prefix = ['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', f'{work}:{work}', image] if image else []
    result = run_engine(request, work / 'native', prefix + [request.settings.engine], prefix + ['mafft'])
    (work / 'worker-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result['returncode']


if __name__ == '__main__':
    sys.exit(main())
