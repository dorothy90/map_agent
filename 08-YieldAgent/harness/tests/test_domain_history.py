import asyncio
from types import SimpleNamespace
import pytest
from harness.tools import lot_tools, analysis_tools
from harness.tools.registry import ToolRegistry


def test_lot_keeps_five_schemas_and_existing_risk_html(monkeypatch):
    import lot_history_tools
    groups = {k: [] for k in lot_history_tools._QUERIES}
    groups['fdc_alarm']=[{'alarm_level_cd':'HALT','lot_id':'L'}]
    async def fake(fn,*args,**kwargs): return {'L':groups}
    monkeypatch.setattr(lot_tools,'run_blocking',fake)
    result=asyncio.run(lot_tools.query_lot_history(lot_tools.LotInput(lot_ids=['L']),None))
    assert [t.table_id for t in result.tables] == list(groups)
    assert result.tables[0].rows == groups['fdc_alarm']
    assert result.scope['risk_by_lot']=={'L':'red'}
    assert 'HIGH RISK' in result.artifacts[0]['data']


def test_search_exposes_explicit_modes_and_preserves_ids(monkeypatch):
    modes=analysis_tools.SearchInput.model_json_schema()['properties'].get('mode',{}).get('enum',[])
    assert modes == ['bm25','hybrid','wiki']
    calls=[]
    async def fake(fn,*a,**kw): calls.append(kw); return [{'doc_id':'D1','content':'snippet'}]
    monkeypatch.setattr(analysis_tools,'run_blocking',fake)
    result=asyncio.run(analysis_tools.search_history(analysis_tools.SearchInput(query='q',mode='bm25'),None))
    assert result.rows[0]['doc_id']=='D1'
    assert calls[0]['use_embeddings'] is False
    assert result.scope['retrieval']=='bm25'


def test_mining_unconfigured_is_unavailable_not_empty_success(monkeypatch):
    monkeypatch.setenv('HARNESS_MINING_API_URL','  ')
    registry=ToolRegistry(); analysis_tools.register(registry)
    assert registry.tools['analyze_mining'].available()['available'] is False
    with pytest.raises(ValueError,match='configured'):
        asyncio.run(analysis_tools.mining(analysis_tools.MiningInput(lotcd='A',fail_type='f',group_good=['G'],group_bad=['B']),SimpleNamespace(run={'principal_id':'p'})))


def test_mining_preserves_service_columns_and_no_invented_gini(monkeypatch):
    monkeypatch.setenv('HARNESS_MINING_API_URL','https://mining.example/api')
    rows=[{'operation':'ETCH','GINI':.27,'service_specific':'kept'}]
    class Client:
        def __init__(self,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def post(self,*a,**kw): return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'rows':rows})
    monkeypatch.setattr(analysis_tools.httpx,'AsyncClient',Client)
    result=asyncio.run(analysis_tools.mining(analysis_tools.MiningInput(lotcd='A',fail_type='f',group_good=['G'],group_bad=['B']),SimpleNamespace(run={'principal_id':'p'})))
    assert result.rows == rows


def test_wiki_preserves_full_body_and_document_ids(monkeypatch):
    import wiki_store
    body='body '*100 + 'TAIL'
    monkeypatch.setattr(wiki_store,'lookup',lambda *a,**kw: {'concepts':[{'id':'concept:A'}], 'recent_episodes':[{'id':'episode:E','doc_ids':['D']}]})
    monkeypatch.setattr(wiki_store,'read_node',lambda node: {'body':body,'frontmatter':{'id':node}})
    rows=analysis_tools._search_wiki(analysis_tools.SearchInput(query='q',mode='wiki'))
    assert rows[0]['content']==body
    assert rows[1]['doc_ids']==['D']


def test_hybrid_rejects_incompatible_dimension_before_embedding(monkeypatch):
    import fail_history_tools as fh
    monkeypatch.setattr(fh,'_EMBEDDING_API_KEY','configured-fixture')
    monkeypatch.setattr(fh,'_EMBEDDING_DIM',4096)
    monkeypatch.setattr(fh,'_get_opensearch_client',lambda:SimpleNamespace(indices=SimpleNamespace(get_mapping=lambda **kw:{'index':{'mappings':{'properties':{'embedding':{'dimension':3}}}}})))
    with pytest.raises(ValueError,match='dimension'):
        analysis_tools._check_hybrid_index()


def test_relation_and_groups_preserve_existing_sets(monkeypatch):
    import relation_tree_agent, wt_resp_agent
    monkeypatch.setattr(relation_tree_agent,'_query_main_opers',lambda *a:['ETCH','CMP'])
    monkeypatch.setattr(wt_resp_agent,'_query_good_bad',lambda *a:(['G1','G2'],['B1']))
    async def direct(fn,*a,**kw): return fn(*a,**kw)
    monkeypatch.setattr(analysis_tools,'run_blocking',direct)
    args=analysis_tools.RelationInput(lotcd='A',fail_type='f')
    relation=asyncio.run(analysis_tools.query_relation(args,None))
    groups=asyncio.run(analysis_tools.query_groups(args,None))
    assert {r['main_oper'] for r in relation.rows}=={'ETCH','CMP'}
    assert {(r['group'],r['lot_id']) for r in groups.rows}=={('good','G1'),('good','G2'),('bad','B1')}
