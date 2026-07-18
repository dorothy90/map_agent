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

_RECONCILE_SCRIPT = """
local user_key_prefix = ARGV[1]
local user_keys = redis.call('KEYS', user_key_prefix .. '*')

if #user_keys > 0 then
    redis.call('DEL', unpack(user_keys))
end
redis.call('DEL', KEYS[1])

local argument_index = 2
local owner_count = tonumber(ARGV[argument_index])
argument_index = argument_index + 1

for _ = 1, owner_count do
    local owner_hash = ARGV[argument_index]
    argument_index = argument_index + 1
    local job_count = tonumber(ARGV[argument_index])
    argument_index = argument_index + 1
    local user_key = user_key_prefix .. owner_hash

    for _ = 1, job_count do
        local job_id = ARGV[argument_index]
        argument_index = argument_index + 1
        redis.call('SADD', user_key, job_id)
        redis.call('SADD', KEYS[1], job_id)
    end
end

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
        arguments: list[str | int] = [USER_ACTIVE_KEY_PREFIX, len(job_counts)]
        for owner_hash, job_ids in job_counts.items():
            owner_job_ids = set(job_ids)
            arguments.extend((owner_hash, len(owner_job_ids), *owner_job_ids))
        await self.redis.eval(
            _RECONCILE_SCRIPT,
            1,
            GLOBAL_ACTIVE_KEY,
            *arguments,
        )

    async def global_count(self) -> int:
        return await self.redis.scard(GLOBAL_ACTIVE_KEY)

    @staticmethod
    def _user_key(owner_hash: str) -> str:
        return f"{USER_ACTIVE_KEY_PREFIX}{owner_hash}"
