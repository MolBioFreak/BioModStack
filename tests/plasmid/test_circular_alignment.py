import random
import pytest
import verify_construct as verify
from plasmid_circular import Budget, CircularAlignmentUnresolved, align_circular, _banded


def oracle(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1):
            nxt.append(min(row[j]+1,nxt[-1]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]


def test_exact_fast_path_has_correct_observed_rotation(monkeypatch):
    ref='ACGTCCTAGTGCA'
    for oriented in (ref,verify.reverse_complement(ref)):
        for offset in range(len(ref)):
            obs=oriented[offset:]+oriented[:offset]
            monkeypatch.setattr(verify,'_small_circular_alignment',lambda *_: pytest.fail('exact path performed edit search'))
            result=verify.best_circular_alignment(ref,obs)
            selected=obs if result['orientation']=='forward' else verify.reverse_complement(obs)
            r=result['rotation_offset']
            assert selected[r:]+selected[:r]==ref
            assert result['edit_cost']==0 and result['matches']==len(ref)


def test_band_matches_independent_linear_oracle():
    rng=random.Random(991)
    for _ in range(200):
        a=''.join(rng.choices('ACGT',k=rng.randint(3,20)))
        b=''.join(rng.choices('ACGT',k=rng.randint(3,20)))
        expected=oracle(a,b)
        cost,ops=_banded(a,b,max(len(a),len(b)),Budget(),trace=True)
        assert cost==expected
        assert sum(i2-i1 for t,i1,i2,j1,j2 in ops)==len(a)
        assert sum(j2-j1 for t,i1,i2,j1,j2 in ops)==len(b)


def test_seed_search_matches_exhaustive_rotation_oracle():
    rng=random.Random(318)
    for _ in range(60):
        ref=''.join(rng.choices('ACGT',k=16))
        obs=list(ref)
        for change in range(rng.randint(1,3)):
            pos=rng.randrange(len(obs));op=rng.choice(['replace','insert','delete'])
            if op=='replace': obs[pos]=rng.choice('ACGT')
            elif op=='insert': obs.insert(pos,rng.choice('ACGT'))
            else: obs.pop(pos)
        obs=''.join(obs);offset=rng.randrange(len(obs));obs=obs[offset:]+obs[:offset]
        if rng.randrange(2): obs=verify.reverse_complement(obs)
        expected=min(oracle(ref,x[i:]+x[:i]) for x in (obs,verify.reverse_complement(obs)) for i in range(len(x)))
        result=align_circular(ref,obs,Budget(max_edits=4))
        assert result['edit_cost']==expected
        # Exact legacy tie behavior is preserved, apart from the method label.
        legacy=verify._small_circular_alignment(ref,obs)
        assert result['opcodes']==legacy['opcodes']
        assert result['normalized_observed']==legacy['normalized_observed']


@pytest.mark.parametrize('operation',['snv','ins','del'])
def test_realistic_plasmid_with_rotation_and_edit(operation):
    rng=random.Random(22);ref=''.join(rng.choices('ACGT',k=5570))
    if operation=='snv': obs=ref[:3515]+next(b for b in 'ACGT' if b!=ref[3515])+ref[3516:]
    elif operation=='ins': obs=ref[:3515]+'G'+ref[3515:]
    else: obs=ref[:3515]+ref[3516:]
    obs=verify.reverse_complement(obs[1000:]+obs[:1000])
    result=verify.best_circular_alignment(ref,obs)
    assert result['edit_cost']==1
    assert sum(i2-i1 for t,i1,i2,j1,j2 in result['opcodes'])==len(ref)
    assert sum(j2-j1 for t,i1,i2,j1,j2 in result['opcodes'])==len(obs)


def test_budget_exhaustion_is_not_a_partial_alignment():
    ref='ACGT'*100
    with pytest.raises(CircularAlignmentUnresolved):
        align_circular(ref,ref[:50]+'G'+ref[51:],Budget(max_candidates=2))


def test_fragment_or_dimer_cannot_be_exact_whole_circle():
    ref='ACGTTGCA'*20
    with pytest.raises(CircularAlignmentUnresolved):
        verify.best_circular_alignment(ref,ref+ref)
    with pytest.raises(CircularAlignmentUnresolved):
        verify.best_circular_alignment(ref,ref[:50])
