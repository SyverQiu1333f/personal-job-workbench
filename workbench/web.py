import hashlib
import json
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qsl, urlunsplit

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session
from . import db
from .storage import Store, LOCAL
from .validation import InputError, page_number, optional_integer
from .choices import REGIONS, PLATFORMS
from .navigation import module_parent
from . import attachments

JOB_TEXT = ['company', 'title', 'source', 'url', 'jd', 'salary', 'city', 'region', 'address', 'direct', 'commute_note', 'stage', 'end_reason', 'note']


def create_app(data_dir=None):
    app = Flask(__name__)
    store = Store(data_dir or Path(__file__).resolve().parent.parent / 'data')
    secret = store.root / 'session.key'
    if not secret.exists():
        secret.write_bytes(secrets.token_bytes(32))
    app.config.update(APP_VERSION=db.VERSION, STARTED_AT=db.now(), SECRET_KEY=secret.read_bytes(), SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_HTTPONLY=True, MAX_FORM_MEMORY_SIZE=None, MAX_FORM_PARTS=20000)
    app.extensions['store'] = store
    from .presentation import history_value, breadcrumb_trail, salary_range, history_summaries
    app.add_template_filter(history_value, 'history_value')
    app.add_template_filter(salary_range, 'salary_range')
    app.add_template_filter(history_summaries, 'history_summaries')

    @app.before_request
    def before():
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(400, '仅允许本机访问')
        if request.path == '/healthz' or request.path.startswith('/static/'):
            return
        if store.maintenance:
            return '正在恢复，请稍候', 503
        if not store.lock.acquire(timeout=30):
            return '有操作正在进行，请稍后重试', 503
        g.locked = True
        # A request may have waited on the lock while shutdown/restore began.
        if store.maintenance:
            return '正在关闭或恢复，请稍候', 503
        g.db = db.Database(store.path)
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(24)
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', ''), session['csrf']):
            abort(400, '表单已过期，请刷新页面；原资料未修改')

    @app.teardown_request
    def teardown(error):
        if getattr(g, 'db', None):
            g.db.c.close()
        if getattr(g, 'locked', False):
            store.lock.release()

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(exc):
        return render_template('export_error.html', error=error_text(exc)), 503

    @app.errorhandler(InputError)
    def input_error(exc):
        return render_template('request_error.html', title='请检查输入', message=str(exc)), 400

    @app.errorhandler(400)
    def bad_request(exc):
        message = exc.description
        if not any('\u4e00' <= ch <= '\u9fff' for ch in message):
            message = '请求内容不正确或表单已过期，请返回页面检查后重试。'
        return render_template('request_error.html', title='请求未完成', message=message), 400

    @app.errorhandler(500)
    def unexpected_error(exc):
        return render_template('request_error.html', title='暂时无法完成操作', message='处理时发生异常，请返回页面核对最新资料后重试。详细原因已记录到应用日志；不要重复假定本次已保存。'), 500

    @app.context_processor
    def context():
        def page_url(page):
            conditions = {k: v for k, v in request.args.items() if k != 'saved'}
            return request.path + '?' + urlencode(dict(conditions, page=page))
        clean_url = request.path + ('?' + urlencode({k:v for k,v in request.args.items() if k!='saved'}) if any(k!='saved' for k in request.args) else '')
        reset_url = '/jobs?' + urlencode({'view': request.args.get('view', '')}) if request.endpoint == 'jobs' else request.path
        search_enabled = request.endpoint in ['jobs', 'resumes', 'materials', 'analysis']
        labels = dict(q='关键词', tags='标签', tag='关联标签', stage='状态', platform='平台', reply='回复', invitation='邀约', region='区域', fact='进展范围', kind='类型', hidden='简历范围', archived='岗位范围', start='日期起', end='日期止', date_scope='日期范围', star='星标', sort='排序')
        descriptions = {'archived': {'all': '含归档', '1': '仅归档'}, 'hidden': {'all': '包含收起'}, 'date_scope': {'unknown': '仅日期未知'}, 'fact': {'sent': '已发送', 'touched': '已触达'}, 'sort': {'oldest': '最早收录在前'}, 'star': {'1': '仅星标'}}
        summary = []
        for key, label in labels.items():
            value = request.args.get(key, '')
            if not value or (key, value) in [('archived', '0'), ('date_scope', 'all')]:
                continue
            summary.append(label + '：' + descriptions.get(key, {}).get(value, value))
        if request.args.get('tag_mode') == 'all' and (request.args.get('tags') or request.args.get('tag')):
            summary.append('标签全部匹配')
        return dict(stages=db.STAGES, kinds=db.KINDS, regions=REGIONS, platforms_choices=PLATFORMS, field_labels=db.FIELD_LABELS, new_id=lambda: uuid.uuid4().hex, csrf=session.get('csrf', ''), data_epoch=store.epoch, app_version=db.VERSION, current_url=clean_url, page_url=page_url, reset_url=reset_url, breadcrumb_trail=breadcrumb_trail, search_enabled=search_enabled, filter_summary=summary if search_enabled else [], navigation_parent=module_parent)

    def check_epoch():
        if request.form.get('data_epoch') != store.epoch:
            raise db.Conflict('资料已恢复或切换，旧表单不能覆盖当前数据。请另开页面读取最新资料后重新提交。')

    @app.template_filter('localtime')
    def localtime(value):
        if not value:
            return '尚未核对'
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(LOCAL).strftime('%Y-%m-%d %H:%M')

    def save(action, extra=None):
        check_epoch()
        values = {k: request.form.getlist(k) for k in sorted(request.form) if k != 'csrf'}
        hashed = hashlib.sha256(json.dumps([values, extra], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return g.db.save(store, request.form.get('request_id'), hashed, action)

    def safe_back(default='/jobs'):
        back = request.values.get('back', default)
        if not back.startswith('/') or back.startswith('//') or urlsplit(back).netloc or '\\' in back:
            return default
        parts = urlsplit(back)
        return urlunsplit(('', '', parts.path, urlencode([(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k!='saved']), parts.fragment))

    def feedback(back, text='已保存'):
        base, sep, anchor = back.partition('#')
        return base + ('&' if '?' in base else '?') + urlencode({'saved': text}) + (sep + anchor if sep else '')

    def job_feedback(back, ident):
        parts = urlsplit(back)
        text = '已保存'
        if parts.path == '/jobs':
            where, args = g.db.job_filter(dict(parse_qsl(parts.query, keep_blank_values=True)))
            if not g.db.one('SELECT j.id FROM jobs j WHERE j.id=? AND '+where, [ident,*args]):
                text = '已保存，岗位现已移出当前筛选结果；条件与页码保持不变'
        return feedback(back, text)

    def error_text(exc):
        logging.exception('保存失败')
        if isinstance(exc, sqlite3.OperationalError) and ('locked' in str(exc) or 'busy' in str(exc)):
            return '数据库忙，请稍后重试。当前输入已保留，资料未被覆盖。'
        if isinstance(exc, (InputError, db.Conflict)):
            return str(exc)
        if isinstance(exc, sqlite3.IntegrityError):
            return '名称或关联资料存在冲突，请核对最新资料后重试。'
        if isinstance(exc, ValueError):
            message = str(exc)
            if message and '\u4e00' <= message[0] <= '\u9fff':
                return message
            return '输入格式不正确，请检查日期、数字和所选记录后重试。'
        if isinstance(exc, OSError):
            return '文件或备份保存失败，请检查磁盘空间、文件是否存在及目录权限后重试。'
        return '保存未完成，请核对最新资料后重试。详细原因已记录到日志。'

    def job_values(old=None):
        # Removed controls must never blank out legacy city or numeric salary data.
        values = {k: request.form.get(k, '').strip() if k not in ['jd', 'note'] else request.form.get(k, '') for k in JOB_TEXT if k != 'city'}
        if 'company_size' in request.form or not old:
            values['company_size'] = request.form.get('company_size', '').strip()
        if not values['company'] or not values['title']:
            raise ValueError('公司和岗位名称不能为空')
        if values['stage'] not in db.STAGES or values['direct'] not in ['未知', '是', '否']:
            raise ValueError('请选择有效阶段和通勤选项')
        values['commute'] = optional_integer(request.form.get('commute'), '单程通勤分钟')
        # Accept legacy forms explicitly carrying the old numeric fields; modern
        # forms expose just salary. Omitted old columns keep their stored values.
        for key, label in [('salary_min', '薪资数值下限'), ('salary_max', '薪资数值上限')]:
            if key in request.form:
                values[key] = optional_integer(request.form.get(key), label)
        low = values.get('salary_min', (old or {}).get('salary_min'))
        high = values.get('salary_max', (old or {}).get('salary_max'))
        if ('salary_min' in values or 'salary_max' in values) and low is not None and high is not None and low > high:
            raise ValueError('薪资下限不能大于上限')
        values.update(star=int(request.form.get('star') == '1'), updated_at=db.now())
        return values

    @app.get('/healthz')
    def health():
        return jsonify(app_id='personal-job-workbench', workspace_id=store.workspace_id, app_version=db.VERSION, schema_version=db.SCHEMA, pid=os.getpid(), started_at=app.config['STARTED_AT'])

    @app.get('/')
    def index():
        from .reporting import stats
        result = stats(g.db, {})
        counts = g.db.one("SELECT count(*) records,coalesce(sum(p.communicated=1 OR p.sent=1),0) touched,coalesce(sum(p.sent),0) sent,coalesce(sum(p.reply='已有回复'),0) reply,coalesce(sum(p.invitation='已有明确邀约'),0) invitation FROM platforms p JOIN jobs j ON j.id=p.job_id WHERE j.archived=0")
        starred = g.db.jobs({'view':'star'})[0][:6]
        return render_template('home.html', result=result, counts=counts, starred=starred)

    @app.get('/jobs')
    def jobs():
        filters = request.args.to_dict()
        if filters.pop('apply_filters', None) == '1':
            # A new search uses the three visible fields and the current view scope.
            # Old advanced conditions remain readable via links, but never hide in a new search.
            filters = {k: v for k, v in filters.items() if k in {'view', 'q', 'tags', 'stage', 'archived', 'fact', 'tag_mode'}}
            if filters.get('view') == 'interview' and filters.get('stage') != '面试中':
                filters['view'] = ''
            return redirect('/jobs?' + urlencode(filters or {'view': ''}))
        if not filters:
            preference = g.db.one("SELECT value FROM preferences WHERE key='job_filters'")
            if preference and preference['value']:
                restored = dict(parse_qsl(preference['value'], keep_blank_values=True))
                if 'page' in restored:
                    try:
                        restored['page'] = str(page_number(restored['page']))
                    except InputError:
                        restored['page'] = '1'
                return redirect('/jobs?' + urlencode(restored))
        page = page_number(filters.get('page'))
        if 'page' in filters:
            filters['page'] = str(page)
        clean = {k: v for k, v in filters.items() if k != 'saved'}
        pref = urlencode(clean)
        old = g.db.one("SELECT value FROM preferences WHERE key='job_filters'")
        error = None
        if not old or old['value'] != pref:
            try:
                store.daily()
                g.db.execute("INSERT INTO preferences(key,value) VALUES('job_filters',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,row_version=row_version+1", (pref,))
                g.db.c.commit()
            except Exception as exc:
                g.db.c.rollback()
                error = '本次列表条件未能持久保存：' + error_text(exc)
        rows, total = g.db.jobs(filters)
        platform_map = {r['id']: g.db.all('SELECT * FROM platforms WHERE job_id=? ORDER BY id', (r['id'],)) for r in rows}
        scope_notes = []
        for key, label in [('platform', '平台'), ('reply', '回复'), ('invitation', '邀约'), ('region', '区域')]:
            if filters.get(key):
                scope_notes.append(label + '：' + filters[key])
        if filters.get('star') == '1':
            scope_notes.append('仅星标')
        if filters.get('archived') == 'all':
            scope_notes.append('含已归档岗位')
        elif filters.get('archived') == '1' and filters.get('view') != 'archived':
            scope_notes.append('仅已归档岗位')
        if filters.get('fact') in ['sent', 'touched']:
            scope_notes.append('已发送简历' if filters['fact'] == 'sent' else '已触达')
        if filters.get('sort') == 'oldest':
            scope_notes.append('最早收录在前')
        return render_template('jobs.html', rows=rows, total=total, filters=filters, scope_notes=scope_notes, platform_map=platform_map, page=page, error=error, back='/jobs?' + pref)

    @app.route('/jobs/new', methods=['GET', 'POST'])
    @app.route('/jobs/<int:ident>/edit', methods=['GET', 'POST'])
    def edit_job(ident=None):
        old = g.db.one('SELECT * FROM jobs WHERE id=?', (ident,)) if ident else None
        if ident and not old:
            abort(404)
        tags = ','.join(r['name'] for r in g.db.all('SELECT t.name FROM tags t JOIN job_tags jt ON jt.tag_id=t.id WHERE jt.job_id=?', (ident,))) if ident else ''
        values = dict(old or dict(stage='待准备', city='宁波', direct='未知'), tags=tags)
        if old:
            values['salary'] = salary_range(old)
        duplicates, error = [], None
        if request.method == 'POST':
            values.update(request.form.to_dict())
            values['star'] = int(request.form.get('star') == '1')
            try:
                fields = job_values(old)
                prepared = attachments.prepare(store, request)
                if not ident:
                    from difflib import SequenceMatcher
                    candidates = g.db.all('SELECT id,company,title,archived,url FROM jobs')
                    duplicates = [r for r in candidates if (fields['url'] and fields['url'] == r['url']) or SequenceMatcher(None, (fields['company'] + fields['title']).casefold().replace(' ', ''), (r['company'] + r['title']).casefold().replace(' ', '')).ratio() >= .80]
                    replay = g.db.one('SELECT request_id FROM request_log WHERE request_id=?', (request.form.get('request_id'),))
                    if duplicates and request.form.get('continue') != '1' and not replay:
                        return render_template('job_form.html', item=values, duplicates=duplicates, error=None, back=safe_back())
                def action():
                    if ident:
                        g.db.update('jobs', ident, int(request.form['row_version']), fields)
                        jid = ident
                    else:
                        jid = g.db.insert('jobs', dict(fields, created_at=db.now()))
                        g.db.insert('platforms', dict(job_id=jid, platform=fields['source'], url=fields['url'], created_at=db.now(), updated_at=db.now()))
                    g.db.changes(jid, '岗位', jid, old, fields)
                    g.db.changes(jid, '岗位', jid, {'tags': tags}, {'tags': request.form.get('tags', '')})
                    g.db.set_tags(jid, request.form.get('tags', ''))
                    attachments.apply(g.db, jid, prepared)
                    return job_feedback(safe_back(), jid)
                return redirect(save(action, prepared[3]))
            except Exception as exc:
                error = error_text(exc)
        return render_template('job_form.html', item=values, duplicates=duplicates, error=error, back=safe_back(), platforms=g.db.all('SELECT * FROM platforms WHERE job_id=? ORDER BY id', (ident,)) if ident else [], note_attachments=attachments.rows(g.db, ident) if ident else []), (409 if error and '已被修改' in error else 200)

    @app.get('/jobs/<int:ident>')
    def job_detail(ident):
        item = g.db.one('SELECT * FROM jobs WHERE id=?', (ident,))
        if not item:
            abort(404)
        return render_template('job_detail.html', item=item, tags=g.db.all('SELECT t.* FROM tags t JOIN job_tags jt ON jt.tag_id=t.id WHERE jt.job_id=?', (ident,)), platforms=g.db.all('SELECT * FROM platforms WHERE job_id=? ORDER BY id', (ident,)), history=g.db.all('SELECT * FROM history WHERE job_id=? ORDER BY id DESC', (ident,)), resumes=g.db.all('SELECT r.* FROM resumes r JOIN job_resumes jr ON jr.resume_id=r.id WHERE jr.job_id=?', (ident,)), materials=g.db.all('SELECT m.* FROM materials m JOIN job_materials jm ON jm.material_id=m.id WHERE jm.job_id=? ORDER BY m.id DESC', (ident,)), back=safe_back(), note_attachments=attachments.rows(g.db, ident))

    @app.post('/jobs/<int:ident>/flag')
    def job_flag(ident):
        try:
            key = request.form['field']
            if key not in ['star', 'archived']:
                raise ValueError('字段无效')
            old = g.db.one('SELECT * FROM jobs WHERE id=?', (ident,))
            def action():
                values = {key: int(request.form['value']), 'updated_at': db.now()}
                if values[key] not in [0, 1]:
                    raise ValueError('值无效')
                g.db.update('jobs', ident, int(request.form['row_version']), values)
                g.db.changes(ident, '岗位', ident, old, values)
                return job_feedback(safe_back(), ident)
            return redirect(save(action))
        except Exception as exc:
            return render_template('error.html', error=error_text(exc), fields=request.form.to_dict(), action=request.path, back=safe_back()), 409

    @app.route('/tags', methods=['GET', 'POST'])
    def tags():
        error = None
        if request.method == 'POST':
            try:
                name = request.form.get('name', '').strip()
                if not name:
                    raise ValueError('标签名不能为空')
                def action():
                    ident = int(request.form['id'])
                    old = g.db.one('SELECT * FROM tags WHERE id=?', (ident,))
                    g.db.update('tags', ident, int(request.form['row_version']), {'name': name})
                    for row in g.db.all('SELECT job_id FROM job_tags WHERE tag_id=?', (ident,)):
                        g.db.changes(row['job_id'], '标签', ident, old, {'name': name})
                    return feedback('/tags')
                return redirect(save(action))
            except Exception as exc:
                error = error_text(exc)
        return render_template('tags.html', rows=g.db.all('SELECT * FROM tags ORDER BY name'), error=error, entered=request.form)

    app.extensions['helpers'] = dict(save=save, safe_back=safe_back, feedback=feedback, error_text=error_text, job_feedback=job_feedback, check_epoch=check_epoch)
    from .records import register as register_records
    register_records(app)
    from .materials import register as register_materials
    register_materials(app)
    from .reporting import register as register_reporting
    register_reporting(app)
    from .pdf_preview import register as register_pdf_preview
    register_pdf_preview(app)
    return app
