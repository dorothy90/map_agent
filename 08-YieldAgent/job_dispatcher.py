from typing import Protocol


class JobDispatcher(Protocol):
    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        raise NotImplementedError


class UnavailableJobDispatcher:
    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        raise RuntimeError("job dispatcher is not configured")
