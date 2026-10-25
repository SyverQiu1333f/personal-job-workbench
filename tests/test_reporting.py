import io
import json
import zipfile
from pathlib import Path

from workbench import db
from workbench.reporting import stats, export_analysis
from test_jobs import env, job, post


def test_statistics_and_complete_export(env, tmp_path):
    app, c, csrf = env
    job(c, csrf, tags='甲,乙')
    job(c, csrf, **{'continue':'1'})
    d = db.Database(app.extensions['store'].path)
    d.execute("UPDATE platforms SET communicated=1,communication_date='2026-10-01',reply='已有回复',reply_kind='明确拒绝' WHERE id=1")
    d.execute("UPDATE platforms SET sent=1,invitation='已有明确邀约' WHERE id=2")
    d.insert('platforms',dict(job_id=1,platform='第二平台',sent=1,sent_date='2026-10-02',reply='已有回复',invitation='已有明确邀约',created_at=db.now(),updated_at=db.now()))
    d.c.commit()
    result = stats(d,{})
    assert result['jobs'] == 2 and result['touched'] == 3
    assert result['reply'] == 2 and result['invitation'] == 2 and result['replied_and_invited'] == 1
    assert result['reply_rate'].startswith('2/3')
    assert stats(d,{'start':'2026-10-01','end':'2026-10-01'})['touched'] == 1
    assert stats(d,{'date_scope':'unknown'})['touched'] == 1
    assert stats(d,{'platform':'第二平台'})['touched'] == 1
    assert stats(d,{'tags':'甲,乙'})['jobs'] == 1
    assert post(c,csrf,'/materials/new',title='共享正文',kind='外部分析结果',jobs=['1','2'],body='原始\n正文',file=(io.BytesIO(b'%PDF-1.4\n material'),'附件.pdf')).status_code == 302
    assert post(c,csrf,'/materials/new',title='只有正文',kind='真实面试复盘',jobs=['1'],body='自由文本').status_code == 302
    assert post(c,csrf,'/materials/new',title='只有原件',kind='背调',jobs=['2'],file=(io.BytesIO(b'%PDF-1.4\n other'),'附件.pdf')).status_code == 302
    assert post(c,csrf,'/resumes/new',name='最终简历',jobs=['1'],file=(io.BytesIO(b'%PDF-1.4\n resume'),'附件.pdf')).status_code == 302
    output = io.BytesIO()
    manifest = export_analysis(d,app.extensions['store'],{'archived':'all'},output)
    assert manifest['unique_material_count'] == 3
    assert manifest['material_content_counts'] == {'正文及原件均有':1,'含文本正文':1,'仅原件':1}
    with zipfile.ZipFile(output) as z:
        for entry in manifest['files']:
            assert entry['export_path'] in z.namelist()
        assert len({f['export_path'] for f in manifest['files']}) == 3
        assert '原始\n正文' in z.read('summary.md').decode()
        data = json.loads(z.read('relations.json'))
        assert len(data['job_materials']) == 4
    for path in ['/','/analysis','/analysis?date_scope=unknown','/backup','/export?archived=all','/export?job_id=1']:
        assert c.get(path).status_code == 200
    # Missing original is an explicit incomplete export, never a success ZIP.
    f = d.one('SELECT * FROM files ORDER BY id LIMIT 1')
    (app.extensions['store'].current / f['path']).unlink()
    response = c.get('/export?archived=all')
    assert response.status_code == 409 and '缺失'.encode() in response.data
    d.c.close()


def test_restore_preview_and_switch(env):
    app,c,csrf=env
    job(c,csrf)
    result = post(c,csrf,'/backup',operation='manual')
    assert result.status_code==302
    store=app.extensions['store']
    backup=next(b for b in store.list_backups() if b['purpose']=='manual')
    zipped=c.get('/backup/'+backup['backup_id']+'/download').data
    job(c,csrf,**{'continue':'1'})
    response=post(c,csrf,'/backup',operation='preview',backup=(io.BytesIO(zipped),'完整备份.zip'))
    assert '校验通过'.encode() in response.data
    import re
    token=re.search(rb'name="token" value="([a-f0-9]+)"',response.data).group(1).decode()
    response=post(c,csrf,'/backup',operation='restore',token=token,confirmed='1')
    assert response.status_code==302
    d=db.Database(store.path)
    assert d.one('SELECT count(*) n FROM jobs')['n']==1
    assert any(b['purpose']=='pre-restore' for b in store.list_backups())
    d.c.close()
