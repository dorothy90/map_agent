from collections.abc import Iterable, Mapping

from redis.asyncio import Redis


GLOBAL_ACTIVE_KEY = "jobs:active:global"
USER_ACTIVE_KEY_PREFIX = "jobs:active:user:"

_ACQUIRE_SCRIPT = """
local global_key = KEYS[1]
local user_key = KEYS[2]
local job_id = ARGV[1]
local user_limit = tonumber(ARGV[2])
local global_limit = tonumber(ARGV[3])

local in_global = redis.call('SISMEMBER', global_key, job_id)
local in_user = redis.call('SISMEMBER', user_key, job_id)

if in_global == 1 and in_user == 1 then
    return 'OK'
end

if in_user == 0 and redis.call('SCARD', user_key) >= user_limit then
    return 'USER_LIMIT'
end

if in_global == 0 and redis.call('SCARD', global_key) >= global_limit then
    return 'GLOBAL_LIMIT'
end

redis.call('SADD', global_key, job_id)
redis.call('SADD', user_key, job_id)
return 'OK'
"""

_RELEASE_SCRIPT = """
redis.call('SREM', KEYS[1], ARGV[1])
redis.call('SREM', KEYS[2], ARGV[1])
return 'OK'
"""


class UserLimitExceeded(Exception):
    pass


class GlobalLimitExceeded(Exception):
    pass


class AdmissionController:
    def __init__(self, redis: Redis, user_limit: int, global_limit: int):
        self.redis = redis
        self.user_limit = user_limit
        self.global_limit = global_limit

    async def acquire(self, owner_hash: str, job_id: str) -> None:
        result = await self.redis.eval(
            _ACQUIRE_SCRIPT,
            2,
            GLOBAL_ACTIVE_KEY,
            self._user_key(owner_hash),
            job_id,
            self.user_limit,
            self.global_limit,
        )
        if result in (b"USER_LIMIT", "USER_LIMIT"):
            raise UserLimitExceeded
        if result in (b"GLOBAL_LIMIT", "GLOBAL_LIMIT"):
            raise GlobalLimitExceeded

    async def release(self, owner_hash: str, job_id: str) -> None:
        await self.redis.eval(
            _RELEASE_SCRIPT,
            2,
            GLOBAL_ACTIVE_KEY,
            self._user_key(owner_hash),
            job_id,
        )

    async def reconcile(self, job_counts: Mapping[str, Iterable[str]]) -> None:
        user_keys = [
            key async for key in self.redis.scan_iter(match=f"{USER_ACTIVE_KEY_PREFIX}*")
        ]
        pipeline = self.redis.pipeline(transaction=True)
        if user_keys:
            pipeline.delete(*user_keys)
        pipeline.delete(GLOBAL_ACTIVE_KEY)

        all_job_ids: set[str] = set()
        for owner_hash, job_ids in job_counts.items():
            owner_job_ids = set(job_ids)
            if not owner_job_ids:
                continue
            pipeline.sadd(self._user_key(owner_hash), *owner_job_ids)
            all_job_ids.update(owner_job_ids)

        if all_job_ids:
            pipeline.sadd(GLOBAL_ACTIVE_KEY, *all_job_ids)
        await pipeline.execute()

    async def global_count(self) -> int:
        return await self.redis.scard(GLOBAL_ACTIVE_KEY)

    @staticmethod
    def _user_key(owner_hash: str) -> str:
        return f"{USER_ACTIVE_KEY_PREFIX}{owner_hash}"
