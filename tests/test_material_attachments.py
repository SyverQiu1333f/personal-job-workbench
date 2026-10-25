import hashlib
import io
import json
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from workbench import db
from workbench.storage import Store
from test_jobs import env, job, post
from test_note_attachments import image, files, legacy_two


def material(c, csrf, **extra):
    values = dict(title='图片备注', kind='真实面试复盘', jobs=['1'], body='')
    values.update(extra)
    return post(c, csrf, '/materials/new', **values)


def edit(c, csrf, version=1, **extra):
    values = dict(title='图片备注', kind='真实面试复盘', jobs=['1'], body='', row_version=str(version))
    values.update(extra)
    return post(c, csrf, '/materials/1/edit', **values)


@pytest.mark.parametrize('kind', db.KINDS)
def test_material_kinds_can_save_only_images_with_optional_document(env, kind):
    app, c, csrf = env
    job(c, csrf)
    raw = image()
    assert material(c, csrf, kind=kind, note_files=files((raw,'截图.png'))).status_code == 302
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT body,file_id FROM materials') == dict(body='', file_id=None)
    assert d.one('SELECT count(*) n FROM material_attachments')['n'] == 1
    page = c.get('/materials/1').data.decode()
    assert '截图.png' in page and 'material_attachment_id=1' in page and '放大查看' in page
    assert c.get('/file/1?material_attachment_id=1').data == raw
    assert 'note-images' not in c.get('/materials').data.decode()
    assert '仅原件' in c.get('/materials').data.decode()
    pdf = (Path(__file__).parent/'fixtures/样例简历.pdf').read_bytes()
    assert edit(c, csrf, kind=kind, body='文字和文档继续保留', file=(io.BytesIO(pdf),'原有文档.pdf')).status_code == 302
    assert edit(c, csrf, version=2, kind=kind, body='文字和文档继续保留', note_files=files((image('JPEG'),'再补一张.jpg'))).status_code == 302
    item = d.one('SELECT * FROM materials')
    assert item['body'] == '文字和文档继续保留' and item['original_name'] == '原有文档.pdf'
    assert d.one('SELECT count(*) n FROM material_attachments')['n'] == 2
    assert '/preview/' in c.get('/materials/1').data.decode()
    d.c.close()


def test_material_idempotency_shared_files_and_stale_guard(env):
    app, c, csrf = env
    raw = image()
    job(c, csrf, note_files=files((raw,'岗位图.png')))
    job(c, csrf, **{'continue':'1'})
    key = uuid.uuid4().hex
    def create():
        return c.post('/materials/new',data=dict(csrf=csrf,data_epoch=app.extensions['store'].epoch,request_id=key,
            title='共享图片资料',kind='模拟面试',jobs=['1','2'],note_files=files((raw,'资料图.png'))))
    assert create().status_code == 302 and create().status_code == 302
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT count(*) n FROM files')['n'] == 1
    assert d.one('SELECT count(*) n FROM materials')['n'] == 1
    assert d.one('SELECT count(*) n FROM material_attachments')['n'] == 1
    assert d.one('SELECT count(*) n FROM job_materials')['n'] == 2
    assert c.get('/file/1?material_attachment_id=1&download=1').headers['Content-Disposition'] != c.get('/file/1?job_attachment_id=1&download=1').headers['Content-Disposition']
    assert edit(c,csrf,jobs=['1','2'],hide_attachments='1').status_code == 302
    assert 'note-images' not in c.get('/materials/1').data.decode()
    assert 'note-images' in c.get('/jobs/1').data.decode()
    assert edit(c,csrf,jobs=['1','2'],body='旧表单不能覆盖',note_files=files((image('JPEG'),'旧表单.jpg'))).status_code == 409
    assert d.one('SELECT body FROM materials')['body'] == ''
    assert edit(c,csrf,version=2,jobs=['1','2'],restore_attachments='1').status_code == 302
    assert edit(c,csrf,version=3,jobs=['1','2'],note_files=files((raw,'重复.png'))).status_code == 302
    assert d.one('SELECT count(*) n FROM material_attachments')['n'] == 1
    assert {r['job_id'] for r in d.all("SELECT DISTINCT job_id FROM history WHERE entity='资料附件'")} == {1,2}
    d.c.close()


def test_material_failure_and_foreign_attachment_keep_inputs_and_originals(env):
    app, c, csrf = env
    job(c, csrf)
    assert material(c,csrf,note_files=files((image(),'已有.png'))).status_code == 302
    assert material(c,csrf,title='另一份',note_files=files((image('JPEG'),'其他.jpg'))).status_code == 302
    bad = edit(c,csrf,body='应保留的备注',hide_attachments='2',note_files=files((image('WEBP'),'待选.webp')))
    assert '不属于当前记录' in bad.data.decode() and '应保留的备注' in bad.data.decode()
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT row_version,body FROM materials WHERE id=1') == dict(row_version=1,body='')
    assert d.one('SELECT count(*) n FROM material_attachments')['n'] == 2
    bad = edit(c,csrf,body='损坏时保留正文',hide_attachments='1',note_files=files((b'broken','坏图.png')))
    assert '图片无法识别' in bad.data.decode() and '损坏时保留正文' in bad.data.decode()
    assert 'name="hide_attachments" value="1" checked' in bad.data.decode()
    bad = edit(c,csrf,body='大文件时保留正文',note_files=files((b'%PDF-'+b'0'*50_000_000,'超限.pdf')))
    assert '50MB' in bad.data.decode() and '大文件时保留正文' in bad.data.decode()
    assert d.one('SELECT count(*) n FROM files')['n'] == 2
    assert not list(app.extensions['store'].staging.iterdir())
    d.c.close()


