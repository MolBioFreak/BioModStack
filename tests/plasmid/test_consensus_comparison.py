import hashlib
import json
import pytest
from compare_plasmid_consensus import prepare_comparison
from verify_construct import check_consensus_comparison


def setup(tmp_path,read_sequence):
    source=tmp_path/'source';source.mkdir()
    observed=source/'observed_consensus.fasta';observed.write_text('>assembly\nACGTTG\n')
    digest=hashlib.sha256(observed.read_bytes()).hexdigest()
    (source/'observed_state.json').write_text(json.dumps({'observed_sha256':digest}))
    read=tmp_path/'read.fasta';read.write_text('>read\n'+read_sequence+'\n')
    return source,read


def test_rotated_consensus_agreement_retains_both(tmp_path):
    source,read=setup(tmp_path,'TTGACG');original=(source/'observed_state.json').read_bytes()
    out=tmp_path/'out';result=prepare_comparison(source,read,out)
    assert result['exact_circular_agreement'] is True
    assert (source/'observed_state.json').read_bytes()==original
    state=json.loads((out/'observed_state.json').read_text())
    checked=check_consensus_comparison(state,out,'ACGTTG')
    assert checked['agreement'] is True
    assert checked['independent_biological_replication'] is False


def test_disagreement_is_not_overwritten(tmp_path):
    source,read=setup(tmp_path,'ACGATG');out=tmp_path/'out'
    prepare_comparison(source,read,out)
    state=json.loads((out/'observed_state.json').read_text())
    assert check_consensus_comparison(state,out,'ACGTTG')['agreement'] is False
    assert (out/'observed_consensus.fasta').read_text()==(source/'observed_consensus.fasta').read_text()


def test_tampered_read_consensus_is_rejected(tmp_path):
    source,read=setup(tmp_path,'ACGTTG');out=tmp_path/'out'
    prepare_comparison(source,read,out)
    state=json.loads((out/'observed_state.json').read_text())
    replacement=out/'replacement.fasta'
    replacement.write_text('>x\nAAAAAA\n')
    replacement.replace(out/'read_guided_consensus.fasta')
    with pytest.raises(ValueError,match='DIGEST_MISMATCH'):
        check_consensus_comparison(state,out,'ACGTTG')


def test_retained_comparison_payloads_share_but_state_is_private(tmp_path):
    source,read=setup(tmp_path,'ACGTTG')
    observed=source/'observed_consensus.fasta'
    observed.chmod(0o444)
    read.chmod(0o444)
    alias=tmp_path/'staged_bundle'
    alias.symlink_to(source, target_is_directory=True)
    out=tmp_path/'out'
    prepare_comparison(alias,read,out)
    assert (out/'observed_consensus.fasta').stat().st_ino == observed.stat().st_ino
    assert (out/'read_guided_consensus.fasta').stat().st_ino == read.stat().st_ino
    assert (out/'observed_state.json').stat().st_ino != (source/'observed_state.json').stat().st_ino
    observed.unlink()
    read.unlink()
    state=json.loads((out/'observed_state.json').read_text())
    assert check_consensus_comparison(state,out,'ACGTTG')['agreement'] is True
