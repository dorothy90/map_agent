import asyncio


class CeleryJobDispatcher:
    def __init__(self, celery):
        self.celery = celery

    async def dispatch(self, job_id: str, run_sequence: int) -> str:
        task_id = f"{job_id}:{run_sequence}"
        await asyncio.to_thread(
            self.celery.send_task,
            "yield_agent.run_job",
            args=[job_id, run_sequence],
            task_id=task_id,
            queue="analysis",
        )
        return task_id