def test_material_export_denominator_complete_restore_and_integrity(env,tmp_path):
    app,c,csrf=env
    raw=image()
    job(c,csrf,note_files=files((raw,'岗位截图.png')))
    job(c,csrf,**{'continue':'1'})
    pdf=(Path(__file__).parent/'fixtures/两页样例.pdf').read_bytes()
    assert material(c,csrf,jobs=['1','2'],note_files=files((raw,'仅图片.png'),(pdf,'补充.pdf'))).status_code==302
    assert material(c,csrf,title='正文和图片',body='自由正文',note_files=files((raw,'正文图.png'))).status_code==302
    assert edit(c,csrf,jobs=['1','2'],hide_attachments='1').status_code==302
    for scope in ['job_id=1','q=宁波测试','archived=all']:
        with zipfile.ZipFile(io.BytesIO(c.get('/export?'+scope).data)) as z:
            m=json.loads(z.read('manifest.json'))
            assert m['unique_material_count']==2
            assert m['material_content_counts']=={'仅原件':1,'正文及原件均有':1}
            assert len(m['material_attachments'])==3 and len(m['files'])==2
            assert len(m['materials'][0]['attachments'])==2
            assert m['materials'][0]['file_path'] is None
            assert len(json.loads(z.read('relations.json'))['material_attachments'])==3
            assert 'material_attachments.csv' in z.namelist()
            assert '已收起' in z.read('summary.md').decode()
            for f in m['files']:
                assert hashlib.sha256(z.read(f['export_path'])).hexdigest()==f['hash']
    store=app.extensions['store']
    folder=store.backup()
    assert store.validate(folder)['counts']['material_attachments']==3
    zipped=tmp_path/'资料图片完整包.zip'
    store.zip_backup(folder,zipped)
    restored=Store(tmp_path/'独立图片恢复目录')
    restored.restore(restored.unpack(zipped))
    a,b=db.Database(store.path),db.Database(restored.path)
    tables=[r['name'] for r in a.all("SELECT name FROM sqlite_master WHERE type='table' AND name!='db_meta'")]
    for table in tables:
        assert a.all('SELECT * FROM '+table)==b.all('SELECT * FROM '+table)
    for f in db.referenced_files(b):
        assert hashlib.sha256((restored.current/f['path']).read_bytes()).hexdigest()==f['hash']
    f=a.one("SELECT * FROM files WHERE ext='png'")
    (store.current/f['path']).unlink()
    assert c.get('/export?job_id=1').status_code==409
    with pytest.raises(FileNotFoundError):store.backup()
    a.c.close();b.c.close()


def legacy_three(root):
    legacy_two(root)
    c=sqlite3.connect(root/'current/db.sqlite3')
    for statement in db.NOTE_DDL:c.execute(statement)
    c.execute("UPDATE db_meta SET value='3' WHERE key='schema_version'")
    raw=image()
    digest=hashlib.sha256(raw).hexdigest()
    rel=f'files/{digest[:2]}/{digest}.png'
    (root/'current'/rel).parent.mkdir(parents=True)
    (root/'current'/rel).write_bytes(raw)
    c.execute('INSERT INTO files(hash,ext,path,size,original_name,created_at) VALUES(?,?,?,?,?,?)',(digest,'png',rel,len(raw),'旧岗位截图.png','旧时间'))
    c.execute("INSERT INTO job_attachments(job_id,file_id,original_name,created_at) VALUES(1,1,'旧岗位截图.png','旧时间')")
    c.commit();c.close()


def test_schema_three_job_images_survive_upgrade_restore_and_failed_migration(tmp_path):
    root=tmp_path/'结构三'
    legacy_three(root)
    store=Store(root)
    d=db.Database(store.path)
    assert d.one('SELECT count(*) n FROM job_attachments')['n']==1
    assert d.one('SELECT count(*) n FROM material_attachments')['n']==0
    original=next(b for b in store.list_backups() if b['purpose']=='pre-migrate')
    assert original['schema_version']==3 and len(Store.validate(original['location'])['files'])==1
    restored=Store(tmp_path/'旧结构三恢复')
    staging=restored.staging/'old'
    shutil.copytree(original['location'],staging)
    restored.restore(staging)
    r=db.Database(restored.path)
    assert r.all('SELECT * FROM job_attachments')==d.all('SELECT * FROM job_attachments')
    r.c.close();d.c.close()
    broken=tmp_path/'升级故障'
    legacy_three(broken)
    with patch.object(db,'MATERIAL_NOTE_DDL',[db.MATERIAL_NOTE_DDL[0],'INVALID SQL']):
        with pytest.raises(RuntimeError,match='迁移失败'):Store(broken)
    d=db.Database(broken/'current/db.sqlite3')
    assert d.one("SELECT value FROM db_meta WHERE key='schema_version'")['value']=='3'
    assert not db.has_table(d,'material_attachments')
    assert d.one('SELECT count(*) n FROM job_attachments')['n']==1
    d.c.close()
