import asyncio
import uuid
import time
import pytest


def test_new_runtime_pins_skills_and_rejects_active_old_checkpoint():
    from harness.store import HarnessStore, Conflict
    from harness.control import RunController
    from harness.config import Settings
    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        controller = RunController(store, Settings())
        try:
            await store.setup()
            run = await store.start_run('p', 's', 'r', 'fixture')
            assert run['runtime_version'] == 'harness/v2'
            assert run['contract_version'] == 'harness-observation/v2'
            assert run['skill_versions'] and run['skill_snapshot']
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'runtime_version': 'harness/v1'}})
            with pytest.raises(Conflict, match='version'):
                await controller.launch(run['run_id'])
            assert not controller.tasks
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_fixed_mcp_session_only_replaces_expired_owner():
    from harness.mcp_server import acquire_session
    from harness.store import HarnessStore, Conflict, LeaseLost
    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        try:
            await store.setup()
            first = await acquire_session(store, 'p', 'fixed', 'first')
            with pytest.raises(Conflict):
                await acquire_session(store, 'p', 'fixed', 'second')
            await store.runs.update_one({'_id': first['run_id']}, {'$set': {'lease_until': time.time() - 1}})
            second = await acquire_session(store, 'p', 'fixed', 'second')
            assert second['run_id'] != first['run_id']
            with pytest.raises(LeaseLost):
                await store.fence(first['run_id'], first['epoch'])
            assert (await store.get_run('p', first['run_id']))['stop_reason'] == 'mcp_connection_expired'
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
