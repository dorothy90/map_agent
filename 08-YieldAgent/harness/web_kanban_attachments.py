"""Durable Hermes card attachments from authenticated chat artifacts."""
from contextlib import closing
from functools import partial
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
from urllib.parse import quote, urlsplit

from bson import ObjectId
from pydantic import Field, model_validator

from .artifact_delivery import artifact_url
from .types import Contract
from .web_kanban import _board_path

ATTACHMENT_TOOLS = ('kanban_attach', 'kanban_attach_url', 'kanban_attachments')


class Attach(Contract):
    task_id: str = Field(min_length=1)
    artifact_id: str | None = Field(default=None, description="Exact artifact_id of a file in this chat.")
    output_path: str | None = Field(default=None, description="Exact /outputs/ path of a generated file in this chat.")

    @model_validator(mode='after')
    def one_file(self):
        if bool(self.artifact_id) == bool(self.output_path):
            raise ValueError('Supply one artifact_id or output_path from this chat.')
        return self


class AttachUrl(Contract):
    task_id: str = Field(min_length=1)
    url: str = Field(description='Exact relative /harness/artifacts/ link from this chat.')


class Attachments(Contract):
    task_id: str = Field(min_length=1)


def public_attachment(task_id, row):
    path = f"/outputs/kanban/{task_id}~{row['id']}/{quote(row['filename'], safe='')}"
    return dict(attachment_id=row['id'], filename=row['filename'], size=row['size'],
                content_type=row['content_type'], output_path=path, media=f'MEDIA:"{path}"')


def open_download(profile, database, path):
    """Return a verified native card file handle. The gateway owns and closes it."""
    parts = path.split('/')
    if len(parts) != 5 or parts[:3] != ['', 'outputs', 'kanban']:
        raise LookupError('Attachment unavailable')
    task_id, _, number = parts[3].partition('~')
    if not number.isascii() or not number.isdecimal() or len(number) > 18:
        raise LookupError('Attachment unavailable')
    db_file = _board_path(profile, database)
    if not db_file.is_file():
        raise LookupError('Attachment unavailable')
    with closing(sqlite3.connect(db_file.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT a.* FROM task_attachments a JOIN tasks t ON t.id=a.task_id '
                           'WHERE a.task_id=? AND a.id=?', (task_id, int(number))).fetchone()
    if row is None or public_attachment(task_id, row)['output_path'] != path:
        raise LookupError('Attachment unavailable')
    src = Path(row['stored_path'])
    if src.parent != db_file.parent / 'attachments' / task_id or src.resolve() != src:
        raise LookupError('Attachment unavailable')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        stream = os.fdopen(os.open(src, flags), 'rb')
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != row['size']:
            stream.close()
            raise LookupError('Attachment unavailable')
    except OSError:
        raise LookupError('Attachment unavailable') from None
    return dict(filename=row['filename'], size=row['size'], content_type=row['content_type']), stream


async def read_chat_artifact(store, run, cmd, cap):
    field, value = ('artifact_id', cmd.artifact_id) if cmd.artifact_id else ('output_path', cmd.output_path)
    found = await store.results.find_one({
        'principal_id': run['principal_id'], 'session_id': run['session_id'],
        'observation.artifact_refs.' + field: value,
    }, {'observation.artifact_refs': 1})
    if found is None:
        raise LookupError('No such artifact in this chat.')
    ref = next(r for r in found['observation']['artifact_refs'] if r.get(field) == value)
    blob = await store.blobs.open_download_stream(ObjectId(ref['artifact_id']))
    try:
        if blob.length > cap:
            raise ValueError(f'Attachment exceeds the {cap // (1024 * 1024)} MB limit.')
        return ref, await blob.read(cap + 1)
    finally:
        blob.close()


def install_attachment_tools(registry, profile, database, run, *, read_source, publish):
    from hermes_cli import kanban_db as kb
    from hermes_cli.auth import _file_lock
    from hermes_cli.kanban_db_connect import connect_closing
    from tools.registry import tool_error
    from utils import atomic_write_bytes

    def handle(model, args, **kwargs):
        try:
            cmd = model.model_validate(args)
            if isinstance(cmd, AttachUrl):
                blob = urlsplit(cmd.url).path.rsplit('/', 1)[-1]
                if not ObjectId.is_valid(blob) or cmd.url != artifact_url(run['session_id'], blob):
                    raise ValueError('Use an artifact link returned by this chat.')
                cmd = Attach(task_id=cmd.task_id, artifact_id=blob)
            path = _board_path(profile, database)
            if not path.is_file():
                raise LookupError('No such item on this employee board.')
            with connect_closing(db_path=path) as conn:
                if kb.get_task(conn, cmd.task_id) is None:
                    raise LookupError('No such item on this employee board.')
                if isinstance(cmd, Attach):
                    ref, data = read_source(cmd, kb.KANBAN_ATTACHMENT_MAX_BYTES)
                    if len(data) > kb.KANBAN_ATTACHMENT_MAX_BYTES:
                        raise ValueError('Attachment exceeds the Hermes size limit.')
                    folder = path.parent / 'attachments' / cmd.task_id
                    if folder.resolve() != folder:
                        raise ValueError('Invalid card attachment directory.')
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    dest = folder / str(ObjectId(ref['artifact_id']))
                    filename = kb._safe_attachment_name(ref['title'])
                    # The same chat artifact may be attached concurrently or replayed after a lost reply.
                    with _file_lock(path.with_suffix('.attachments.lock'), threading.local(), 15, 'Board is busy.'):
                        old = conn.execute('SELECT id FROM task_attachments WHERE task_id=? AND stored_path=?',
                                           (cmd.task_id, str(dest))).fetchone()
                        if old is None:
                            atomic_write_bytes(dest, data, mode=0o600, fsync_dir=True)
                            try:
                                attachment_id = kb.add_attachment(conn, cmd.task_id, filename=filename, stored_path=str(dest),
                                    content_type=ref.get('mime'), size=len(data), uploaded_by=run['principal_id'])
                            except Exception:
                                if not conn.execute('SELECT 1 FROM task_attachments WHERE stored_path=?',
                                                    (str(dest),)).fetchone():
                                    dest.unlink(missing_ok=True)
                                raise
                        else:
                            attachment_id = old['id']
                    return json.dumps(dict(ok=True, task_id=cmd.task_id, attachment_id=attachment_id,
                                           filename=filename, size=len(data)), ensure_ascii=False)
                rows = kb.list_attachments(conn, cmd.task_id)
                files = [public_attachment(cmd.task_id, vars(a)) for a in rows]
                for f in files:
                    publish(f['output_path'], f['media'])
                return json.dumps(dict(ok=True, task_id=cmd.task_id, attachments=files), ensure_ascii=False)
        except (ValueError, LookupError, TimeoutError) as exc:
            return tool_error(str(exc))

    for name, model, desc in [
        ('kanban_attach', Attach, 'Attach an existing file, report, image or export from this chat to a saved '
         'personal Kanban defect item. Use its existing artifact_id or /outputs/ path. Maximum 25 MB.'),
        ('kanban_attach_url', AttachUrl, 'Attach a stored file using its exact relative artifact download URL '
         'from this chat. External URLs are unavailable.'),
        ('kanban_attachments', Attachments, 'List a saved personal Kanban item’s files, including those attached '
         'in other chats. Return the provided MEDIA references so the user can download them.'),
    ]:
        registry.register(name=name, toolset='web_kanban', schema=dict(name=name, description=desc,
            parameters=model.model_json_schema()), description=desc, handler=partial(handle, model),
            override=True, emoji='📎')
