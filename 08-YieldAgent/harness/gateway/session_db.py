"""Hermes 5894e750 SessionDB's M1 synchronous contract, backed only by Mongo.

Active call closure inspected: web_routers/sessions.py _resolve_session_id ->
resolve_session_id, messages -> resolve_resume_session_id/get_messages, listing ->
list_sessions_rich. Its _with_db runs in asyncio.to_thread. session_execution.py
passes the synchronous DB to AIAgent and reads get_session off-loop; upstream
AsyncSessionDB also uses to_thread. Bind this wrapper to the Motor owner's running
loop and call it from a worker thread. No private loop/client per request, no
blocking wait on an event-loop thread. Future worker IPC must use the same host
store; it is outside PR-3. close() retires the facade, not the shared host client.

Constructor intentionally accepts the injected principal-bound store and owner
loop instead of the upstream filesystem constructor. Public operation signatures
below are transcribed from the pinned source; annotations do not affect dispatch.

Unsupported public methods (all raise NotImplementedError):
    acquire_session_turn_lease, adopt_orphaned_gateway_session, adopt_session_lineage_from, append_completion_note, append_delegation_delivery
    append_messages_batch, apply_telegram_topic_migration, archive_and_compact, archive_sessions, archive_stale_sessions
    assert_export_safe, assert_exports_safe, assert_resume_safe, auxiliary_usage_by_task, backfill_acp_session_cwd
    backfill_null_session_profiles, backfill_repo_roots, bind_telegram_topic, claim_handoff, clear_backend_heartbeat
    clear_compression_failure_cooldown, clear_messages, clear_session_activity_labels, complete_handoff, conversation_revision
    count_empty_sessions, count_messages_all, count_open_prune_matches, count_prune_matches, cron_run_identity
    deactivate_message, deactivate_messages_by_display_kind, declared_scope_identity, delete_empty_sessions, delete_gateway_routing_rows
    delete_moved_session, delete_session_if_empty, delete_sessions, delete_telegram_topic_binding, disable_telegram_topic_mode
    discard_unadmitted_session, display_message_count, distinct_session_cwds, enable_telegram_topic_mode, export_all
    export_session, export_session_for_move, export_session_lineage, fail_handoff, finalize_orphaned_compression_sessions
    find_crossed_profile_sessions, find_foreign_import, find_latest_gateway_session_for_peer, find_live_compression_child, find_orphaned_gateway_sessions
    find_pr_url_messages, find_profile_less_telegram_topic_rows, find_session_by_origin, flush_token_counts, fts_cjk_rebuild_status
    fts_cjk_rebuild_step, fts_optimize_available, fts_rebuild_status, fts_rebuild_step, gateway_routing_entry_for_session
    get_active_message_ids, get_active_message_watermark, get_ancestor_display_prefix, get_anchored_view, get_compression_failure_cooldown
    get_compression_failure_cooldown_row, get_compression_fallback_streak, get_compression_ineffective_count, get_compression_lock_holder, get_compression_overload_streak
    get_compression_recovery_deadline, get_conversation_root, get_handoff_state, get_latest_todo_result, get_message_reactions
    get_message_role, get_messages_around, get_messages_as_conversation, get_meta, get_next_title_in_lineage
    get_recent_session_model_route, get_resume_conversations, get_resume_message_count, get_session_by_title, get_session_delete_targets
    get_session_model_config_value, get_telegram_topic_binding, get_telegram_topic_binding_by_session, has_archived_messages, has_gateway_input_owner
    has_pending_handoffs, has_platform_message_id, held_row_mismatches, import_foreign_history, import_moved_session
    import_sessions, increment_compression_overload_streak, increment_hygiene_failure_streak, insert_gateway_routing_rows_if_absent, is_explicit_fork_child
    is_kanban_owned_session, is_telegram_session_linked_to_topic, is_telegram_topic_mode_enabled, key_profiles_for_chat, latest_conversation_boundary
    latest_conversation_role, latest_message_row_id, list_backend_heartbeats, list_cron_job_runs, list_gateway_routing_rows
    list_gateway_sessions, list_meta_prefix, list_never_active_keyed_sessions, list_pending_handoffs, list_prune_candidates
    list_recent_sessions_bounded, list_recent_user_messages, list_skill_scaffolded_sessions, list_telegram_topic_bindings_for_chat, list_unlinked_telegram_sessions_for_user
    load_gateway_routing_entries, logical_size_bytes, maybe_auto_archive, maybe_auto_prune_and_vacuum, optimize_fts
    optimize_fts_storage, patch_session_model_config, plan_user_turn_rewind, promote_to_session_reset, prune_empty_ghost_sessions
    prune_never_active_keyed_sessions, prune_sessions, prune_stale_heartbeats, publish_compression_child, publish_session_git_metadata
    purge_profile_state, purge_stale_tool_call_markers, queue_token_counts, rebuild_fts, reclaim_stale_running_handoffs
    record_auxiliary_usage, record_compression_failure_cooldown, record_gateway_session_peer, refresh_compression_lock, refresh_session_turn_lease
    register_backend_heartbeat, rekey_legacy_main_sessions, rekey_profile_state, relabel_sessions_to_key_profile, relabel_telegram_topic_rows
    release_compression_lock, release_session_turn_lease, reopen_if_explicitly_closed, reopen_orphaned_compression_session, reopen_session
    replace_gateway_routing_entries, replace_messages, replace_messages_in_transaction, request_handoff, reset_hygiene_failure_streak
    resolve_active_row_id, resolve_session_by_title, restore_compression_failure_cooldown_row, retag_kanban_worker_sessions, retire_gateway_routes
    retire_undrained_queue_rows, retry_deferred_fts_recovery, rewind_to_message, rewind_user_turn, rewrite_pruned_rows
    rewritten_held_row_ids, sanitize_title, save_gateway_routing_entry, search_messages, search_sessions
    search_sessions_by_id, session_count_by_source, session_count_ge, session_gateway_runtime, session_ids_with_model_config_key
    session_lifecycle_statuses, session_turn_lease_expires_at, session_unread, session_yolo_enabled, set_auto_title
    set_compression_fallback_streak, set_compression_ineffective_count, set_compression_overload_streak, set_compression_recovery_deadline, set_expiry_finalized
    set_latest_matching_message_display_kind, set_latest_user_api_content, set_message_api_content, set_message_reaction, set_meta
    set_session_archived, set_session_hidden, set_session_pinned, set_session_read, set_session_title_source
    set_session_yolo, set_user_message_content, sever_crossed_parents, stamp_legacy_compression_chains, sweep_orphaned_sessions
    take_unseen_reactions, touch_session_activity, try_acquire_compression_lock, try_acquire_session_turn_lease, unarchive_recoverable_session
    update_conversation_model_config_value, update_session_billing_route, update_session_cwd, update_session_meta, update_session_model
    update_session_runtime_lock, update_session_tool_names, update_system_prompt, update_token_counts, usage_totals
    vacuum
"""
from __future__ import annotations

