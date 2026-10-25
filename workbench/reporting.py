import csv
import io
import json
import re
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from flask import abort, g, redirect, render_template, request, send_file
from . import db
from .storage import LOCAL, digest

DEFINITION = '''岗位按稳定 ID 去重；平台按记录 ID。触达=已发起沟通或已发送简历（一次记录只计一次）。任何回复含拒绝；邀约为明确面试邀约。回复率=触达中有回复/触达；约面率=触达中有邀约/触达；回复中的邀约比例=触达中同时回复及邀约/触达中有回复。仅反映当前已记录结果，不推演历史时点。日期批次为首次触达实际日期；任一已发生触达事件缺日期时，首次触达日期按未知处理，不冒充录入日。标签组可重叠，不累加为总体。'''

LEGACY_FIELDS_NOTE = '自 app 1.0.6 起，当前薪资以 jobs.salary（薪资范围文本）为准；salary_min、salary_max 和 city 保留历史值以兼容原数据，新版表单不再维护这些旧字段，不应据旧数值推算当前薪资。'


def first_touch(p):
    dates = []
    for flag, key in [('communicated', 'communication_date'), ('sent', 'sent_date')]:
        if p[flag]:
            if not p[key]:
                return None
            dates.append(p[key])
    return min(dates) if dates else None


def stats(database, filters):
    jobs, _ = database.jobs(filters, paged=False)
    ids = {j['id'] for j in jobs}
    rows = [p for p in database.all('SELECT * FROM platforms') if p['job_id'] in ids and (not filters.get('platform') or p['platform'] == filters['platform'])]
    touched = [p for p in rows if p['communicated'] or p['sent']]
    missing = sum(first_touch(p) is None for p in touched)
    excluded = 0
    if filters.get('date_scope') == 'unknown':
        touched = [p for p in touched if first_touch(p) is None]
    elif filters.get('start') or filters.get('end'):
        from datetime import date
        for key in ['start', 'end']:
            if filters.get(key):
                date.fromisoformat(filters[key])
        if filters.get('start') and filters.get('end') and filters['start'] > filters['end']:
            raise ValueError('起始日期不能晚于结束日期')
        excluded = missing
        touched = [p for p in touched if first_touch(p) is not None and (not filters.get('start') or first_touch(p) >= filters['start']) and (not filters.get('end') or first_touch(p) <= filters['end'])]
    reply = sum(p['reply'] == '已有回复' for p in touched)
    invite = sum(p['invitation'] == '已有明确邀约' for p in touched)
    both = sum(p['reply'] == '已有回复' and p['invitation'] == '已有明确邀约' for p in touched)
    n = len(touched)
    ratio = lambda a, b: f'{a}/{b} = {a/b:.1%}' if b else f'{a}/{b}（无分母）'
    return dict(jobs=len(jobs), platform_records=len(rows), touched=n, sent=sum(p['sent'] for p in touched), reply=reply, invitation=invite, replied_and_invited=both, touched_jobs=len({p['job_id'] for p in touched}), pending_reply=sum(p['reply']=='待核对' for p in touched), pending_invitation=sum(p['invitation']=='待核对' for p in touched), missing_dates=missing, excluded_missing_dates=excluded, reply_rate=ratio(reply,n), invite_rate=ratio(invite,n), reply_invite_rate=ratio(both,reply), stages=dict(Counter(j['stage'] for j in jobs)), cutoff=db.now(), definition=DEFINITION, filters=filters)


