"""Adapt the existing controller-generated MSA handoff to Shape monomers.

Provider selection and settings belong to the declared service, never to a
worker-side search or predictor fallback.
"""
from pathlib import Path
import sys


def generated_chain(validator: str, sequence: str) -> dict:
    scripts = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(scripts / 'lib'))
    sys.path.insert(0, str(scripts.parent))
    from component_adapter import await_external_service
    from biomodstack_msa_handoff import hydrate_prepared_protenix_task
    roster = [{'name': 'shape', 'sequences': [{'proteinChain': {'sequence': sequence, 'count': 1}}]}]
    source, sha = await_external_service('shape:' + validator + ':generated_msa', roster)
    return hydrate_prepared_protenix_task(roster, source, sha)[0]['sequences'][0]['proteinChain']


def boltz_alignment(sequence: str, output: Path) -> str:
    chain = generated_chain('boltz2', sequence)
    from biomodstack_boltz_msa import write_paired_csv
    unpaired = (Path(chain['unpairedMsaPath']).read_bytes() if chain.get('unpairedMsaPath')
                else ('>query\n' + sequence + '\n').encode())
    if chain.get('pairedMsaPath'):
        destination = output / 'shape_msa.csv'
        write_paired_csv(destination, unpaired, Path(chain['pairedMsaPath']).read_bytes(),
                         deduplicates_paired=False)
    else:
        destination = output / 'shape_msa.a3m'
        destination.write_bytes(unpaired)
    return str(destination.resolve())
