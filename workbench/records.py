import uuid
import io
from datetime import date
from pathlib import Path

from flask import abort, g, redirect, render_template, request, send_file
from . import db
from . import attachments
from .storage import IMAGE_MIMES
from .validation import page_number

READING = ['未知', '未读', '已读']
REPLY = ['待核对', '截至核对日暂无回复', '已有回复']
INVITATION = ['待核对', '截至核对日暂无邀约', '已有明确邀约']


def register(app):
    store = app.extensions['store']
    helpers = app.extensions['helpers']
    save, back, feedback, error_text = [helpers[k] for k in ['save', 'safe_back', 'feedback', 'error_text']]

    def file_record(info):
        existing = g.db.one('SELECT id FROM files WHERE path=?', (info['path'],))
        return existing['id'] if existing else g.db.insert('files', info)

    def job_choices():
        return g.db.all('SELECT id,company,title,archived FROM jobs ORDER BY archived,star DESC,created_at DESC,id DESC')

    @app.route('/jobs/<int:job_id>/platforms/new', methods=['GET', 'POST'])
    @app.route('/platforms/<int:ident>/edit', methods=['GET', 'POST'])
    def platform_edit(job_id=None, ident=None):
        old = g.db.one('SELECT * FROM platforms WHERE id=?', (ident,)) if ident else None
        if ident and not old:
            abort(404)
        job_id = old['job_id'] if old else job_id
        job = g.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job:
            abort(404)
        values = dict(old or dict(reading='未知', reply='待核对', invitation='待核对'), stage=job['stage'], job_note=job['note'], job_version=job['row_version'])
        error = None
        if request.method == 'POST':
            values.update(request.form.to_dict())
            values['communicated'] = int(request.form.get('communicated')=='1')
            values['sent'] = int(request.form.get('sent')=='1')
            try:
                fields = {k: request.form.get(k, '') for k in ['platform', 'url', 'reading', 'reply', 'reply_kind', 'invitation', 'note']}
                for k in ['communicated', 'sent']:
                    fields[k] = int(request.form.get(k) == '1')
                for k in ['communication_date', 'sent_date', 'reply_date', 'invitation_date']:
                    value = request.form.get(k, '')
                    if value:
                        date.fromisoformat(value)
                    fields[k] = value or None
                if fields['reading'] not in READING or fields['reply'] not in REPLY or fields['invitation'] not in INVITATION:
                    raise ValueError('请选择有效的事实状态')
                if fields['reply_kind'] not in ['', '继续沟通', '明确拒绝', '其他／待判断']:
                    raise ValueError('回复性质无效')
                stage = request.form.get('stage')
                if stage not in db.STAGES:
                    raise ValueError('请选择有效的整体阶段')
                fields['resume_id'] = int(request.form['resume_id']) if request.form.get('resume_id') else None
                if fields['resume_id'] and not g.db.one('SELECT id FROM resumes WHERE id=?', (fields['resume_id'],)):
                    raise ValueError('指定的实际发送简历不存在')
                for scope in ['reading', 'reply', 'invitation']:
                    if request.form.get('check_all') == '1' or request.form.get('check_' + scope) == '1':
                        fields[scope + '_checked'] = db.now()
                if request.form.get('check_all') == '1':
                    fields['checked_at'] = db.now()
                fields['updated_at'] = db.now()
                prepared = attachments.prepare(store, request)
                def action():
                    job_fields = dict(stage=stage, note=request.form.get('job_note', ''), updated_at=db.now())
                    g.db.update('jobs', job_id, int(request.form['job_version']), job_fields)
                    if ident:
                        g.db.update('platforms', ident, int(request.form['row_version']), fields)
                        pid = ident
                    else:
                        pid = g.db.insert('platforms', dict(fields, job_id=job_id, created_at=db.now()))
                    g.db.changes(job_id, '岗位', job_id, job, job_fields)
                    g.db.changes(job_id, '平台', pid, old, fields)
                    attachments.apply(g.db, job_id, prepared)
                    return helpers['job_feedback'](back(), job_id)
                return redirect(save(action, prepared[3]))
            except Exception as exc:
                error = error_text(exc)
        return render_template('platform_form.html', item=values, job=job, error=error, reading=READING, reply=REPLY, invitation=INVITATION, resumes=g.db.all('SELECT * FROM resumes ORDER BY hidden,id DESC'), back=back(), note_attachments=attachments.rows(g.db, job_id)), (409 if error and '已被修改' in error else 200)

    @app.get('/file/<int:ident>')
    def file_open(ident):
        info = g.db.one('SELECT * FROM files WHERE id=?', (ident,))
        if not info:
            abort(404)
        path = store.current / info['path']
        if not path.resolve().is_relative_to((store.current / 'files').resolve()):
            abort(400, '原件路径无效')
        if not path.is_file():
            return render_template('error.html', error='原件缺失，请从完整备份恢复，未报告文件读取成功', fields={}, action='', back='/backup'), 404
        name = info['original_name']
        for key,table in [('resume_id','resumes'),('material_id','materials'),('job_attachment_id','job_attachments'),('material_attachment_id','material_attachments')]:
            if request.args.get(key):
                business = g.db.one(f'SELECT original_name FROM {table} WHERE id=? AND file_id=?',(request.args[key],ident))
                if business and business['original_name']:
                    name = business['original_name']
        # Copy under coordination, so an HTTP/PDF range stream cannot hold the active
        # Windows file handle open during a restore generation switch.
        content = io.BytesIO(path.read_bytes())
        response = send_file(content, as_attachment=request.args.get('download') == '1' or info['ext'] not in ['pdf',*IMAGE_MIMES], download_name=name, mimetype='application/pdf' if info['ext'] == 'pdf' else IMAGE_MIMES.get(info['ext'],'application/octet-stream'), conditional=True)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/resumes')
    def resumes():
        q, tag = request.args.get('q', ''), request.args.get('tag', '')
        page=page_number(request.args.get('page'))
        rows,total = g.db.paginate('''SELECT DISTINCT r.*,f.hash FROM resumes r JOIN files f ON f.id=r.file_id
        WHERE (?='all' OR r.hidden=0) AND (r.name LIKE ? ESCAPE '!' OR EXISTS(SELECT 1 FROM jobs j WHERE (j.company LIKE ? ESCAPE '!' OR j.title LIKE ? ESCAPE '!') AND (EXISTS(SELECT 1 FROM job_resumes jr WHERE jr.job_id=j.id AND jr.resume_id=r.id) OR EXISTS(SELECT 1 FROM platforms p WHERE p.job_id=j.id AND p.resume_id=r.id))))
        AND (?='' OR EXISTS(SELECT 1 FROM job_tags jt JOIN tags t ON t.id=jt.tag_id WHERE t.name=? AND (EXISTS(SELECT 1 FROM job_resumes jr WHERE jr.job_id=jt.job_id AND jr.resume_id=r.id) OR EXISTS(SELECT 1 FROM platforms p WHERE p.job_id=jt.job_id AND p.resume_id=r.id)))) ORDER BY r.id DESC''', (request.args.get('hidden', ''), *[db.literal_like(q)] * 3, tag, tag),page)
        return render_template('resumes.html', rows=rows, q=q, tag=tag,page=page,total=total)

    @app.route('/resumes/new', methods=['GET', 'POST'])
    @app.route('/resumes/<int:ident>/edit', methods=['GET', 'POST'])
    def resume_edit(ident=None):
        old = g.db.one('SELECT * FROM resumes WHERE id=?', (ident,)) if ident else None
        if ident and not old:
            abort(404)
        linked = [r['job_id'] for r in g.db.all('SELECT job_id FROM job_resumes WHERE resume_id=?', (ident,))] if ident else ([int(request.args['job_id'])] if request.args.get('job_id') else [])
        values, error, duplicates = dict(old or {}), None, []
        if request.method == 'POST':
            values.update(request.form.to_dict())
            linked = [int(x) for x in request.form.getlist('jobs')]
            try:
                name = request.form.get('name', '').strip()
                if not name:
                    raise ValueError('请输入简历名称')
                info = None
                if not ident:
                    upload = request.files.get('file')
                    if not upload or not upload.filename:
                        raise ValueError('请选择最终 PDF')
                    info, _ = store.put(upload, resume=True)
                    duplicates = g.db.all('SELECT r.* FROM resumes r JOIN files f ON f.id=r.file_id WHERE f.hash=?', (info['hash'],))
                    replay = g.db.one('SELECT request_id FROM request_log WHERE request_id=?', (request.form.get('request_id'),))
                    if duplicates and request.form.get('continue') != '1' and not replay:
                        raise ValueError('相同内容已存在，可打开下列简历复用；若需独立命名请勾选继续并重新选文件')
                def action():
                    fields = dict(name=name, note=request.form.get('note', ''), hidden=int(request.form.get('hidden') == '1'), updated_at=db.now())
                    previous_links = [r['job_id'] for r in g.db.all('SELECT job_id FROM job_resumes WHERE resume_id=?', (ident,))] if ident else []
                    if ident:
                        g.db.update('resumes', ident, int(request.form['row_version']), fields)
                        rid = ident
                    else:
                        rid = g.db.insert('resumes', dict(fields, file_id=file_record(info), original_name=info['original_name'], created_at=db.now()))
                    g.db.execute('DELETE FROM job_resumes WHERE resume_id=?', (rid,))
                    for jid in linked:
                        g.db.insert('job_resumes', dict(job_id=jid, resume_id=rid))
                    for jid in set(linked + previous_links):
                        g.db.changes(jid, '简历', rid, dict(old or {}, jobs=previous_links), dict(fields, jobs=linked))
                    return feedback(back('/resumes/' + str(rid)) if ident else '/resumes/' + str(rid))
                return redirect(save(action, dict(hash=info['hash'],name=info['original_name']) if info else None))
            except Exception as exc:
                error = error_text(exc)
        return render_template('resume_form.html', item=values, linked=linked, jobs=job_choices(), error=error, duplicates=duplicates, back=back('/resumes/' + str(ident) if ident else '/resumes'))

    @app.get('/resumes/<int:ident>')
    def resume_detail(ident):
        item = g.db.one('SELECT r.*,f.hash FROM resumes r JOIN files f ON f.id=r.file_id WHERE r.id=?', (ident,))
        if not item:
            abort(404)
        jobs = g.db.all('SELECT j.* FROM jobs j JOIN job_resumes jr ON jr.job_id=j.id WHERE jr.resume_id=?', (ident,))
        sent = g.db.all('SELECT p.*,j.company,j.title FROM platforms p JOIN jobs j ON j.id=p.job_id WHERE p.resume_id=? ORDER BY p.id DESC', (ident,))
        return render_template('resume_detail.html', item=item, jobs=jobs, sent=sent, back=back('/resumes'))

    app.extensions['record_helpers'] = dict(file_record=file_record, job_choices=job_choices)
