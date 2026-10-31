import pytest
from harness.domain_metrics import wafer_pass_metric

def test_pass_only_question_mark():
    m={'MAP':['0,0,?,A','0,1,?,?','0,2,A,D','0,3,7,T']}
    assert wafer_pass_metric(m)['pass_count']==2
    assert wafer_pass_metric(m,bin_type='right_bin')['pass_count']==1
    from map_agent import _parse_wafer_for_cummap
    assert _parse_wafer_for_cummap((m,'left_bin',None))[2]==[1,1,0,0]

def test_fail_bin_validation():
    from harness.tools.defect_tools import DefectYieldInput
    kw=dict(lotcd='4SA',start_date='2026-01-01',end_date='2026-02-01',oper='PT1H')
    assert DefectYieldInput(**kw,target_bins=['A']).target_bins==['A']
    with pytest.raises(ValueError): DefectYieldInput(**kw,target_bins=['?'])

def test_legend():
    from common import BIN_CATEGORY_MAP
    assert BIN_CATEGORY_MAP['?']=='PASS'
    assert BIN_CATEGORY_MAP.get('A')!='PASS'
