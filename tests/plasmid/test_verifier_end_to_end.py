"""Actual Python verifier execution with synthetic records and mocked samtools IO.

These tests exercise decision/provenance/serialization code, NOT native BAM,
basecalling, assembly, biological accuracy, Nextflow, or browser acceptance.
"""
import csv
import hashlib
import json
import random
from pathlib import Path
import pytest
import verify_construct as verify
from build_construct_topology_evidence import derive_topology_evidence


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(tmp_path,monkeypatch,*,gap=False,missing_screen=False,contradictory=False,tamper=False,comparison=None,qualified=True):
    ref=''.join(random.Random(42).choices('ACGT',k=128))
    records=[];fastq=[]
    for i in range(30):
        name=f'r{i}';flag=16 if i%2 else 0
        if gap:
            seq=ref[:40]+ref[41:];pos=1;cigar='40M1N87M'
        elif i<2:
            seq=ref[64:]+ref[:64];pos=65;cigar='64M64S';flag=0
        else:
            seq=ref;pos=1;cigar='128M'
        records.append(dict(qname=name,flag=flag,rname='p',position=pos,mapq=60,cigar=cigar,sequence=seq))
        raw=verify.reverse_complement(seq) if flag&16 else seq
        fastq.append(f'@{name}\n{raw}\n+\n'+('I'*len(raw))+'\n')
        if not gap and i<2:
            records.append(dict(qname=name,flag=2048,rname='p',position=1,mapq=60,cigar='64S64M',sequence=seq))
    semantics=verify.recompute_alignment_semantics(records,'p',ref)
    reference=tmp_path/'reference.fasta';reference.write_text('>p\n'+ref+'\n')
    bundle=tmp_path/'bundle';bundle.mkdir()
    reads=bundle/'source.fastq';reads.write_text(''.join(fastq))
    observed=bundle/'observed.fasta';observed.write_text('>observed\n'+ref+'\n')
    state={'state':'present','method':'samtools_consensus_bayesian','observed_sha256':sha(observed),
           'source_reads_path':reads.name,'source_reads_sha256':sha(reads),
           'source_kind':'read_derived_consensus_candidate',
           'source_read_provenance':{'binding_method':'qname_and_sequence_against_primary_bam'}}
    if comparison is not None:
        other=bundle/'read_consensus.fasta'
        altered=ref if comparison else ref[:50]+next(b for b in 'ACGT' if b!=ref[50])+ref[51:]
        other.write_text('>read\n'+altered+'\n')
        state['read_guided_comparison']={'path':other.name,'sha256':sha(other),'method':'samtools_bayesian_reference_guided'}
    observed_state=bundle/'observed_state.json';observed_state.write_text(json.dumps(state))
    support=tmp_path/'support.tsv'
    fields=['chrom','position_1based','reference_base','depth','forward_depth','reverse_depth',
            'a_count','c_count','g_count','t_count','n_count','insertion_count','deletion_count','consensus_base','major_allele_fraction']
    with support.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t');writer.writeheader()
        for pos,counts in semantics['support'].items():
            base= max('ACGTN',key=lambda b:counts[b]) if counts['depth'] else 'N'
            writer.writerow({'chrom':'p','position_1based':pos,'reference_base':ref[pos-1],
                **{k:counts[k] for k in ('depth','forward_depth','reverse_depth','insertion_count','deletion_count')},
                **{f'{b.lower()}_count':counts[b] for b in 'ACGTN'},
                'consensus_base':base,'major_allele_fraction':max(counts[b] for b in 'ACGTN')/counts['depth'] if counts['depth'] else 0})
    stats=tmp_path/'stats.tsv';stats.write_text('metric\tvalue\ntotal_reads\t30\nmapped_reads\t30\nunmapped_reads\t0\n')
    bam=tmp_path/'synthetic.bam';bam.write_bytes(b'SYNTHETIC TEST PLACEHOLDER NOT A BAM')
    bai=tmp_path/'synthetic.bam.bai';bai.write_bytes(b'SYNTHETIC INDEX PLACEHOLDER')
    call=tmp_path/'call.tsv';secondary=tmp_path/'secondary.tsv'
    callrows=[{'call_status':'split_supported' if contradictory else 'no_split','call_confidence':'high' if contradictory else 'low',
               'primary_position_mod_ref':'64','boundary_window_bp':'10'}]
    secondaryrows=[{'aligned_dimer_reads':'30','non_boundary_split_reads':'0'}]
    if missing_screen: callrows=[];secondaryrows=[]
    for path,rows,columns in [(call,callrows,['call_status','call_confidence','primary_position_mod_ref','boundary_window_bp']),
                               (secondary,secondaryrows,['aligned_dimer_reads','non_boundary_split_reads'])]:
        with path.open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=columns,delimiter='\t');writer.writeheader();writer.writerows(rows)
    samrows=['\t'.join([r['qname'],str(r['flag']),'p',str(r['position']),'60',r['cigar'],'*','0','0',r['sequence'],'I'*len(r['sequence'])]) for r in records]
    topology=derive_topology_evidence(reference_length=len(ref),sam_rows=samrows,
        breakpoint_rows=callrows,secondary_rows=secondaryrows,edge_window_bp=10)
    topology['provenance']={'reference_sha256':sha(reference),'alignment_bam_sha256':sha(bam),
                            'breakpoint_call_sha256':sha(call),'secondary_summary_sha256':sha(secondary)}
    if tamper: topology['contradictory_breakpoint_evidence']=not topology['contradictory_breakpoint_evidence']
    topfile=tmp_path/'topology.json';topfile.write_text(json.dumps(topology))
    base=json.loads((Path(__file__).resolve().parents[2]/'config/ngs/construct_verify_profiles.json').read_text())
    profile=dict(base['profiles']['plasmid_strict_v1'])
    if qualified:
        profile.update(calibration_status='calibrated',public_accuracy_validated=True,automatic_pass_eligible=True)
    profilefile=tmp_path/'test_only_profile.json';profilefile.write_text(json.dumps({'profiles':{'test_only':profile}}))
    args=verify.build_parser().parse_args([
        '--reference-fasta',str(reference),'--expected-reference-sha256',hashlib.sha256(ref.encode()).hexdigest(),
        '--observed-state',str(observed_state),'--observed-fasta',str(observed),'--per-base-support',str(support),
        '--alignment-bam',str(bam),'--alignment-index',str(bai),'--alignment-stats',str(stats),
        '--topology-evidence',str(topfile),'--breakpoint-call',str(call),'--secondary-summary',str(secondary),
        '--profile-config',str(profilefile),'--profile','test_only','--out-dir',str(tmp_path/'out')])
    valid={'status':'valid','validator':'mocked native IO; no scientific runtime claim','reason':None}
    monkeypatch.setattr(verify,'validate_alignment_artifacts',lambda *args:(valid,valid,'mock-samtools',[{'name':'mock','argv':['mock']}]))
    monkeypatch.setattr(verify,'read_alignment_records',lambda *args:(records,{'name':'mock','argv':['mock']}))
    result=verify.run_verification(args)
    assert json.loads((tmp_path/'out/qc_manifest.json').read_text())==result
    return result


