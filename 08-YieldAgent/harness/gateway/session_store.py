"""Principal-bound Motor storage for the pinned Hermes M1 session contract.

Collections: gateway_sessions (principal,id unique; parent/started/title indexes),
gateway_messages (principal,id unique; principal,session_id,seq unique), and
 gateway_message_counters (principal unique, monotonic cross-session message ids).
A session's next_seq is independent of HarnessStore run/event sequences. Parent
links live only in gateway_sessions. Deleted rows retain a tombstone so delayed
creates/appends cannot resurrect a retired conversation. No parallel transcript.

HarnessStore owns run admission/leases and session mutation locks. setup() calls
its existing setup and adds only these new indexes. Mutations sharing a session
use its lock; title transfers lock both the recipient and the previous owner. Mongo
standalone is supported: multi-document operations are not crash-atomic (no
replica-set transaction requirement). Tombstones hide incomplete delete cleanup.

Unsupported SessionDB methods are enumerated in session_db.UNSUPPORTED_METHODS.
Filesystem deletion and worker leases passed to append are explicitly unsupported.
Local runtime policies/worker persistence are outside this core.
The host owns client/loop lifetime; create this store on that loop and use the
SessionDB facade via to_thread for original synchronous callers.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from harness.store import Conflict

SESSIONS_DEFAULTS = {'id': None,
 'runtime_revision': 0,
 'runtime_generation': 0,
 'source': None,
 'created_source': None,
 'user_id': None,
 'session_key': None,
 'chat_id': None,
 'chat_type': None,
 'thread_id': None,
 'display_name': None,
 'origin_json': None,
 'expiry_finalized': 0,
 'model': None,
 'model_config': None,
 'system_prompt': None,
 'system_prompt_hash': None,
 'parent_session_id': None,
 'started_at': None,
 'ended_at': None,
 'end_reason': None,
 'message_count': 0,
 'tool_call_count': 0,
 'input_tokens': 0,
 'output_tokens': 0,
 'cache_read_tokens': 0,
 'cache_write_tokens': 0,
 'reasoning_tokens': 0,
 'cwd': None,
 'git_branch': None,
 'git_repo_root': None,
 'git_metadata_generation': 0,
 'billing_provider': None,
 'billing_base_url': None,
 'billing_mode': None,
 'estimated_cost_usd': None,
 'actual_cost_usd': None,
 'cost_status': None,
 'cost_source': None,
 'pricing_version': None,
 'title': None,
 'title_source': None,
 'last_activity_at': None,
 'last_activity_description': None,
 'last_activity_provenance': None,
 'api_call_count': 0,
 'handoff_state': None,
 'handoff_platform': None,
 'handoff_error': None,
 'compression_failure_cooldown_until': None,
 'compression_failure_error': None,
 'compression_fallback_streak': 0,
 'compression_ineffective_count': 0,
 'compression_recovery_deadline': None,
 'compression_overload_streak': 0,
 'profile_name': None,
 'transport_profile': None,
 'rewind_count': 0,
 'archived': 0,
 'auto_archived': 0,
 'pinned': 0,
 'hidden': 0,
 'last_read_at': None,
 'tool_names': None,
 'transcript_revision': 0}

MESSAGES_DEFAULTS = {'id': None,
 'session_id': None,
 'role': None,
 'content': None,
 'tool_call_id': None,
 'tool_calls': None,
 'tool_name': None,
 'effect_disposition': None,
 'timestamp': None,
 'token_count': None,
 'finish_reason': None,
 'reasoning': None,
 'reasoning_content': None,
 'reasoning_details': None,
 'codex_reasoning_items': None,
 'codex_message_items': None,
 'platform_message_id': None,
 'observed': 0,
 '_compressed_summary': 0,
 'active': 1,
 'compacted': 0,
 'api_content': None,
 'display_kind': None,
 'display_metadata': None,
 'display_identity': None,
 'display_order': None,
 'message_uid': None,
 'absorbed_message_uids': None,
 'tool_call_uids': None,
 'tool_call_uid': None}

RESET_REASONS = {'session_reset', 'session_switch', 'idle', 'daily', 'suspended', 'resume_pending_expired'}
CREATE_FIELDS = {'model', 'model_config', 'system_prompt', 'user_id', 'session_key',
                 'chat_id', 'chat_type', 'thread_id', 'parent_session_id', 'cwd',
                 'profile_name', 'git_repo_root', 'origin_json', 'display_name', 'transport_profile'}


def _config(row):
    raw = row.get('model_config')
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _same_profile_namespace(parent_key, child_key):
    # Exact prefix extraction used by _SAME_KEY_NAMESPACE_SQL in pinned Hermes.
    if parent_key is None or child_key is None:
        return True
    prefix = lambda key: key[:6 + key[6:].find(':') + 1]
    return prefix(parent_key) == prefix(child_key)


def _listing_chain(session_id, by_id):
    # Listing's CTE aggregates ALL continuation siblings; tip projection alone
    # chooses the preferred forward path. A seen set also bounds corrupt cycles.
    chain, seen = [session_id], {session_id}
    for current_id in chain:
        parent = by_id[current_id]
        if parent.get('end_reason') != 'compression':
            continue
        for child in by_id.values():
            if child.get('parent_session_id') == current_id and child['id'] not in seen and not MongoSessionStore._fork(child, parent, presence=True):
                seen.add(child['id'])
                chain.append(child['id'])
    return chain


def _page(rows, limit, offset=0, latest=False):
    # SQL LIMIT < 0 is unbounded; negative OFFSET is zero.
    selected = list(reversed(rows)) if latest else rows
    selected = selected[max(0, offset):]
    if limit is not None and limit >= 0:
        selected = selected[:limit]
    return list(reversed(selected)) if latest else selected


def _call_id(call):
    for key in ('call_id', 'id'):
        raw = call.get(key) if isinstance(call, dict) else getattr(call, key, None)
        value = raw.strip() if isinstance(raw, str) else ''
        if value:
            return value.split('|', 1)[0].strip() or value
    return ''


def _display_key(row):
    calls = row.get('tool_calls') or []
    ids = tuple(_call_id(call) for call in calls)
    if row['role'] == 'assistant' and ids and all(ids):
        return ('assistant', None, row['timestamp'], row.get('tool_call_id'), ids, row.get('tool_name'))
    content = row.get('content')
    if isinstance(content, (dict, list)):
        content = json.dumps(content)
    return (row['role'], content, row['timestamp'], row.get('tool_call_id'),
            json.dumps(calls) if calls else None, row.get('tool_name'))


def _display_rows(rows, drop_removed=False):
    """Pinned display generation choice: original order, prefer active/newest;
    summary carriers fold by UID, removed occurrences suppress older copies.
    """
    chosen, order, removed, first_by_uid, identities = {}, [], {}, {}, {}
    for row in rows:
        key = _display_key(row)
        uid = row.get('message_uid')
        absorbed = row.get('absorbed_message_uids') or []
        if isinstance(absorbed, str):
            absorbed = json.loads(absorbed)
        copies = [key, *(('uid', u) for u in [uid, *absorbed] if u)]
        gone = not row.get('active') and not row.get('compacted')
        for copy in copies:
            removed[copy] = gone
        if gone and drop_removed:
            continue
        if (row.get('display_metadata') or {}).get('model_only'):
            continue
        if uid:
            if row.get('_compressed_summary'):
                key = first_by_uid.get(uid, key)
            else:
                first_by_uid.setdefault(uid, key)
        if key not in chosen:
            order.append(key)
            chosen[key] = row
            identities[key] = copies
        elif (row['active'], row['id']) > (chosen[key]['active'], chosen[key]['id']):
            chosen[key] = row
            identities[key] = copies
    return [chosen[k] for k in order if not drop_removed or not any(removed[c] for c in identities[k])]


def _public_session(row):
    if row is None or row.get('deleted'):
        return None
    result = {key: row.get(key, value) for key, value in SESSIONS_DEFAULTS.items()}
    for key in ('_branched_from', '_reset_from'):
        value = _config(row).get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _public_message(row):
    result = {key: row.get(key, value) for key, value in MESSAGES_DEFAULTS.items()
              if key not in ('display_identity', 'display_order', '_compressed_summary')}
    if row.get('_compressed_summary'):
        result['_compressed_summary'] = True
    return result


class TitleInUse(ValueError):
    pass


class MongoSessionStore:
    def __init__(self, harness_store, principal_id):
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError('A nonempty principal_id is required')
        self._principal_id = principal_id
        self.harness = harness_store
        self.sessions = harness_store.db.gateway_sessions
        self.messages = harness_store.db.gateway_messages
        self.counters = harness_store.db.gateway_message_counters

    @property
    def principal_id(self):
        return self._principal_id

    def _scope(self, **fields):
        return {**fields, 'principal_id': self._principal_id}

    def _session(self, session_id):
        return self._scope(id=session_id, deleted={'$ne': True})

    async def setup(self):
        await self.harness.setup()
        await self.sessions.create_index([('principal_id', 1), ('id', 1)], unique=True)
        await self.sessions.create_index([('principal_id', 1), ('parent_session_id', 1), ('started_at', -1)])
        await self.sessions.create_index([('principal_id', 1), ('started_at', -1)])
        await self.sessions.create_index([('principal_id', 1), ('title', 1)], unique=True,
                                         partialFilterExpression={'title': {'$type': 'string'}})
        await self.messages.create_index([('principal_id', 1), ('id', 1)], unique=True)
        await self.messages.create_index([('principal_id', 1), ('session_id', 1), ('seq', 1)], unique=True)
        await self.counters.create_index('principal_id', unique=True)

    @asynccontextmanager
    async def _lock(self, session_id):
        # Same distributed lock as existing harness writes, not a process-local mutex.
        async with self.harness.session_lock(self._principal_id, session_id):
            yield

    async def create_session(self, session_id: str, source: str, **kwargs) -> str:
        unknown = kwargs.keys() - CREATE_FIELDS
        if unknown:
            raise TypeError(f'Unexpected session arguments: {sorted(unknown)}')
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self._lock(session_id))
            parent_id = kwargs.get('parent_session_id')
            if parent_id and parent_id != session_id:
                await stack.enter_async_context(self._lock(parent_id))
            existing = await self.sessions.find_one(self._scope(id=session_id))
            if existing and existing.get('deleted'):
                raise LookupError('Session not found')
            parent_id = kwargs.get('parent_session_id')
            parent = await self.get_session(parent_id) if parent_id else None
            if parent_id and parent_id != session_id and not parent:
                raise LookupError('Session not found')
            row = dict(existing or SESSIONS_DEFAULTS)
            if existing is None:
                row.update(id=session_id, source=source, created_source=source, started_at=time.time(), next_seq=0)
            elif row['source'] == 'unknown':
                row['source'] = source
            if row.get('created_source') in (None, '', 'unknown', 'local') and source != 'local':
                row['created_source'] = source
            for key, value in kwargs.items():
                if existing is not None and key == 'user_id':
                    continue
                if row.get(key) is None:
                    row[key] = (json.dumps(value) if value else None) if key == 'model_config' else value
            if parent:
                inherited = ['cwd', 'git_repo_root', 'git_branch']
                if _same_profile_namespace(parent.get('session_key'), row.get('session_key')):
                    inherited.append('profile_name')
                if parent['end_reason'] == 'compression' and not self._explicit_fork(row):
                    inherited += ['user_id', 'session_key', 'chat_id', 'chat_type', 'thread_id',
                                  'display_name', 'origin_json', 'transport_profile']
                for key in inherited:
                    if row.get(key) is None:
                        row[key] = parent.get(key)
            if row.get('system_prompt') is not None:
                row['system_prompt_hash'] = hashlib.sha256(row['system_prompt'].encode()).hexdigest()
            await self.sessions.replace_one(self._scope(id=session_id), self._scope(**row), upsert=True)
        return session_id

    async def ensure_session(self, session_id, source='unknown', model=None, **kwargs):
        return await self.create_session(session_id, source, model=model, **kwargs)

    async def get_session(self, session_id):
        return _public_session(await self.sessions.find_one(self._session(session_id)))

    async def get_session_list_row(self, session_id):
        row = await self.get_session(session_id)
        if row is None:
            return None
        return {k: row[k] for k in ('id', 'source', 'title', 'started_at', 'ended_at', 'message_count', 'model', 'hidden', 'archived')}

    async def resolve_session_id(self, session_id_or_prefix):
        row = await self.get_session(session_id_or_prefix)
        if row:
            return row['id']
        # Literal prefix, never an unescaped Mongo regex supplied by the caller.
        rows = await self.sessions.find(self._scope(deleted={'$ne': True}), {'id': 1}).to_list(None)
        matches = [r['id'] for r in rows if r['id'].lower().startswith(session_id_or_prefix.lower())]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _explicit_fork(row):
        # Original _is_explicit_fork_child_row's default (reset is not included).
        if row.get('source') == 'tool':
            return True
        cfg = _config(row)
        markers = (cfg.get('_branched_from'), cfg.get('_delegate_from'))
        parent_id = row.get('parent_session_id')
        return parent_id in markers if parent_id else any(m is not None for m in markers)

    @staticmethod
    def _fork(row, parent=None, *, presence=False):
        cfg = _config(row)
        markers = [cfg.get(k) for k in ('_branched_from', '_delegate_from', '_reset_from')]
        pid = row.get('parent_session_id')
        marked = any(m is not None for m in markers) if presence or not pid else pid in markers
        legacy_reset = bool(parent and parent['end_reason'] in RESET_REASONS and row.get('session_key')
                            and row['session_key'] == parent.get('session_key'))
        return row.get('source') == 'tool' or marked or legacy_reset

    async def _children(self, session_id):
        return await self.sessions.find(self._scope(parent_session_id=session_id, deleted={'$ne': True})).to_list(None)

    async def _last_active(self, row):
        latest = await self.messages.find_one(self._scope(session_id=row['id'], timestamp={'$gte': 0, '$lte': 4_200_000_000}), sort=[('timestamp', -1)])
        values = [row.get('last_activity_at'), latest.get('timestamp') if latest else None]
        valid = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 4_200_000_000]
        return max(valid) if valid else row['started_at']

    async def get_compression_chain(self, session_id):
        chain = [session_id] if session_id else []
        current = await self.get_session(session_id)
        for _ in range(1000):
            if not current or current['end_reason'] != 'compression':
                break
            candidates = [c for c in await self._children(current['id']) if not self._fork(c, current, presence=True)]
            ranked = [(0 if c['end_reason'] == 'compression' else 1 if c['ended_at'] is None else 2,
                       -(await self._last_active(c)), -c['started_at'], c['id'], c) for c in candidates]
            if not ranked:
                break
            # id DESC is the final tie-break in the original SQL.
            ranked.sort(key=lambda x: x[3], reverse=True)
            ranked.sort(key=lambda x: x[:3])
            current = ranked[0][4]
            if current['id'] in chain:
                break
            chain.append(current['id'])
        return chain

    async def get_compression_tip(self, session_id):
        chain = await self.get_compression_chain(session_id)
        return chain[-1] if chain else session_id

    async def _ancestors(self, session_id):
        current = await self.get_session(session_id)
        chain = [session_id] if current else []
        while current and len(chain) < 100:
            parent = await self.get_session(current.get('parent_session_id'))
            if not parent or parent['end_reason'] != 'compression' or self._fork(current, parent) or parent['id'] in chain:
                break
            chain.append(parent['id'])
            current = parent
        return list(reversed(chain))

    async def get_compression_lineage(self, session_id):
        session = await self.get_session(session_id)
        if not session or self._explicit_fork(session):
            return [session_id] if session else []
        ancestors = await self._ancestors(session_id)
        if not ancestors:
            return []
        chain = [ancestors[0]]
        current = await self.get_session(chain[0])
        while current and current['end_reason'] == 'compression':
            children = sorted(await self._children(current['id']), key=lambda c: c['started_at'])
            child = next((c for c in children if not self._fork(c, current)), None)
            if child is None or child['id'] in chain:
                break
            chain.append(child['id'])
            current = child
        return chain if session_id in chain else [session_id]

    async def resolve_resume_session_id(self, session_id):
        # A foreign id behaves exactly like a nonexistent id: unchanged input.
        if not await self.get_session(session_id):
            return session_id
        session_id = await self.get_compression_tip(session_id)
        current, seen, best = session_id, set(), None
        for _ in range(32):
            if current in seen:
                break
            seen.add(current)
            if await self.messages.find_one(self._scope(session_id=current), {'id': 1}):
                best = current
            parent = await self.get_session(current)
            children = [c for c in await self._children(current) if not self._fork(c, parent, presence=True)]
            if not children:
                break
            current = max(children, key=lambda c: (c['started_at'], c['id']))['id']
        return best or session_id

    async def end_session(self, session_id, end_reason):
        async with self._lock(session_id):
            await self.sessions.update_one({**self._session(session_id), 'ended_at': None},
                                           {'$set': {'ended_at': time.time(), 'end_reason': end_reason}})

    async def get_messages(self, session_id, include_inactive=False, include_compacted=False,
                           limit=None, offset=0, latest=False, after_id=None, include_ancestors=False):
        if after_id is not None and (latest or offset or include_compacted or include_ancestors):
            raise ValueError('after_id is incompatible with latest/offset/include_compacted/include_ancestors')
        ids = await self._ancestors(session_id) if include_ancestors else ([session_id] if await self.get_session(session_id) else [])
        if not ids:
            return []
        query = self._scope(session_id={'$in': ids})
        if after_id is not None:
            query['id'] = {'$gt': after_id}
        if len(ids) == 1 and not include_compacted:
            if not include_inactive:
                query['active'] = 1
            if limit == 0:
                return []
            cursor = self.messages.find(query).sort('id', -1 if latest else 1).skip(max(0, offset))
            if limit is not None and limit > 0:
                cursor = cursor.limit(limit)
            rows = await cursor.to_list(None)
            if latest:
                rows.reverse()
            return [_public_message(r) for r in rows]
        rows = await self.messages.find(query).sort('id', 1).to_list(None)
        display = include_compacted and not include_inactive
        if not include_inactive and not display:
            rows = [r for r in rows if r['active']]
        if len(ids) > 1 or include_compacted:
            rows = _display_rows(rows, drop_removed=display)
        if len(ids) > 1:
            # Upstream's merged-lineage branch pages a Python list, including
            # its negative slicing semantics; single-segment SQL uses LIMIT -1.
            rows = rows[::-1][offset:][:limit][::-1] if latest else rows[offset:][:limit]
        else:
            rows = _page(rows, limit, offset, latest)
        return [_public_message(r) for r in rows]

    async def append_message(self, session_id, role, content=None, tool_name=None, tool_calls=None,
                             tool_call_id=None, token_count=None, finish_reason=None, reasoning=None,
                             reasoning_content=None, reasoning_details=None, codex_reasoning_items=None,
                             codex_message_items=None, platform_message_id=None, observed=False,
                             effect_disposition=None, _compressed_summary=False, timestamp=None,
                             api_content=None, display_kind=None, display_metadata=None,
                             compression_lock_holder=None, turn_lease_holder=None, turn_lease_ttl_seconds=300.0,
                             message_uid=None):
        values = dict(locals())
        if compression_lock_holder is not None or turn_lease_holder is not None:
            raise NotImplementedError('Hermes compression/turn leases require the later execution adapter')
        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except ValueError:
                tool_calls = None
        if isinstance(display_metadata, str):
            try:
                display_metadata = json.loads(display_metadata)
            except ValueError:
                display_metadata = None
        if not isinstance(display_metadata, dict):
            display_metadata = None
        try:
            stamp = float(timestamp.timestamp()) if isinstance(timestamp, datetime) else float(timestamp) if timestamp is not None else time.time()
            if not 0 <= stamp <= 4_200_000_000:
                stamp = time.time()
        except (ValueError, TypeError):
            stamp = time.time()
        async with self._lock(session_id):
            row = await self.get_session(session_id)
            if row is None:
                raise LookupError('Session not found')
            if row['end_reason'] == 'compression':
                raise Conflict('Session closed by compression')
            try:
                counter = await self.counters.find_one_and_update(self._scope(), {'$inc': {'value': 1}},
                            upsert=True, return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                # Two different sessions can allocate this principal's first ID together.
                counter = await self.counters.find_one_and_update(self._scope(), {'$inc': {'value': 1}},
                            return_document=ReturnDocument.AFTER)
            allocated = await self.sessions.find_one_and_update(self._session(session_id), {'$inc': {'next_seq': 1}},
                        return_document=ReturnDocument.AFTER)
            message = {**MESSAGES_DEFAULTS, **{k: v for k, v in values.items() if k in MESSAGES_DEFAULTS}}
            message.update(id=counter['value'], seq=allocated['next_seq'], timestamp=stamp,
                           active=1, compacted=0, observed=int(observed), tool_calls=tool_calls or None,
                           message_uid=message_uid if isinstance(message_uid, str) and message_uid else str(uuid.uuid4()),
                           display_metadata=display_metadata or None)
            if role == 'assistant' and tool_calls:
                uids = {call_id: uuid.uuid4().hex for call in tool_calls if (call_id := _call_id(call))}
                if uids:
                    message['tool_call_uids'] = json.dumps(uids, sort_keys=True)
            for key in ('content', 'reasoning', 'reasoning_content', 'tool_name', 'api_content', 'display_kind'):
                if isinstance(message[key], str):
                    message[key] = ''.join('\ufffd' if 0xd800 <= ord(c) <= 0xdfff else c for c in message[key])
            for key in ('api_content', 'display_kind'):
                if not isinstance(message[key], str):
                    message[key] = None
            for key in ('reasoning_details', 'codex_reasoning_items', 'codex_message_items'):
                value = message[key]
                message[key] = (value if isinstance(value, str) else json.dumps(value)) if value else None
            await self.messages.insert_one(self._scope(**message))
            await self.sessions.update_one(self._session(session_id),
                {'$inc': {'message_count': 1, 'tool_call_count': len(tool_calls) if isinstance(tool_calls, list) else int(tool_calls is not None)}})
            return message['id']

    async def get_session_title(self, session_id):
        row = await self.get_session(session_id)
        return row['title'] if row else None

    async def get_session_title_source(self, session_id):
        row = await self.get_session(session_id)
        return row['title_source'] if row and row['title'] is not None else None

    async def set_session_title(self, session_id, title, *, source='user', on_saved=None):
        if source not in ('user', 'derived', 'llm'):
            raise ValueError('Invalid title source')
        # Pinned sanitize_title semantics: controls/invisibles removed, whitespace collapsed.
        title = ''.join('\ufffd' if 0xd800 <= ord(c) <= 0xdfff else c for c in (title or '')
                        if not (0 <= ord(c) <= 8 or ord(c) in (11, 12, 127, 0xfeff, 0xfffc)
                                or 14 <= ord(c) <= 31 or 0x200b <= ord(c) <= 0x200f
                                or 0x2028 <= ord(c) <= 0x202e or 0x2060 <= ord(c) <= 0x2069
                                or 0xfff9 <= ord(c) <= 0xfffb))
        title = ' '.join(title.split()) or None
        if title and len(title) > 100:
            raise ValueError('Title too long (max 100)')
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self._lock(session_id))
            row = await self.get_session(session_id)
            if row is None:
                return False
            if row['hidden'] and row['title'] == 'Bot Chat' and title != 'Bot Chat':
                if source != 'user':
                    return False
                raise ValueError('Canonical Bot Chat cannot be renamed')
            if source != 'user' and (not title or row['title'] is not None and
                    (source == 'derived' or row['title_source'] in ('user', 'llm', None))):
                return False
            other = await self.sessions.find_one(self._scope(title=title, id={'$ne': session_id}, deleted={'$ne': True})) if title else None
            if other:
                await stack.enter_async_context(self._lock(other['id']))
                other = await self.get_session(other['id'])
                if other and other['title'] == title:
                    ancestors = await self._ancestors(session_id)
                    ghost = other['ended_at'] is not None and not other['message_count']
                    if other['id'] not in ancestors and not (ghost and not other['hidden']) and not (
                        title == 'Bot Chat' and other['archived'] and other['hidden']):
                        raise TitleInUse('Title is already in use')
                    await self.sessions.update_one(self._session(other['id']), {'$set': {'title': None, 'title_source': None}})
            try:
                result = await self.sessions.update_one(self._session(session_id),
                    {'$set': {'title': title, 'title_source': source if title else None}})
            except DuplicateKeyError:
                raise TitleInUse('Title is already in use') from None
            if result.matched_count and on_saved:
                await on_saved(title)
            return bool(result.matched_count)

    async def apply_native_title(self, session_id, title, source, *, on_saved=None):
        """Mirror a Hermes title and resolve names reserved only in the web gateway."""
        if source not in ('derived', 'llm') or not isinstance(title, str) or not title.strip():
            return None
        try:
            written = await self.set_session_title(session_id, title, source=source, on_saved=on_saved)
        except TitleInUse:
            if source == 'derived':
                return None
            base = re.sub(r' #\d+$', '', title)
            names = self.sessions.find(self._scope(title={'$regex': '^' + re.escape(base) + r' #\d+$'}), {'title': 1})
            numbers = [int(row['title'].rsplit(' #', 1)[1]) async for row in names]
            title = f"{base} #{max([1, *numbers]) + 1}"
            written = await self.set_session_title(session_id, title, source=source, on_saved=on_saved)
        return title if written else None

    async def _set_session_flag(self, session_id, field, value):
        async with self._lock(session_id):
            result = await self.sessions.update_one(self._session(session_id), {'$set': {field: value}})
            if not result.matched_count:
                raise LookupError('Session not found')

    async def set_session_deleted(self, session_id):
        await self._set_session_flag(session_id, 'deleted', True)

    async def set_session_archived(self, session_id, archived=True):
        await self._set_session_flag(session_id, 'archived', archived)

    async def set_session_hidden(self, session_id, hidden=True):
        await self._set_session_flag(session_id, 'hidden', hidden)

    async def start_run(self, session_id, request_id, query):
        """Host admission API (not a fabricated SessionDB method). Idempotent receipts
        and the partial active unique index remain solely owned by HarnessStore.
        """
        async with self._lock(session_id):
            if not await self.get_session(session_id):
                raise LookupError('Session not found')
            return await self.harness.start_run(self._principal_id, session_id, request_id, query,
                                                execution_kind='gateway', engine='hermes')

    async def _rich(self, row, compact_rows=False):
        result = _public_session(row)
        result['last_active'] = await self._last_active(row)
        first = await self.messages.find_one(self._scope(session_id=row['id'], role='user', content={'$ne': None}, display_kind={'$ne': 'hidden'}),
                                              sort=[('timestamp', 1), ('id', 1)])
        text = str(first['content'] or '').strip().replace('\n', ' ').replace('\r', ' ') if first else ''
        result['preview'] = text[:60] + '...' if len(text) > 60 else text
        if compact_rows:
            for key in ('system_prompt', 'system_prompt_hash', 'git_metadata_generation'):
                result.pop(key, None)
        return result

    async def list_sessions_rich(
        self, source=None, sources=None, exclude_sources=None, cwd_prefix=None, limit=20, offset=0,
        include_children=False, min_message_count=0, project_compression_tips=True,
        order_by_last_active=False, include_archived=False, archived_only=False, id_query=None,
        search_query=None, compact_rows=False, include_pinned=False, session_key=None,
        include_hidden=False, include_subagents=False, local_created_sources=None,
    ):
        rows = await self.sessions.find(self._scope(deleted={'$ne': True})).to_list(None)
        by_id = {row['id']: row for row in rows}
        labels = [source] if source else list(sources or [])
        selected, pins = [], []
        for row in rows:
            cfg = _config(row)
            parent = by_id.get(row.get('parent_session_id'))
            legacy_branch = bool(parent and parent['end_reason'] == 'branched' and parent['ended_at'] is not None
                                 and row['started_at'] >= parent['ended_at'])
            legacy_reset = bool(parent and parent['end_reason'] in RESET_REASONS and row.get('session_key')
                                and row['session_key'] == parent.get('session_key'))
            listable = not row.get('parent_session_id') or cfg.get('_branched_from') is not None or cfg.get('_reset_from') is not None or legacy_branch or legacy_reset
            if not include_children and not ((listable and cfg.get('_delegate_from') is None) or
                                             (include_subagents and cfg.get('_delegate_from') is not None)):
                continue
            if labels:
                admitted = row['source'] in labels
                if local_created_sources:
                    admitted = row['source'] in [s for s in labels if s != 'local'] or (
                        row['source'] == 'local' and row['created_source'] in local_created_sources)
                if not admitted:
                    continue
            if row['source'] in (exclude_sources or []):
                continue
            if session_key and row['session_key'] != session_key:
                continue
            if cwd_prefix:
                prefix = cwd_prefix.rstrip('/\\') or cwd_prefix
                cwd = row.get('cwd') or ''
                if cwd != prefix and not cwd.startswith((prefix + '/', prefix + '\\')):
                    continue
            if min_message_count > 0 and row['message_count'] < min_message_count:
                continue
            if not include_hidden and not archived_only and row['hidden']:
                continue
            rich = await self._rich(row, compact_rows)
            chain = _listing_chain(row['id'], by_id)
            rich['_sort_activity'] = max([await self._last_active(by_id[s]) for s in chain if s in by_id])
            search_match = True
            if order_by_last_active:
                id_needle = (id_query or '').strip().lower()
                needle = (search_query or '').strip().lower()
                compact_needle = ''.join(c for c in needle if c.isalnum())
                chain_rows = [by_id[s] for s in chain if s in by_id]
                if id_needle and not any(id_needle in c['id'].lower() for c in chain_rows):
                    search_match = False
                if needle and not any(needle in c['id'].lower() or needle in (c['title'] or '').lower() or
                                      (compact_needle and compact_needle in (c['title'] or '').lower().translate(str.maketrans('', '', '-_. ')))
                                      for c in chain_rows):
                    search_match = False
            archive_match = bool(row['archived']) if archived_only else include_archived or not row['archived']
            if archive_match and search_match:
                selected.append(rich)
            if include_pinned and row['pinned']:
                pins.append(rich)
        key = (lambda r: (r['_sort_activity'], r['started_at'], r['id'])) if order_by_last_active else (lambda r: r['started_at'])
        selected.sort(key=key, reverse=True)
        selected = _page(selected, limit, offset)
        seen = {r['id'] for r in selected}
        selected += [r for r in sorted(pins, key=lambda r: r['started_at'], reverse=True) if r['id'] not in seen]
        for row in selected:
            row.pop('_sort_activity', None)
            if project_compression_tips and not include_children and row['end_reason'] == 'compression':
                chain = await self.get_compression_chain(row['id'])
                if len(chain) > 1:
                    tip = await self._rich(by_id[chain[-1]], compact_rows)
                    root_id, root_title = row['id'], row['title']
                    for field in ('id', 'ended_at', 'end_reason', 'message_count', 'tool_call_count', 'title',
                                  'last_active', 'preview', 'model', 'system_prompt', 'cwd', 'git_branch', 'git_repo_root'):
                        if field in tip:
                            row[field] = tip[field]
                    row['title'] = row['title'] if row['title'] is not None else root_title
                    row.update(_lineage_root_id=root_id, _lineage_ids=chain, continuation_kind='compression')
            row['unread'] = row['last_read_at'] is not None and (row['last_active'] or row['started_at'] or 0) > row['last_read_at']
        return selected

    async def delete_session(self, session_id, sessions_dir=None, expected_delete_ids=None,
                             expected_display_messages=None, exclude_active_write_guards=False,
                             include_compression_chain=False):
        if sessions_dir is not None:
            raise NotImplementedError('Filesystem session deletion is outside M1')
        async with self._lock(session_id):
            if not await self.get_session(session_id):
                return False
            ids = {session_id}
            if include_compression_chain:
                # All continuation siblings, not only the preferred forward tip.
                ids.update(await self._ancestors(session_id))
                frontier = list(ids)
                while frontier:
                    parent = await self.get_session(frontier.pop())
                    if parent and parent['end_reason'] == 'compression':
                        for child in await self._children(parent['id']):
                            if child['id'] not in ids and not self._fork(child, parent, presence=True):
                                ids.add(child['id'])
                                frontier.append(child['id'])
            frontier = list(ids)
            while frontier:
                parent_id = frontier.pop()
                rows = await self.sessions.find(self._scope(deleted={'$ne': True})).to_list(None)
                for child in rows:
                    marker = _config(child).get('_delegate_from')
                    if child['id'] not in ids and (marker == parent_id or (marker is not None and child['parent_session_id'] == parent_id)):
                        ids.add(child['id'])
                        frontier.append(child['id'])
            if expected_delete_ids is not None and set(expected_delete_ids) != ids:
                return False
            # Acquire the remaining physical-session locks in stable order. Any
            # competing mutation refuses with Conflict; no wait/deadlock cycle.
            async with AsyncExitStack() as stack:
                for sid in sorted(ids - {session_id}):
                    await stack.enter_async_context(self._lock(sid))
                if expected_display_messages is not None:
                    for sid, expected in expected_display_messages.items():
                        if not await self.get_session(sid) or await self.get_messages(sid, include_compacted=True) != expected:
                            return False
                if exclude_active_write_guards and await self.harness.runs.find_one(
                        self._scope(session_id={'$in': list(ids)}, active=True)):
                    raise Conflict('Session has an active run')
                await self.sessions.update_many(self._scope(id={'$in': list(ids)}),
                                                {'$set': {'deleted': True, 'title': None, 'title_source': None}})
                await self.messages.delete_many(self._scope(session_id={'$in': list(ids)}))
                await self.sessions.update_many(self._scope(parent_session_id={'$in': list(ids)}, deleted={'$ne': True}),
                                                {'$set': {'parent_session_id': None}})
            return True

    async def session_count(self, source=None, sources=None, cwd_prefix=None, min_message_count=0,
                            include_archived=False, archived_only=False, exclude_children=False,
                            exclude_sources=None, include_subagents=False, local_created_sources=None):
        rows = await self.list_sessions_rich(source=source, sources=sources, cwd_prefix=cwd_prefix,
            min_message_count=min_message_count, include_archived=include_archived, archived_only=archived_only,
            include_children=not exclude_children, exclude_sources=exclude_sources, include_subagents=include_subagents,
            local_created_sources=local_created_sources, include_hidden=True, project_compression_tips=False, limit=-1)
        return len(rows)

    async def message_count(self, session_id=None):
        if session_id:
            if not await self.get_session(session_id):
                return 0
            ids = [session_id]
        else:
            ids = [r['id'] for r in await self.sessions.find(self._scope(deleted={'$ne': True}), {'id': 1}).to_list(None)]
        return await self.messages.count_documents(self._scope(session_id={'$in': ids}))
