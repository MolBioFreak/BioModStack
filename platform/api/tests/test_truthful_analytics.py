"""Typed scalar arithmetic fixtures; persisted-route proof is in the companion file."""
from copy import deepcopy
import pytest
from database import Design, Job
from routers import designs
from routers.analytics import DesignMetricPoint
from services import scientific_analytics as scientific


def record(key, value, *, state=None):
    return dict(metric_key=key, value=value, state=state or ('unavailable' if value is None else 'ok'),
        reason_code='not_reported' if value is None else None,
        unit='fraction' if key=='complex_plddt' else 'dimensionless', scope='complex' if key=='complex_plddt' else 'overall',
        direction='higher_is_better', producer_version='fixture-producer-v1', derivation_version='fixture-scalar-v1',
        source=dict(artifact_sha256='a'*64, candidate_id='producer-candidate', document_id='native-document'))


def projected(id, x, y):
    return (id,scientific.projection(Design(id=id,job_id='j',name=id),
        records=[record('complex_plddt',x),record('ptm',y)]))


def test_distributions_and_pair_denominators_are_candidate_bound():
    result=scientific.summarize([projected('a',0,0.2),projected('b',0.8,None),projected('c',None,0.4)])
    assert result['metrics']['complex_plddt']['observed_count']==2
    assert result['metrics']['complex_plddt']['unavailable_count']==1
    assert result['metrics']['complex_plddt']['statistics']['avg']==0.4
    pair=result['pairs']['complex_plddt_vs_ptm']
    assert pair['points']==[{'id':'a','x':0,'y':0.2}]
    assert pair['excluded_ids']==['b','c']
    assert pair['pair_count']==1
    assert pair['correlation'].state=='unavailable'
    assert scientific.summarize([])=={'metrics':{},'pairs':{}}


@pytest.mark.parametrize('field',['scope','unit','producer_version','derivation_version'])
def test_incompatible_descriptor_never_enters_distribution(field):
    a=projected('a',0.1,0.2);b=projected('b',0.3,0.4)
    setattr(b[1]['metric_descriptors']['ptm'],field,'different')
    with pytest.raises(ValueError,match='incompatible'):
        scientific.summarize([a,b])


def test_empty_and_invalid_observed_sets_keep_reasons():
    r=record('ptm',None,state='invalid')
    p=scientific.projection(Design(id='a',job_id='j',name='a'),records=[r])
    result=scientific.summarize([('a',p)])
    assert result['metrics']['ptm']['statistics'] is None
    assert result['metrics']['ptm']['invalid_count']==1
    assert result['metrics']['ptm']['reason_code']=='no_observed_values'


@pytest.mark.parametrize('model',[designs.PlotlyMetricPoint, DesignMetricPoint])
def test_scientific_response_point_rejects_unknown_fields(model):
    from pydantic import ValidationError
    _, p=projected('a',0.1,0.2)
    with pytest.raises(ValidationError):
        model(id='a',name='a',**p,forged_authority=True)


def test_neutral_descriptor_preserves_no_direction():
    r=record('ptm',0.2);r['direction']='neutral'
    p=scientific.projection(Design(id='a',job_id='j',name='a'),records=[r])
    assert p['metric_descriptors']['ptm'].direction=='none'


@pytest.mark.parametrize('bad',[True,'0.4',float('nan'),float('inf')])
def test_canonical_values_never_coerce(bad):
    with pytest.raises(ValueError):
        scientific.projection(Design(id='a',job_id='j',name='a'),records=[record('ptm',bad)])


def test_unverified_marked_plotly_does_not_read_legacy_or_nested_json():
    d=Design(id='a',job_id='j',name='A',plddt_overall=0,ptm=True,
        confidence_metrics={'heterogeneous':{'seed':7,'score':99,'passed':True}})
    assert designs._build_plotly_metrics(d,job=Job(id='j',provenance={'core_protein_scientific_contract':1}))=={}
    assert designs._build_plotly_metrics(Design(id='b',job_id='legacy',name='B',plddt_overall=0))['plddt_overall']==0
