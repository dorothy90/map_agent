import asyncio
import uuid


def test_metadata_ack_loss_preserves_published_data(monkeypatch):
    from harness.store import HarnessStore
    from pymongo.errors import AutoReconnect

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            insert = store.results.insert_one
            async def acknowledged_on_server_only(document):
                await insert(document)
                raise AutoReconnect("lost insert acknowledgement")
            monkeypatch.setattr(store.results, "insert_one", acknowledged_on_server_only)
            obs = {"run_id": "r", "invocation_id": "i", "tool_name": "query"}
            try:
                await store.put_result("p", "s", obs, [{"n": 7}], [])
            except AutoReconnect:
                pass
            retry = await store.put_result("p", "s", obs, [{"n": 7}], [])
            assert await store.rows("p", "s", retry.result_id) == [{"n": 7}]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
