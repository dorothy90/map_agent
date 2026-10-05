from collections import defaultdict
from copy import deepcopy


async def epoch_config(saver, run_id, epoch, *, child_id=None, recursion_limit=240):
    """Fork recovery state into an owner-specific checkpoint namespace.

    In-flight writes from a previous owner can only affect its old namespace.
    The checkpoint and pending writes are copied together before execution.
    """
    prefix = "harness:" + run_id + (":child:" + child_id if child_id else "")
    config = {"configurable": {"thread_id": f"{prefix}:epoch:{epoch}", "checkpoint_ns": ""}, "recursion_limit": recursion_limit}
    if await saver.aget_tuple(config):
        return config
    for previous_epoch in range(epoch - 1, 0, -1):
        previous = await saver.aget_tuple({"configurable": {"thread_id": f"{prefix}:epoch:{previous_epoch}"}})
        if previous is None:
            continue
        checkpoint = deepcopy(previous.checkpoint)
        copied = {"configurable": {**config["configurable"], "checkpoint_id": checkpoint["id"]}}
        writes = defaultdict(list)
        for task_id, channel, value in previous.pending_writes or []:
            writes[task_id].append((channel, value))
        for task_id, values in writes.items():
            await saver.aput_writes(copied, values, task_id)
        # Publish the checkpoint last. Interrupted copies without a checkpoint
        # cannot be selected as a future recovery source.
        await saver.aput(config, checkpoint, previous.metadata, checkpoint["channel_versions"])
        break
    return config
