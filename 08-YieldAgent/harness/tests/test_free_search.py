def test_harness_search_does_not_call_paid_embeddings(monkeypatch):
    import asyncio
    import fail_history_tools
    from harness.tools.analysis_tools import search_history, SearchInput
    def unexpected(*args):
        raise AssertionError("Paid embedding API must not be called")
    class Client:
        def search(self, **kwargs):
            assert "hybrid" not in kwargs["body"]["query"]
            return {"hits": {"hits": []}}
    monkeypatch.setattr(fail_history_tools, "_get_embedding", unexpected)
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr("harness.tools.analysis_tools.run_blocking", direct)
    monkeypatch.setattr(fail_history_tools, "_get_opensearch_client", lambda: Client())
    result = asyncio.run(search_history(SearchInput(query="수율"), None))
    assert result.rows == []