import asyncio

IMPLEMENTED_METHODS = ('create_session',
 'ensure_session',
 'get_session',
 'get_session_list_row',
 'resolve_session_id',
 'list_sessions_rich',
 'resolve_resume_session_id',
 'get_messages',
 'append_message',
 'end_session',
 'get_compression_chain',
 'get_compression_tip',
 'get_compression_lineage',
 'set_session_title',
 'get_session_title',
 'get_session_title_source',
 'delete_session',
 'session_count',
 'message_count',
 'close')

UNSUPPORTED_METHODS = ('acquire_session_turn_lease',
 'adopt_orphaned_gateway_session',
 'adopt_session_lineage_from',
 'append_completion_note',
 'append_delegation_delivery',
 'append_messages_batch',
 'apply_telegram_topic_migration',
 'archive_and_compact',
 'archive_sessions',
 'archive_stale_sessions',
 'assert_export_safe',
 'assert_exports_safe',
 'assert_resume_safe',
 'auxiliary_usage_by_task',
 'backfill_acp_session_cwd',
 'backfill_null_session_profiles',
 'backfill_repo_roots',
 'bind_telegram_topic',
 'claim_handoff',
 'clear_backend_heartbeat',
 'clear_compression_failure_cooldown',
 'clear_messages',
 'clear_session_activity_labels',
 'complete_handoff',
 'conversation_revision',
 'count_empty_sessions',
 'count_messages_all',
 'count_open_prune_matches',
 'count_prune_matches',
 'cron_run_identity',
 'deactivate_message',
 'deactivate_messages_by_display_kind',
 'declared_scope_identity',
 'delete_empty_sessions',
 'delete_gateway_routing_rows',
 'delete_moved_session',
 'delete_session_if_empty',
 'delete_sessions',
 'delete_telegram_topic_binding',
 'disable_telegram_topic_mode',
 'discard_unadmitted_session',
 'display_message_count',
 'distinct_session_cwds',
 'enable_telegram_topic_mode',
 'export_all',
 'export_session',
 'export_session_for_move',
 'export_session_lineage',
 'fail_handoff',
 'finalize_orphaned_compression_sessions',
 'find_crossed_profile_sessions',
 'find_foreign_import',
 'find_latest_gateway_session_for_peer',
 'find_live_compression_child',
 'find_orphaned_gateway_sessions',
 'find_pr_url_messages',
 'find_profile_less_telegram_topic_rows',
 'find_session_by_origin',
 'flush_token_counts',
 'fts_cjk_rebuild_status',
 'fts_cjk_rebuild_step',
 'fts_optimize_available',
 'fts_rebuild_status',
 'fts_rebuild_step',
 'gateway_routing_entry_for_session',
 'get_active_message_ids',
 'get_active_message_watermark',
 'get_ancestor_display_prefix',
 'get_anchored_view',
 'get_compression_failure_cooldown',
 'get_compression_failure_cooldown_row',
 'get_compression_fallback_streak',
 'get_compression_ineffective_count',
 'get_compression_lock_holder',
 'get_compression_overload_streak',
 'get_compression_recovery_deadline',
 'get_conversation_root',
 'get_handoff_state',
 'get_latest_todo_result',
 'get_message_reactions',
 'get_message_role',
 'get_messages_around',
 'get_messages_as_conversation',
 'get_meta',
 'get_next_title_in_lineage',
 'get_recent_session_model_route',
 'get_resume_conversations',
 'get_resume_message_count',
 'get_session_by_title',
 'get_session_delete_targets',
 'get_session_model_config_value',
 'get_telegram_topic_binding',
 'get_telegram_topic_binding_by_session',
 'has_archived_messages',
 'has_gateway_input_owner',
 'has_pending_handoffs',
 'has_platform_message_id',
 'held_row_mismatches',
 'import_foreign_history',
 'import_moved_session',
 'import_sessions',
 'increment_compression_overload_streak',
 'increment_hygiene_failure_streak',
 'insert_gateway_routing_rows_if_absent',
 'is_explicit_fork_child',
 'is_kanban_owned_session',
 'is_telegram_session_linked_to_topic',
 'is_telegram_topic_mode_enabled',
 'key_profiles_for_chat',
 'latest_conversation_boundary',
 'latest_conversation_role',
 'latest_message_row_id',
 'list_backend_heartbeats',
 'list_cron_job_runs',
 'list_gateway_routing_rows',
 'list_gateway_sessions',
 'list_meta_prefix',
 'list_never_active_keyed_sessions',
 'list_pending_handoffs',
 'list_prune_candidates',
 'list_recent_sessions_bounded',
 'list_recent_user_messages',
 'list_skill_scaffolded_sessions',
 'list_telegram_topic_bindings_for_chat',
 'list_unlinked_telegram_sessions_for_user',
 'load_gateway_routing_entries',
 'logical_size_bytes',
 'maybe_auto_archive',
 'maybe_auto_prune_and_vacuum',
 'optimize_fts',
 'optimize_fts_storage',
 'patch_session_model_config',
 'plan_user_turn_rewind',
 'promote_to_session_reset',
 'prune_empty_ghost_sessions',
 'prune_never_active_keyed_sessions',
 'prune_sessions',
 'prune_stale_heartbeats',
 'publish_compression_child',
 'publish_session_git_metadata',
 'purge_profile_state',
 'purge_stale_tool_call_markers',
 'queue_token_counts',
 'rebuild_fts',
 'reclaim_stale_running_handoffs',
 'record_auxiliary_usage',
 'record_compression_failure_cooldown',
 'record_gateway_session_peer',
 'refresh_compression_lock',
 'refresh_session_turn_lease',
 'register_backend_heartbeat',
 'rekey_legacy_main_sessions',
 'rekey_profile_state',
 'relabel_sessions_to_key_profile',
 'relabel_telegram_topic_rows',
 'release_compression_lock',
 'release_session_turn_lease',
 'reopen_if_explicitly_closed',
 'reopen_orphaned_compression_session',
 'reopen_session',
 'replace_gateway_routing_entries',
 'replace_messages',
 'replace_messages_in_transaction',
 'request_handoff',
 'reset_hygiene_failure_streak',
 'resolve_active_row_id',
 'resolve_session_by_title',
 'restore_compression_failure_cooldown_row',
 'retag_kanban_worker_sessions',
 'retire_gateway_routes',
 'retire_undrained_queue_rows',
 'retry_deferred_fts_recovery',
 'rewind_to_message',
 'rewind_user_turn',
 'rewrite_pruned_rows',
 'rewritten_held_row_ids',
 'sanitize_title',
 'save_gateway_routing_entry',
 'search_messages',
 'search_sessions',
 'search_sessions_by_id',
 'session_count_by_source',
 'session_count_ge',
 'session_gateway_runtime',
 'session_ids_with_model_config_key',
 'session_lifecycle_statuses',
 'session_turn_lease_expires_at',
 'session_unread',
 'session_yolo_enabled',
 'set_auto_title',
 'set_compression_fallback_streak',
 'set_compression_ineffective_count',
 'set_compression_overload_streak',
 'set_compression_recovery_deadline',
 'set_expiry_finalized',
 'set_latest_matching_message_display_kind',
 'set_latest_user_api_content',
 'set_message_api_content',
 'set_message_reaction',
 'set_meta',
 'set_session_archived',
 'set_session_hidden',
 'set_session_pinned',
 'set_session_read',
 'set_session_title_source',
 'set_session_yolo',
 'set_user_message_content',
 'sever_crossed_parents',
 'stamp_legacy_compression_chains',
 'sweep_orphaned_sessions',
 'take_unseen_reactions',
 'touch_session_activity',
 'try_acquire_compression_lock',
 'try_acquire_session_turn_lease',
 'unarchive_recoverable_session',
 'update_conversation_model_config_value',
 'update_session_billing_route',
 'update_session_cwd',
 'update_session_meta',
 'update_session_model',
 'update_session_runtime_lock',
 'update_session_tool_names',
 'update_system_prompt',
 'update_token_counts',
 'usage_totals',
 'vacuum')

