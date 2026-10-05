from __future__ import annotations

import hashlib
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .types import ResultTable, TableRef, ToolObservation, now


class Conflict(RuntimeError):
    pass


class LeaseLost(Conflict):
    pass


class BudgetExceeded(Conflict):
    pass


class HarnessStore:
    def __init__(self, uri="mongodb://localhost:27017", database="yield_agent"):
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        self.db = self.client[database]
        self.blobs = AsyncIOMotorGridFSBucket(self.db, bucket_name="harness_blobs")
        self.runs = self.db.harness_runs
        self.results = self.db.harness_results
        self.calls = self.db.harness_invocations

    async def setup(self):
        await self.db.harness_sessions.create_index([("principal_id", 1), ("session_id", 1)], unique=True)
        await self.runs.create_index([("principal_id", 1), ("session_id", 1), ("request_id", 1)], unique=True)
        await self.runs.create_index([("principal_id", 1), ("session_id", 1)], unique=True, partialFilterExpression={"active": True})
        await self.calls.create_index([("run_id", 1), ("invocation_id", 1)], unique=True)
        await self.results.create_index("result_id", unique=True)

    @asynccontextmanager
    async def session_lock(self, principal_id, session_id):
        key = {"principal_id": principal_id, "session_id": session_id}
        try:
            await self.db.harness_sessions.update_one(key, {"$setOnInsert": key}, upsert=True)
        except DuplicateKeyError:
            pass
        owner = uuid.uuid4().hex
        locked = await self.db.harness_sessions.find_one_and_update({**key, "$or": [{"lock_until": {"$exists": False}}, {"lock_until": {"$lt": time.time()}}]},
            {"$set": {"lock_owner": owner, "lock_until": time.time() + 60}})
        if not locked:
            raise Conflict("Session update in progress; retry this request")
        try:
            yield
        finally:
            await self.db.harness_sessions.update_one({**key, "lock_owner": owner}, {"$unset": {"lock_owner": "", "lock_until": ""}})

    async def put_result(self, principal_id, session_id, observation, rows, artifacts, *, tables=None):
        publication = asyncio.create_task(self._put_result(principal_id, session_id, observation, rows, artifacts, tables=tables))
        try:
            return await asyncio.shield(publication)
        except asyncio.CancelledError:
            # Drain publication so a cancelled DB await cannot leave metadata
            # pointing at compensated/deleted GridFS blobs.
            await asyncio.gather(publication, return_exceptions=True)
            raise

    async def _put_result(self, principal_id, session_id, observation, rows, artifacts, *, tables=None):
        if rows and tables:
            raise ValueError("rows and tables cannot both be supplied")
        if tables is None or not tables:
            complete = observation.get("status", "success") in ("success", "empty")
            tables = [ResultTable(table_id="default", title=observation.get("tool_name", "Result"),
                rows=rows, complete=complete, missing_reason=None if complete else "Source result is partial or unsuccessful")]
        tables = [ResultTable.model_validate(json.loads(json.dumps(
            table.model_dump() if isinstance(table, ResultTable) else table,
            ensure_ascii=False, default=str, allow_nan=False))) for table in tables]
        if len({table.table_id for table in tables}) != len(tables):
            raise ValueError("Duplicate table_id")
        result_id = str(uuid.uuid5(uuid.NAMESPACE_URL, observation["run_id"] + ":" + observation["invocation_id"]))
        old = await self.results.find_one({"result_id": result_id})
        if old:
            if old["principal_id"] != principal_id or old["session_id"] != session_id:
                raise PermissionError("Result unavailable")
            return ToolObservation.model_validate(old["observation"])
        payload = json.dumps([t.model_dump() for t in tables], ensure_ascii=False, allow_nan=False).encode()
        blob_ids = []
        metadata_attempted = False
        try:
            table_refs = []
            for table in tables:
                data_id = await self.blobs.upload_from_stream(result_id + ":" + table.table_id,
                    json.dumps(table.rows, ensure_ascii=False, allow_nan=False).encode())
                blob_ids.append(data_id)
                preview, preview_bytes = [], 0
                for row in table.rows[:50]:
                    row_bytes = len(json.dumps(row, ensure_ascii=False).encode())
                    if preview_bytes + row_bytes > 32000:
                        break
                    preview.append(row)
                    preview_bytes += row_bytes
                table_refs.append(TableRef(table_id=table.table_id, title=table.title,
                    columns=list(dict.fromkeys([*table.columns, *(k for row in table.rows for k in row)])),
                    total_rows=len(table.rows), preview_rows=preview, units=table.units,
                    complete=table.complete, missing_reason=table.missing_reason, data_ref=str(data_id)))
            refs = []
            for artifact in artifacts:
                content = artifact["data"]
                if isinstance(content, str):
                    content = content.encode()
                blob = await self.blobs.upload_from_stream(artifact.get("title", "artifact"), content)
                blob_ids.append(blob)
                refs.append({"artifact_id": str(blob), "title": artifact.get("title", ""), "mime": artifact.get("mime", "text/html"), "result_id": result_id})
            single = table_refs[0] if len(table_refs) == 1 else None
            obs = ToolObservation(**{
                **observation, "schema_version": "harness-observation/v2",
                "principal_id": principal_id, "session_id": session_id,
                "result_id": result_id, "tables": table_refs,
                "data_ref": single.data_ref if single else "",
                "preview_rows": single.preview_rows if single else [],
                "total_rows": sum(t.total_rows for t in table_refs),
                "truncated": any(t.total_rows > len(t.preview_rows) for t in table_refs),
                "columns": single.columns if single else [], "artifact_refs": refs,
            })
            metadata_attempted = True
            await self.results.insert_one({"result_id": result_id, "principal_id": principal_id, "session_id": session_id,
                "checksum": hashlib.sha256(payload).hexdigest(), "observation": obs.model_dump(mode="json")})
            return obs
        except BaseException:
            if metadata_attempted:
                # A lost acknowledgement does not mean the insert failed. Keep
                # blobs on an uncertain outcome; deleting them can corrupt a
                # committed result. A duplicate publication may discard only
                # the blobs positively known not to belong to the winner.
                saved = await self.results.find_one({"result_id": result_id})
                if saved:
                    winner = ToolObservation.model_validate(saved["observation"])
                    referenced = {winner.data_ref, *(t.data_ref for t in winner.tables), *(a["artifact_id"] for a in winner.artifact_refs)}
                    for blob in blob_ids:
                        if str(blob) not in referenced:
                            await self.blobs.delete(blob)
                    return winner
                raise
            for blob in blob_ids:
                await self.blobs.delete(blob)
            raise

    async def observation(self, principal_id, session_id, result_id):
        record = await self.results.find_one({"result_id": result_id, "principal_id": principal_id, "session_id": session_id})
        if not record:
            raise PermissionError("Result unavailable")
        return ToolObservation.model_validate(record["observation"])

    @staticmethod
    def _select_table(obs, table_id):
        if table_id is None and len(obs.tables) == 1:
            return obs.tables[0]
        for table in obs.tables:
            if table.table_id == table_id:
                return table
        raise ValueError("Select table_id from: " + ", ".join(t.table_id for t in obs.tables))

    async def rows(self, principal_id, session_id, result_id, table_id=None):
        obs = await self.observation(principal_id, session_id, result_id)
        table = self._select_table(obs, table_id)
        blob = await self.blobs.open_download_stream(ObjectId(table.data_ref))
        return json.loads(await blob.read())

    async def read_result(self, principal_id, session_id, result_id, *, table_id=None, offset=0, limit=50, columns=None):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid page")
        obs = await self.observation(principal_id, session_id, result_id)
        table = self._select_table(obs, table_id)
        rows = await self.rows(principal_id, session_id, result_id, table_id=table.table_id)
        page = rows[offset:offset + limit]
        if columns is not None:
            if not set(columns).issubset(table.columns):
                raise ValueError("Unknown column")
            page = [{k: row.get(k) for k in columns} for row in page]
        return {"result_id": result_id, "table_id": table.table_id, "title": table.title,
                "columns": table.columns if columns is None else columns, "units": table.units,
                "rows": page, "offset": offset, "total_rows": len(rows),
                "complete": table.complete, "missing_reason": table.missing_reason,
                "truncated": offset > 0 or offset + limit < len(rows), "scope": obs.scope, "provenance": obs.provenance}

    async def artifact(self, principal_id, session_id, artifact_id):
        record = await self.results.find_one({"principal_id": principal_id, "session_id": session_id,
            "observation.artifact_refs.artifact_id": artifact_id})
        if not record:
            raise PermissionError("Artifact unavailable")
        ref = next(a for a in record["observation"]["artifact_refs"] if a["artifact_id"] == artifact_id)
        blob = await self.blobs.open_download_stream(ObjectId(artifact_id))
        return ref, await blob.read()

    async def start_run(self, principal_id, session_id, request_id, query, *, execution_kind="harness", ready=True):
        key = {"principal_id": principal_id, "session_id": session_id, "request_id": request_id}
        old = await self.runs.find_one(key)
        if old:
            if old["query"] != query:
                raise Conflict("Request ID already used")
            return old
        run_id = str(uuid.uuid4())
        from .skills import SkillCatalog
        catalog = SkillCatalog()
        record = {**key, "_id": run_id, "run_id": run_id, "query": query, "runtime_version": "harness/v2",
            "contract_version": "harness-observation/v2", "skill_snapshot": catalog.snapshot,
            "skill_versions": {item["name"]: item["version"] for item in catalog.list()},
            "execution_kind": execution_kind, "ready": ready,
            "status": "created", "active": True, "epoch": 0, "lease_until": 0.0, "created_at": now(),
            "events": [], "goal_revision": 1, "usage": {"tools": 0, "models": 0, "tokens": 0, "active_seconds": 0.0}}
        try:
            await self.runs.insert_one(record)
        except DuplicateKeyError:
            old = await self.runs.find_one(key)
            if old and old["query"] == query:
                return old
            raise Conflict("Session has an active run") from None
        return record

    async def get_run(self, principal_id, run_id):
        run = await self.runs.find_one({"_id": run_id, "principal_id": principal_id})
        if not run:
            raise PermissionError("Run unavailable")
        return run

    async def acquire(self, run_id, owner):
        return await self.runs.find_one_and_update(
            {"_id": run_id, "active": True, "lease_until": {"$lt": time.time()}, "status": {"$in": ["created", "running"]}},
            {"$set": {"owner": owner, "lease_until": time.time() + 30, "status": "running"}, "$inc": {"epoch": 1}},
            return_document=ReturnDocument.AFTER)

    async def fence(self, run_id, epoch):
        run = await self.runs.find_one({"_id": run_id, "epoch": epoch, "active": True, "status": "running", "lease_until": {"$gt": time.time()}})
        if not run:
            raise LeaseLost("Run cancelled or lease lost")
        return run

    async def renew(self, run_id, epoch, elapsed):
        return await self.runs.update_one({"_id": run_id, "epoch": epoch, "status": "running", "active": True},
            {"$set": {"lease_until": time.time() + 30}, "$inc": {"usage.active_seconds": elapsed}})

    async def reserve(self, run_id, epoch, kind, limit, amount=1):
        field = "usage." + kind
        result = await self.runs.update_one({"_id": run_id, "epoch": epoch, "status": "running", "active": True, "lease_until": {"$gt": time.time()}, "$expr": {"$lte": [{"$ifNull": ["$" + field, 0]}, limit - amount]}}, {"$inc": {field: amount}})
        if not result.modified_count:
            await self.fence(run_id, epoch)
            raise BudgetExceeded(kind + "_limit")

    async def reserve_tool_call(self, run_id, epoch, *, invocation_id, tool_limit, child_kind=None):
        # The marker and every counter live in one document: a crash between
        # claiming the invocation and charging it is safe to replay in either order.
        marker = hashlib.sha256(invocation_id.encode()).hexdigest()
        field = "tool_charges." + marker
        limits = {"tools": tool_limit}
        if child_kind:
            limits[child_kind] = 6
        result = await self.runs.update_one({"_id": run_id, "epoch": epoch,
            "status": "running", "active": True, "lease_until": {"$gt": time.time()},
            field: {"$exists": False}, "$expr": {"$and": [
                {"$lt": [{"$ifNull": ["$usage." + kind, 0]}, limit]} for kind, limit in limits.items()]}},
            {"$set": {field: True}, "$inc": {"usage." + kind: 1 for kind in limits}})
        if not result.modified_count:
            run = await self.fence(run_id, epoch)
            if run.get("tool_charges", {}).get(marker):
                return
            for kind, limit in limits.items():
                if run["usage"].get(kind, 0) >= limit:
                    raise BudgetExceeded(kind + "_limit")
            raise BudgetExceeded("tools_limit")

    async def reserve_model_call(self, run_id, epoch, *, token_limit, model_limit,
                                 tokens, child_kind=None):
        limits = {"models": (model_limit, 1), "tokens": (token_limit, tokens)}
        if child_kind:
            limits[child_kind] = (8, 1)
        conditions = [{"$lte": [{"$ifNull": ["$usage." + kind, 0]}, limit - amount]}
            for kind, (limit, amount) in limits.items()]
        result = await self.runs.update_one({"_id": run_id, "epoch": epoch,
            "status": "running", "active": True, "lease_until": {"$gt": time.time()},
            "$expr": {"$and": conditions}},
            {"$inc": {"usage." + kind: amount for kind, (_, amount) in limits.items()}})
        if not result.modified_count:
            run = await self.fence(run_id, epoch)
            for kind, (limit, amount) in limits.items():
                value = run["usage"]
                for part in kind.split("."):
                    value = value.get(part, 0) if isinstance(value, dict) else 0
                if value + amount > limit:
                    raise BudgetExceeded(kind + "_limit")
            # Another completed request may have reconciled usage since admission.
            raise BudgetExceeded("tokens_limit")

    async def event(self, run_id, kind, payload, *, event_key=None, epoch=None):
        # Embedded bounded run events allocate sequence and payload in one atomic write.
        query = {"_id": run_id, "status": "running", "active": True}
        if event_key:
            query["events.event_key"] = {"$ne": event_key}
        if epoch is not None:
            query["epoch"] = epoch
        return await self.runs.find_one_and_update(query, [{"$set": {"events": {"$concatArrays": ["$events", [{
            "run_id": {"$literal": run_id}, "type": {"$literal": kind}, "payload": {"$literal": payload},
            "sequence": {"$add": [{"$size": "$events"}, 1]}, "event_id": {"$literal": str(uuid.uuid4())},
            "event_key": {"$literal": event_key},
        }]]}}}], return_document=ReturnDocument.AFTER)

    async def finish(self, run_id, epoch, status, answer, reason, detail=None):
        terminal = status != "waiting_user"
        event = {"run_id": {"$literal": run_id}, "type": {"$literal": "run_finished" if terminal else "input_required"},
            "payload": {"$literal": {"status": status, "answer": answer, "reason": reason, **(detail or {})}},
            "sequence": {"$add": [{"$size": "$events"}, 1]}, "event_id": {"$literal": str(uuid.uuid4())}}
        record = await self.runs.find_one_and_update({"_id": run_id, "epoch": epoch, "active": True, "status": {"$in": ["running", "cancelling"] if status == "cancelled" else ["running"]}},
            [{"$set": {"status": {"$literal": status}, "answer": {"$literal": answer}, "stop_reason": {"$literal": reason}, "active": not terminal, "lease_until": 0,
                "events": {"$concatArrays": ["$events", [event]]}}}],
            return_document=ReturnDocument.AFTER)
        if record is None and status != "cancelled" and await self.runs.find_one({"_id": run_id, "epoch": epoch, "status": "cancelling", "active": True}):
            return await self.finish(run_id, epoch, "cancelled", "작업을 중지했습니다.", "user_cancelled")
        return record

    async def claim_invocation(self, run_id, invocation_id, tool_name, arguments, epoch, *, read_only=False):
        await self.fence(run_id, epoch)
        key = {"run_id": run_id, "invocation_id": invocation_id}
        old = await self.calls.find_one(key)
        if old:
            if old["tool_name"] != tool_name or old["arguments"] != arguments:
                raise Conflict("Invocation ID reused with different input")
            if old["status"] == "succeeded":
                return old
            if read_only and old["epoch"] < epoch:
                recovered = await self.calls.find_one_and_update({**key, "epoch": old["epoch"], "status": "running"},
                    {"$set": {"epoch": epoch}}, return_document=ReturnDocument.AFTER)
                if recovered:
                    return recovered
            raise Conflict("Invocation outcome unknown; do not repeat automatically")
        record = {**key, "tool_name": tool_name, "arguments": arguments, "epoch": epoch, "status": "running"}
        await self.calls.insert_one(record)
        return record

    async def finish_invocation(self, run_id, invocation_id, observation, epoch):
        await self.fence(run_id, epoch)
        result = await self.calls.update_one({"run_id": run_id, "invocation_id": invocation_id, "epoch": epoch, "status": "running"},
            {"$set": {"status": "succeeded", "result_id": observation.result_id}})
        if not result.modified_count:
            raise LeaseLost("Invocation lease lost")
