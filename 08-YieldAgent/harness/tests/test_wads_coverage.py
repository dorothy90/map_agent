import asyncio
import pandas as pd


def test_optional_filters_and_distinct_reports(monkeypatch):
    from harness.tools import wads_tools as mod
    import wads_tools
    rows = [{'lotcd':'4SS','category':'PT1H','parameter':'X','end_tm':tm,'groupkey':wf} for tm,wf in [('2026-08-01 01:00:00','LOT.01'),('2026-08-01 01:00:00','LOT.02'),('2026-08-01 02:00:00',None)]]
    monkeypatch.setattr(wads_tools,'_query_wads_data',lambda **kw: pd.DataFrame(rows))
    async def direct(fn,*a,**kw): return fn(*a,**kw)
    monkeypatch.setattr(mod,'run_blocking',direct)
    result=asyncio.run(mod.query_wads(mod.WadsInput(),None))
    tables={t.table_id:t for t in result.tables}
    assert len(tables['reports'].rows)==2
    assert len(tables['report_wafers'].rows)==2
    assert tables['join_coverage'].rows[0]['missing_groupkey_rows']==1
    assert len({r['report_id'] for r in tables['reports'].rows})==2
    assert tables['report_wafers'].rows[0]['lot_id']=='LOT'
    assert not tables['report_wafers'].complete


def test_limit_marks_all_population_tables_partial(monkeypatch):
    from harness.tools import wads_tools as mod
    import wads_tools
    frame=pd.DataFrame([dict(lotcd='4SS', category='PT1H',parameter='X',end_tm=f'2026-08-01 0{i}:00:00',groupkey='L.01') for i in range(3)])
    monkeypatch.setattr(wads_tools,'_query_wads_data',lambda **kw:frame)
    async def direct(fn,*a,**kw): return fn(*a,**kw)
    monkeypatch.setattr(mod,'run_blocking',direct)
    result=asyncio.run(mod.inspect_wads_coverage(mod.WadsInput(limit=2),None))
    assert all(not table.complete for table in result.tables)
    assert result.tables[-1].rows[0]['latest_issuance']=='2026-08-01 01:00:00'


def test_exact_report_uses_owned_table_and_issuance(monkeypatch):
    from types import SimpleNamespace
    from harness.tools import artifact_tools as mod
    from harness.tools.wads_tools import WadsReportInput, report_id
    import wads_tools
    row=dict(lotcd='4SS',category='PT1H',parameter='X',end_tm='2026-08-01 02:00:00')
    row['report_id']=report_id(row)
    calls=[]
    class Store:
        async def rows(self,owner,session,result,table_id):
            calls.append((owner,session,result,table_id))
            return [row]
    def fetch(**kw):
        assert kw['exact_key']==('4SS','PT1H','X','2026-08-01 02:00:00')
        return pd.DataFrame([dict(row,html='<p>Original</p>')])
    monkeypatch.setattr(wads_tools,'_query_wads_data',fetch)
    async def direct(fn,*a,**kw): return fn(*a,**kw)
    monkeypatch.setattr(mod,'run_blocking',direct)
    ctx=SimpleNamespace(store=Store(),run={'principal_id':'owner','session_id':'session'})
    result=asyncio.run(mod.get_wads_report(WadsReportInput(report_id=row['report_id'],source_result_id='source'),ctx))
    assert calls==[('owner','session','source','reports')]
    assert result.artifacts[0]['data']=='<p>Original</p>'
    assert result.source_result_ids==['source']