def export_analysis(database, store, filters, output):
    if filters.get('job_id'):
        item = database.one('SELECT * FROM jobs WHERE id=?', (int(filters['job_id']),))
        jobs = [item] if item else []
    else:
        jobs, _ = database.jobs(filters, paged=False)
        if filters.get('start') or filters.get('end') or filters.get('date_scope') == 'unknown':
            # Select qualifying jobs by the same cohort definition used by statistics.
            stats(database, dict(filters))
            matching = set()
            for p in database.all('SELECT * FROM platforms'):
                if not (p['communicated'] or p['sent']) or (filters.get('platform') and p['platform']!=filters['platform']):
                    continue
                day = first_touch(p)
                if filters.get('date_scope')=='unknown':
                    qualifies = day is None
                else:
                    qualifies = day is not None and (not filters.get('start') or day>=filters['start']) and (not filters.get('end') or day<=filters['end'])
                if qualifies:
                    matching.add(p['job_id'])
            jobs = [j for j in jobs if j['id'] in matching]
    job_ids = {j['id'] for j in jobs}
    platforms = [p for p in database.all('SELECT * FROM platforms') if p['job_id'] in job_ids]
    jt = [r for r in database.all('SELECT * FROM job_tags') if r['job_id'] in job_ids]
    jr = [r for r in database.all('SELECT * FROM job_resumes') if r['job_id'] in job_ids]
    jm = [r for r in database.all('SELECT * FROM job_materials') if r['job_id'] in job_ids]
    materials = [m for m in database.all('SELECT * FROM materials') if m['id'] in {r['material_id'] for r in jm}]
    note_attachments = [a for a in database.all('SELECT * FROM job_attachments') if a['job_id'] in job_ids]
    material_attachments = [a for a in database.all('SELECT * FROM material_attachments') if a['material_id'] in {m['id'] for m in materials}]
    resume_ids = {r['resume_id'] for r in jr} | {p['resume_id'] for p in platforms if p['resume_id']}
    resumes = [r for r in database.all('SELECT * FROM resumes') if r['id'] in resume_ids]
    files = [f for f in database.all('SELECT * FROM files') if f['id'] in {r['file_id'] for r in resumes} | {m['file_id'] for m in materials if m['file_id']} | {a['file_id'] for a in note_attachments + material_attachments}]
    missing = [f['original_name'] for f in files if not (store.current / f['path']).is_file() or digest(store.current / f['path']) != f['hash']]
    if missing:
        raise ValueError('分析导出未完成：下列应有原件缺失或损坏，请恢复后重试：' + '、'.join(missing))
    paths, used = {}, set()
    for f in files:
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', f['original_name']).strip('. ') or ('原件.' + f['ext'])
        base = 'originals/' + f['hash'][:8] + '_' + filename
        name, index = base, 1
        while name.casefold() in used:
            name = 'originals/' + f['hash'][:8] + '_' + str(index) + '_' + filename
            index += 1
        used.add(name.casefold())
        paths[f['id']] = name
    provided = []
    for m in materials:
        attached = [a for a in material_attachments if a['material_id']==m['id']]
        has_body, has_file = bool(m['body']), bool(m['file_id'] or attached)
        provided.append(dict(material_id=m['id'], title=m['title'], original_name=m.get('original_name'), content='正文及原件均有' if has_body and has_file else '含文本正文' if has_body else '仅原件', body_path=f"bodies/material-{m['id']}.md" if has_body else None, file_path=paths.get(m['file_id']), job_ids=[r['job_id'] for r in jm if r['material_id']==m['id']]))
        provided[-1]['attachments'] = [dict(a, content='仅原件', file_path=paths[a['file_id']]) for a in attached]
    manifest = dict(format_version=1, app_version=db.VERSION, schema_version=db.SCHEMA, created_at=db.now(), status='complete', scope=filters, scope_note='筛选选择岗位，包内包含所选岗位的全部平台事实及关联资料；日期批次只决定岗位入选，不截断档案。', job_ids=sorted(job_ids), definition=DEFINITION, files=[dict(f, export_path=paths[f['id']]) for f in files], materials=provided, material_content_counts=dict(Counter(m['content'] for m in provided)), unique_material_count=len(materials))
    manifest['legacy_fields_note'] = LEGACY_FIELDS_NOTE
    manifest['job_note_attachments'] = [dict(a, content='仅原件', file_path=paths[a['file_id']]) for a in note_attachments]
    manifest['job_note_attachment_note'] = '备注附件单独列出，不计入面试资料提供情况的分母；图片和 PDF 原件保留，不自动提取正文。已收起附件保留状态和原件。'
    manifest['material_attachments'] = [dict(a, content='仅原件', file_path=paths[a['file_id']]) for a in material_attachments]
    manifest['material_attachment_note'] = '材料图片／补充附件计入所属材料的原件提供情况；分母仍为唯一材料 ID，不按图片数增加材料数量。收起附件保留状态和原件，不自动提取正文。'
    tags = [t for t in database.all('SELECT * FROM tags') if t['id'] in {r['tag_id'] for r in jt}]
    history = [h for h in database.all('SELECT * FROM history') if h['job_id'] in job_ids]
    data = dict(manifest=manifest, jobs=jobs, platforms=platforms, tags=tags, job_tags=jt, resumes=resumes, job_resumes=jr, materials=materials, job_materials=jm, job_attachments=note_attachments, material_attachments=material_attachments, history=history)
    lines = ['# 求职分析包', '', f'范围：{json.dumps(filters,ensure_ascii=False)}', f'截止：{manifest["created_at"]}', '', DEFINITION, '', '附件内容不自动解析；仅原件为合法提供方式，不判断外部 Agent 读取能力。材料提供情况以唯一材料 ID 去重，不包含 JD、简历和 CSV。', '', '此包用于分析；业务恢复请使用完整备份 ZIP。', manifest['scope_note']]
    for j in jobs:
        lines += ['', f"## 岗位 #{j['id']} {j['company']} / {j['title']}", '', '```json', json.dumps(j,ensure_ascii=False,indent=2), '```', '', '### JD 原文', j['jd'] or '未填写', '', '### 平台事实']
        lines += [json.dumps(p,ensure_ascii=False) for p in platforms if p['job_id']==j['id']]
        lines += ['准备简历：' + str([r['resume_id'] for r in jr if r['job_id']==j['id']]), '关联材料：' + str([r['material_id'] for r in jm if r['job_id']==j['id']])]
        lines += ['### 备注附件（仅原件）'] + [f"{a['original_name']}：{paths[a['file_id']]}" + ('（已收起）' if a['hidden'] else '') for a in note_attachments if a['job_id']==j['id']]
    lines.insert(2, LEGACY_FIELDS_NOTE)
    for m, p in zip(materials, provided):
        lines += ['', f"## 材料 #{m['id']} {m['title']}", f"类型：{m['kind']}；提供情况：{p['content']}；关联岗位：{p['job_ids']}", f"正文：{p['body_path'] or '无'}；原件：{p['file_path'] or '无'}", '', m['body']]
        lines += ['### 图片／补充附件（仅原件）'] + [f"{a['original_name']}：{a['file_path']}" + ('（已收起）' if a['hidden'] else '') for a in p['attachments']]
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr('relations.json', json.dumps(data, ensure_ascii=False, indent=2))
        z.writestr('summary.md', '\n'.join(lines))
        for name, rows in [('jobs',jobs),('platforms',platforms),('materials',materials),('resumes',resumes),('job_attachments',note_attachments),('material_attachments',material_attachments)]:
            text = io.StringIO(newline='')
            columns = list(rows[0]) if rows else ['id']
            writer = csv.DictWriter(text, columns)
            writer.writeheader()
            for row in rows:
                # Keep exact originals in JSON; CSV protects spreadsheet formula interpretation.
                writer.writerow({k: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for k,v in row.items()})
            z.writestr(name+'.csv', text.getvalue().encode('utf-8-sig'))
        for m, p in zip(materials,provided):
            if p['body_path']:
                z.writestr(p['body_path'], m['body'])
        for f in files:
            z.write(store.current / f['path'], paths[f['id']])
    return manifest


