import asyncio
from types import SimpleNamespace
import pytest


def test_html_pages_and_ownership():
    from harness.tools.document_tools import ArtifactTextInput, read_artifact_text
    class Store:
        async def artifact(self, owner, session, ident):
            assert (owner, session) == ('owner', 'session')
            if ident != 'stored':
                raise KeyError(ident)
            return {'mime': 'text/html', 'result_id': 'result'}, b'<h1>Title</h1><script>secret</script><p>Body</p><table><tr><td>Value</td><td>42</td></tr></table>'
    ctx = SimpleNamespace(store=Store(), run={'principal_id':'owner','session_id':'session'})
    result = asyncio.run(read_artifact_text(ArtifactTextInput(artifact_id='stored', limit=8), ctx))
    row = result.rows[0]
    assert row['total_length'] > 8 and row['next_offset'] == 8
    assert result.source_result_ids == ['result']
    full = asyncio.run(read_artifact_text(ArtifactTextInput(artifact_id='stored'), ctx)).rows[0]['text']
    assert '42' in full and 'secret' not in full and 'Title\n' in full
    for ident in ['other-session', '/tmp/private', 'https://example.com']:
        with pytest.raises((KeyError, ValueError)):
            asyncio.run(read_artifact_text(ArtifactTextInput(artifact_id=ident), ctx))


def test_full_history_page(monkeypatch):
    import fail_history_tools
    from harness.tools import document_tools as mod
    monkeypatch.setattr(fail_history_tools, '_fetch_results_by_doc_ids', lambda ids, **kw: [{'doc_id':ids[0], 'content':'a'*250+'ANSWER'}])
    async def direct(fn,*a,**kw): return fn(*a,**kw)
    monkeypatch.setattr(mod, 'run_blocking', direct)
    result = asyncio.run(mod.get_history_document(mod.HistoryDocumentInput(doc_id='doc', offset=250), None))
    assert 'ANSWER' in result.rows[0]['text']


def test_fetch_full_content_is_opt_in_and_errors_propagate(monkeypatch):
    import fail_history_tools as mod
    class Client:
        def search(self,**kw):
            return {'hits':{'hits':[{'_source':{'doc_id':'doc','content':'a'*250+'ANSWER'}}]}}
    monkeypatch.setattr(mod,'_get_opensearch_client',lambda:Client())
    assert len(mod._fetch_results_by_doc_ids(['doc'])[0]['content'])==200
    assert mod._fetch_results_by_doc_ids(['doc'],full_content=True)[0]['content'].endswith('ANSWER')
    class Offline:
        def search(self,**kw): raise ConnectionError('offline')
    monkeypatch.setattr(mod,'_get_opensearch_client',lambda:Offline())
    with pytest.raises(ConnectionError): mod._fetch_results_by_doc_ids(['doc'],strict=True)


def test_mongo_artifact_pages_preserve_original_and_enforce_owner():
    import uuid
    from harness.store import HarnessStore
    from harness.tools.document_tools import ArtifactTextInput, read_artifact_text
    async def scenario():
        store=HarnessStore(database='harness_document_test_'+uuid.uuid4().hex)
        try:
            await store.setup()
            original='<h2>Report</h2><p>'+('a'*250)+'ANSWER</p>'
            obs=await store.put_result('owner','session',{'tool_name':'get_wads_report','run_id':'run','invocation_id':'inv'},[],[{'title':'original','mime':'text/html','data':original}])
            ident=obs.artifact_refs[0]['artifact_id']
            ctx=SimpleNamespace(store=store,run={'principal_id':'owner','session_id':'session'})
            result=await read_artifact_text(ArtifactTextInput(artifact_id=ident,offset=240),ctx)
            assert 'ANSWER' in result.rows[0]['text']
            assert (await store.artifact('owner','session',ident))[1].decode()==original
            for owner,session in [('other','session'),('owner','other')]:
                ctx.run={'principal_id':owner,'session_id':session}
                with pytest.raises(PermissionError):
                    await read_artifact_text(ArtifactTextInput(artifact_id=ident),ctx)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
