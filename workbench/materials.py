import bleach
import markdown
from markupsafe import Markup
from flask import abort, g, redirect, render_template, request
from . import db
from . import attachments
from .validation import page_number


def rendered_markdown(source):
    def allowed(tag, name, value):
        if tag == 'a' and name == 'href':
            return value.startswith(('#', 'https://', 'http://', 'mailto:'))
        return name in ('title',) or (tag in ('td', 'th') and name in ('align',))
    html = markdown.markdown(source or '', extensions=['tables', 'fenced_code'])
    cleaned = bleach.clean(html, tags=['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','td','th','del'], attributes=allowed, protocols=['http','https','mailto'], strip=True)
    return Markup(cleaned.replace('<a ', '<a rel="noopener noreferrer" '))


def register(app):
    app.add_template_filter(rendered_markdown, 'markdown')
    store = app.extensions['store']
    helpers = app.extensions['helpers']
    save, back, feedback, error_text = [helpers[k] for k in ['save', 'safe_back', 'feedback', 'error_text']]
    record = app.extensions['record_helpers']

    @app.get('/materials')
    def materials():
        q, kind = request.args.get('q', ''), request.args.get('kind', '')
        page=page_number(request.args.get('page'))
        rows,total = g.db.paginate('''SELECT m.id,m.title,m.kind,m.file_id,(length(m.body)>0) body,m.updated_at,m.original_name,EXISTS(SELECT 1 FROM material_attachments a WHERE a.material_id=m.id) has_attachments FROM materials m LEFT JOIN files f ON f.id=m.file_id
        WHERE (m.title LIKE ? ESCAPE '!' OR EXISTS(SELECT 1 FROM job_materials jm JOIN jobs j ON j.id=jm.job_id WHERE jm.material_id=m.id AND (j.company LIKE ? ESCAPE '!' OR j.title LIKE ? ESCAPE '!'))) AND (?='' OR m.kind=?) ORDER BY m.id DESC''', (*[db.literal_like(q)]*3, kind, kind),page)
        return render_template('materials.html', rows=rows, q=q, kind=kind,page=page,total=total)

    @app.route('/materials/new', methods=['GET', 'POST'])
    @app.route('/materials/<int:ident>/edit', methods=['GET', 'POST'])
    def material_edit(ident=None):
        old = g.db.one('SELECT * FROM materials WHERE id=?', (ident,)) if ident else None
        if ident and not old:
            abort(404)
        linked = [r['job_id'] for r in g.db.all('SELECT job_id FROM job_materials WHERE material_id=?', (ident,))] if ident else ([int(request.args['job_id'])] if request.args.get('job_id') else [])
        values = dict(old or dict(kind=request.args.get('kind', '背调')))
        error = None
        if request.method == 'POST':
            values.update(request.form.to_dict())
            linked = list(dict.fromkeys(int(x) for x in request.form.getlist('jobs')))
            try:
                title, kind = request.form.get('title', '').strip(), request.form.get('kind')
                body = request.form.get('body', '')
                if not title or kind not in db.KINDS:
                    raise ValueError('请填写标题并选择资料类型')
                if not linked:
                    raise ValueError('请至少关联一个岗位；共享分析可勾选多个')
                info = None
                upload = request.files.get('file')
                if upload and upload.filename:
                    info, imported_body = store.put(upload)
                    if imported_body is not None and request.form.get('import_body') == '1':
                        body = imported_body
                        values['body'] = body
                prepared = attachments.prepare(store, request)
                if not body.strip() and not info and not (old and old['file_id']) and not prepared[0] and not (ident and attachments.rows(g.db, ident, 'material')):
                    raise ValueError('正文和附件至少保存一种；自由复盘不要求固定提纲')
                def action():
                    fields = dict(title=title, kind=kind, body=body, source=request.form.get('source', ''), updated_at=db.now(), file_id=record['file_record'](info) if info else old['file_id'] if old else None, original_name=info['original_name'] if info else old['original_name'] if old else '')
                    previous = [r['job_id'] for r in g.db.all('SELECT job_id FROM job_materials WHERE material_id=?', (ident,))] if ident else []
                    if ident:
                        g.db.update('materials', ident, int(request.form['row_version']), fields)
                        mid = ident
                    else:
                        mid = g.db.insert('materials', dict(fields, created_at=db.now()))
                    g.db.execute('DELETE FROM job_materials WHERE material_id=?', (mid,))
                    for jid in linked:
                        g.db.insert('job_materials', dict(job_id=jid, material_id=mid))
                    for jid in set(linked + previous):
                        g.db.changes(jid, '材料', mid, dict(old or {}, jobs=previous), dict(fields, jobs=linked))
                    attachments.apply(g.db, mid, prepared, 'material', linked + previous)
                    return feedback(back('/materials/' + str(mid)) if ident else '/materials/' + str(mid))
                return redirect(save(action, dict(document=dict(hash=info['hash'], name=info['original_name']) if info else None, attachments=prepared[3])))
            except Exception as exc:
                error = error_text(exc)
        return render_template('material_form.html', item=values, jobs=record['job_choices'](), linked=linked, error=error, back=back('/materials/' + str(ident) if ident else '/materials'), material_attachments=attachments.rows(g.db, ident, 'material') if ident else []), (409 if error and '已被修改' in error else 200)

    @app.get('/materials/<int:ident>')
    def material_detail(ident):
        item = g.db.one('SELECT m.*,f.ext FROM materials m LEFT JOIN files f ON f.id=m.file_id WHERE m.id=?', (ident,))
        if not item:
            abort(404)
        linked = g.db.all('SELECT j.* FROM jobs j JOIN job_materials jm ON jm.job_id=j.id WHERE jm.material_id=?', (ident,))
        return render_template('material_detail.html', item=item, jobs=linked, back=back('/materials'), material_attachments=attachments.rows(g.db, ident, 'material'))