class SessionDB:
    def __init__(self, store, *, loop):
        if not loop.is_running():
            raise ValueError("Motor owner loop must be running")
        self._store = store
        self._loop = loop
        self._closed = False

    def _call(self, name, *args, **kwargs):
        if self._closed:
            raise RuntimeError("SessionDB is closed")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Call synchronous SessionDB via asyncio.to_thread")
        if not self._loop.is_running():
            raise RuntimeError("Motor owner loop is not running")
        return asyncio.run_coroutine_threadsafe(
            getattr(self._store, name)(*args, **kwargs), self._loop).result()

    def close(self):
        self._closed = True

    def create_session(self, session_id: str, source: str, **kwargs):
        return self._call('create_session', session_id, source, **kwargs)

    def ensure_session(self, session_id: str, source: str='unknown', model: str | None=None, **kwargs):
        return self._call('ensure_session', session_id, source, model, **kwargs)

    def get_session(self, session_id: str):
        return self._call('get_session', session_id)

    def get_session_list_row(self, session_id: str):
        return self._call('get_session_list_row', session_id)

    def resolve_session_id(self, session_id_or_prefix: str):
        return self._call('resolve_session_id', session_id_or_prefix)

    def list_sessions_rich(self, source: str | None=None, sources: list[str] | None=None, exclude_sources: list[str] | None=None, cwd_prefix: str | None=None, limit: int=20, offset: int=0, include_children: bool=False, min_message_count: int=0, project_compression_tips: bool=True, order_by_last_active: bool=False, include_archived: bool=False, archived_only: bool=False, id_query: str | None=None, search_query: str | None=None, compact_rows: bool=False, include_pinned: bool=False, session_key: str | None=None, include_hidden: bool=False, include_subagents: bool=False, local_created_sources: Sequence[str] | None=None):
        return self._call('list_sessions_rich', source, sources, exclude_sources, cwd_prefix, limit, offset, include_children, min_message_count, project_compression_tips, order_by_last_active, include_archived, archived_only, id_query, search_query, compact_rows, include_pinned, session_key, include_hidden, include_subagents, local_created_sources)

    def resolve_resume_session_id(self, session_id: str):
        return self._call('resolve_resume_session_id', session_id)

    def get_messages(self, session_id: str, include_inactive: bool=False, include_compacted: bool=False, limit: Optional[int]=None, offset: int=0, latest: bool=False, after_id: Optional[int]=None, include_ancestors: bool=False):
        return self._call('get_messages', session_id, include_inactive, include_compacted, limit, offset, latest, after_id, include_ancestors)

    def append_message(self, session_id: str, role: str, content: str | None=None, tool_name: str | None=None, tool_calls: Any=None, tool_call_id: str | None=None, token_count: int | None=None, finish_reason: str | None=None, reasoning: str | None=None, reasoning_content: str | None=None, reasoning_details: Any=None, codex_reasoning_items: Any=None, codex_message_items: Any=None, platform_message_id: str | None=None, observed: bool=False, effect_disposition: Optional[str]=None, _compressed_summary: bool=False, timestamp: Any=None, api_content: Optional[str]=None, display_kind: Optional[str]=None, display_metadata: Optional[dict[str, Any]]=None, compression_lock_holder: Optional[str]=None, turn_lease_holder: Optional[str]=None, turn_lease_ttl_seconds: float=300.0, message_uid: Optional[str]=None):
        return self._call('append_message', session_id, role, content, tool_name, tool_calls, tool_call_id, token_count, finish_reason, reasoning, reasoning_content, reasoning_details, codex_reasoning_items, codex_message_items, platform_message_id, observed, effect_disposition, _compressed_summary, timestamp, api_content, display_kind, display_metadata, compression_lock_holder, turn_lease_holder, turn_lease_ttl_seconds, message_uid)

    def end_session(self, session_id: str, end_reason: str):
        return self._call('end_session', session_id, end_reason)

    def get_compression_chain(self, session_id: str):
        return self._call('get_compression_chain', session_id)

    def get_compression_tip(self, session_id: str):
        return self._call('get_compression_tip', session_id)

    def get_compression_lineage(self, session_id: str):
        return self._call('get_compression_lineage', session_id)

    def set_session_title(self, session_id: str, title: str):
        return self._call('set_session_title', session_id, title)

    def get_session_title(self, session_id: str):
        return self._call('get_session_title', session_id)

    def get_session_title_source(self, session_id: str):
        return self._call('get_session_title_source', session_id)

    def delete_session(self, session_id: str, sessions_dir: Optional[Path]=None, expected_delete_ids: Optional[list[str]]=None, expected_display_messages: Optional[dict[str, list[dict[str, Any]]]]=None, exclude_active_write_guards: bool=False, include_compression_chain: bool=False):
        return self._call('delete_session', session_id, sessions_dir, expected_delete_ids, expected_display_messages, exclude_active_write_guards, include_compression_chain)

    def session_count(self, source: str | None=None, sources: list[str] | None=None, cwd_prefix: str | None=None, min_message_count: int=0, include_archived: bool=False, archived_only: bool=False, exclude_children: bool=False, exclude_sources: list[str] | None=None, include_subagents: bool=False, local_created_sources: Sequence[str] | None=None):
        return self._call('session_count', source, sources, cwd_prefix, min_message_count, include_archived, archived_only, exclude_children, exclude_sources, include_subagents, local_created_sources)

    def message_count(self, session_id: str | None=None):
        return self._call('message_count', session_id)

def _unsupported(name):
    def operation(self, *args, **kwargs):
        raise NotImplementedError(f"SessionDB.{name} is outside M1")
    operation.__name__ = name
    return operation


for _name in UNSUPPORTED_METHODS:
    setattr(SessionDB, _name, _unsupported(_name))