def test_clean_input_passes_test_only_qualified_profile(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch)
    assert result['verdict']=='PASS'
    assert all(c['status']=='pass' for c in result['checks'].values())


def test_clean_input_remains_review_under_real_experimental_profile(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,qualified=False)
    assert result['verdict']=='REVIEW'
    assert result['reason_codes']==['UNCALIBRATED_PROFILE']


def test_one_uncovered_base_cannot_pass_even_old_coverage_tolerance(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,gap=True)
    assert result['verdict']=='REVIEW'
    assert result['checks']['coverage']['status']=='pass' # 127/128 > old 99% threshold
    assert result['checks']['sequence_identity']['status']=='review'
    assert 'OBSERVED_SEQUENCE_HAS_UNCOVERED_POSITIONS' in result['reason_codes']
    assert result['summary']['variant_analysis_status']=='not_assessed'
    assert result['inputs']['observed']['independent_from_expected'] is False


def test_header_only_structural_tables_do_not_pass(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,missing_screen=True)
    assert result['verdict']=='REVIEW' and result['checks']['topology']['status']=='review'
    assert result['checks']['sequence_identity']['status']=='pass'
    assert 'TOPOLOGY_EVIDENCE_UNAVAILABLE' in result['reason_codes']


def test_canonical_breakpoint_contradiction_fails(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,contradictory=True)
    assert result['verdict']=='FAIL'
    assert 'TOPOLOGY_CONTRADICTED' in result['reason_codes']


def test_tampered_breakpoint_boolean_cannot_authorize_verdict(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,contradictory=True,tamper=True)
    assert result['verdict']=='REVIEW'
    assert 'TOPOLOGY_PROVENANCE_INVALID' in result['reason_codes']


def test_consensus_disagreement_forces_review_even_when_assembly_matches(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,comparison=False)
    assert result['verdict']=='REVIEW'
    assert 'CONSENSUS_METHODS_DISAGREE' in result['reason_codes']


def test_same_read_consensus_agreement_is_not_independent_replication(tmp_path,monkeypatch):
    result=run_case(tmp_path,monkeypatch,comparison=True)
    assert result['verdict']=='PASS'
    assert result['checks']['sequence_identity']['metrics']['consensus_comparison']['independent_biological_replication'] is False