def register(app):
    store = app.extensions['store']
    helpers = app.extensions['helpers']

    @app.get('/analysis')
    def analysis():
        error, result = None, None
        try:
            result = stats(g.db, request.args.to_dict())
        except ValueError as exc:
            error = str(exc)
        return render_template('analysis.html', result=result, filters=request.args.to_dict(), error=error)

    @app.get('/export')
    def export():
        output = tempfile.SpooledTemporaryFile(max_size=5_000_000, mode='w+b')
        try:
            export_analysis(g.db, store, request.args.to_dict(), output)
            output.seek(0)
            response = send_file(output, as_attachment=True, download_name='求职分析包.zip', mimetype='application/zip')
            response.call_on_close(output.close)
            return response
        except Exception as exc:
            output.close()
            return render_template('export_error.html', error=str(exc)), 409

    @app.route('/backup', methods=['GET','POST'])
    def backup():
        error, preview, token = None, None, None
        if request.method == 'POST':
            try:
                helpers['check_epoch']()
                operation = request.form.get('operation')
                if operation == 'manual':
                    destination = request.form.get('destination','').strip()
                    # Manual copy publication is also idempotent.
                    def action():
                        folder = store.backup('manual')
                        if destination:
                            dest = Path(destination).resolve()
                            if not dest.is_dir() or dest.is_relative_to(store.root):
                                raise ValueError('另存位置应为数据目录之外的现有目录；也可下载后使用浏览器另存为')
                            output = dest / (folder.name + '.zip')
                            with output.open('xb') as f:
                                store.zip_backup(folder, f)
                        return helpers['feedback']('/backup','手动完整备份已创建' + ('，并已另存到 ' + destination if destination else ''))
                    # Backup must not run inside a database write transaction.
                    previous = g.db.one('SELECT * FROM request_log WHERE request_id=?', (request.form.get('request_id'),))
                    if previous:
                        return redirect(helpers['save'](lambda: ''))
                    ref = action()
                    return redirect(helpers['save'](lambda: ref))
                if operation == 'preview':
                    upload = request.files.get('backup')
                    if not upload or not upload.filename:
                        raise ValueError('请选择完整备份 ZIP')
                    folder = store.unpack(upload.stream)
                    preview = store.validate(folder)
                    token = folder.name
                elif operation == 'restore':
                    token = request.form.get('token','')
                    if not re.fullmatch('[a-f0-9]{32}', token):
                        raise ValueError('恢复预览标识无效，请重新选择备份')
                    if request.form.get('confirmed') != '1':
                        raise ValueError('请确认覆盖范围后恢复')
                    folder = store.staging / token
                    # Close this request's connection; all others are excluded by coordination lock.
                    g.db.c.close()
                    g.db = None
                    store.restore(folder)
                    from flask import session
                    session.clear()
                    return redirect(helpers['feedback']('/backup','恢复完成，已重新读取资料；恢复前完整备份已保留'))
            except Exception as exc:
                error = helpers['error_text'](exc)
                if g.db is None:
                    g.db = db.Database(store.path)
        backups = store.list_backups()
        auxiliary=[dict(location=str(p),size=sum(f.stat().st_size for f in p.rglob('*') if f.is_file())) for p in store.root.iterdir() if p.is_dir() and p.name.startswith(('previous-','interrupted-'))]
        return render_template('backup.html', backups=backups, total_size=sum(b['size'] for b in backups), auxiliary=auxiliary, data_path=str(store.root), preview=preview, token=token, error=error)

    @app.get('/backup/<ident>/download')
    def download_backup(ident):
        if not re.fullmatch(r'[0-9]{8}T[0-9]{6}-[a-f0-9]{12}', ident):
            abort(404)
        folder = store.backups / ident
        try:
            store.validate(folder)
        except Exception as exc:
            return render_template('export_error.html', error=str(exc)), 409
        output = tempfile.SpooledTemporaryFile(max_size=5_000_000, mode='w+b')
        store.zip_backup(folder, output)
        output.seek(0)
        response = send_file(output, as_attachment=True, download_name='完整备份-'+ident+'.zip', mimetype='application/zip')
        response.call_on_close(output.close)
        return response
