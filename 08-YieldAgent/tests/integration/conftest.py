import os
import uuid

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(
        os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27028"),
        tz_aware=True,
    )
    database = client[f"yield_agent_test_{uuid.uuid4().hex}"]
    await client.admin.command("ping")
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()
