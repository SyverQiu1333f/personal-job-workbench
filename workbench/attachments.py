"""Job-note originals share immutable file storage; edits use the job guard."""
from . import db


def target(scope):
    return {'job': ('job_attachments', 'job_id', '备注附件'),
            'material': ('material_attachments', 'material_id', '资料附件')}[scope]


def rows(database, owner_id, scope='job'):
    table, column, _ = target(scope)
    return database.all(f'SELECT a.*,f.ext,f.size,f.hash FROM {table} a JOIN files f ON f.id=a.file_id WHERE a.{column}=? ORDER BY a.hidden,a.id DESC', (owner_id,))


def prepare(store, request):
    uploaded = []
    for upload in request.files.getlist('note_files'):
        if upload and upload.filename:
            try:
                info, _ = store.put(upload, note=True)
            except ValueError as exc:
                raise ValueError('备注附件“' + upload.filename + '”：' + str(exc))
            uploaded.append(info)
    try:
        hidden = {int(value) for value in request.form.getlist('hide_attachments')}
        restored = {int(value) for value in request.form.getlist('restore_attachments')}
        if any(value <= 0 for value in hidden | restored) or hidden & restored:
            raise ValueError()
    except ValueError:
        raise ValueError('附件选择无效，请刷新后重新确认')
    payload = dict(files=[dict(hash=f['hash'], name=f['original_name']) for f in uploaded])
    return uploaded, hidden, restored, payload


def apply(database, owner_id, prepared, scope='job', job_ids=None):
    table, column, entity = target(scope)
    affected_jobs = [owner_id] if scope == 'job' else sorted(set(job_ids or []))
    def changes(ident, old, values):
        for jid in affected_jobs:
            database.changes(jid, entity, ident, old, values)
    uploaded, hidden, restored, _ = prepared
    existing = {row['id']: row for row in database.all(f'SELECT * FROM {table} WHERE {column}=?', (owner_id,))}
    if (hidden | restored) - existing.keys():
        raise ValueError('所选附件不属于当前记录，请刷新后重试')
    for ident in hidden | restored:
        value = int(ident in hidden)
        old = existing[ident]
        if old['hidden'] != value:
            database.execute(f'UPDATE {table} SET hidden=? WHERE id=?', (value, ident))
            changes(ident, old, dict(hidden=value))
    for info in uploaded:
        found = database.one('SELECT id FROM files WHERE path=?', (info['path'],))
        fid = found['id'] if found else database.insert('files', info)
        previous = database.one(f'SELECT * FROM {table} WHERE {column}=? AND file_id=?', (owner_id, fid))
        if previous:
            if previous['hidden']:
                database.execute(f'UPDATE {table} SET hidden=0 WHERE id=?', (previous['id'],))
                changes(previous['id'], previous, dict(hidden=0))
            continue
        values = dict(file_id=fid, original_name=info['original_name'], hidden=0, created_at=db.now(), **{column: owner_id})
        ident = database.insert(table, values)
        changes(ident, None, values)
