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
from PIL import Image
from werkzeug.datastructures import FileStorage
from workbench import db
from workbench.storage import Store
from test_jobs import env, post


def job(c, csrf, **values):
    fields = dict(company='宁波测试', title='产品经理', stage='待准备', direct='未知')
    fields.update(values)
    return post(c, csrf, '/jobs/new', **fields)


def image(fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (480, 300), (47, 94, 113)).save(output, format=fmt)
    return output.getvalue()


def files(*items):
    return [(io.BytesIO(raw), name) for raw, name in items]


def edit(c, csrf, version=1, **values):
    return post(c, csrf, '/jobs/1/edit', company='宁波测试', title='产品经理',
                stage='待准备', direct='未知', row_version=str(version), **values)


def test_multiple_formats_detail_and_scope(env):
    app, c, csrf = env
    pdf = (Path(__file__).parent/'fixtures/两页样例.pdf').read_bytes()
    inputs = [(image(), '微信截图.png'), (image('JPEG'), '招聘信息.JPEG'),
              (image('WEBP'), '图片.webp'), (pdf, '补充.pdf')]
    assert job(c, csrf, note='补充文字', note_files=files(*inputs)).status_code == 302
    d = db.Database(app.extensions['store'].path)
    rows = d.all('SELECT * FROM job_attachments ORDER BY id')
    assert len(rows) == 4
    detail = c.get('/jobs/1').data.decode()
    assert 'note-images' in detail and '/preview/4' in detail
    assert all(name in detail for _, name in inputs)
    assert 'note-images' not in c.get('/jobs?view=').data.decode()
    for row, (raw, name) in zip(rows, inputs):
        result = c.get(f"/file/{row['file_id']}?job_attachment_id={row['id']}")
        assert result.data == raw
        assert result.headers['Content-Disposition'].startswith('inline;')
        assert result.headers['X-Content-Type-Options'] == 'nosniff'
        assert c.get(f"/file/{row['file_id']}?job_attachment_id={row['id']}&download=1").headers['Content-Disposition'].startswith('attachment;')
    assert c.get('/file/1').mimetype == 'image/png'
    assert c.get('/file/2').mimetype == 'image/jpeg'
    assert c.get('/file/3').mimetype == 'image/webp'
    assert c.get('/preview/4').status_code == 200
    assert post(c, csrf, '/preview-data/4', offset='0').is_json
    assert 'data-note-paste' in c.get('/platforms/1/edit', headers={'HX-Request': 'true'}).data.decode()
    d.c.close()


def test_atomic_failure_size_and_content_validation(env):
    app, c, csrf = env
    job(c, csrf)
    raw = image()
    result = edit(c, csrf, note='保留输入', note_files=files((raw, '有效.png'), (b'bad', '损坏.png')))
    assert '保留输入' in result.data.decode() and '图片无法识别' in result.data.decode()
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT note,row_version FROM jobs') == dict(note='', row_version=1)
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 0
    assert d.one('SELECT count(*) n FROM files')['n'] == 0
    result = edit(c, csrf, note='大文件文字保留', note_files=files((b'%PDF-'+b'0'*50_000_000, '超过上限.pdf')))
    assert '50MB' in result.data.decode() and '大文件文字保留' in result.data.decode()
    result = edit(c, csrf, note_files=files((raw, '伪装.jpg')))
    assert '格式不匹配' in result.data.decode()
    assert not list(app.extensions['store'].staging.iterdir())
    assert d.one('SELECT row_version FROM jobs')['row_version'] == 1
    # Existing resume/material boundaries are unchanged.
    assert '仅支持 PDF' in post(c, csrf, '/resumes/new', name='错误', file=(io.BytesIO(raw), '图.png')).data.decode()
    assert '材料支持' in post(c, csrf, '/materials/new', title='错误', kind='背调', jobs=['1'], file=(io.BytesIO(raw), '图.png')).data.decode()
    d.c.close()


def test_shared_file_idempotency_conflict_hide_and_restore(env):
    app, c, csrf = env
    raw = image('JPEG')
    key = uuid.uuid4().hex
    def create():
        return c.post('/jobs/new', data=dict(csrf=csrf, data_epoch=app.extensions['store'].epoch,
            request_id=key, company='共享公司', title='岗位', stage='待准备', direct='未知',
            note_files=files((raw, '第一份.jpeg'))))
    assert create().status_code == 302 and create().status_code == 302
    assert job(c, csrf, company='另一个公司', note_files=files((raw, '第二份.jpg'))).status_code == 302
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT count(*) n FROM files')['n'] == 1
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 2
    assert '第二份.jpg' in c.get('/jobs/2').data.decode()
    assert edit(c, csrf, hide_attachments='2').status_code == 200
    assert d.one('SELECT row_version FROM jobs WHERE id=1')['row_version'] == 1
    assert edit(c, csrf, hide_attachments='1').status_code == 302
    assert 'note-images' not in c.get('/jobs/1').data.decode()
    assert 'note-images' in c.get('/jobs/2').data.decode()
    assert edit(c, csrf, note='旧表单覆盖', note_files=files((image(), '新图.png'))).status_code == 409
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 2
    assert edit(c, csrf, version=2, restore_attachments='1').status_code == 302
    assert edit(c, csrf, version=3, note_files=files((raw, '又一份.jpg'))).status_code == 302
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 2
    assert d.one('SELECT original_name FROM job_attachments WHERE id=1')['original_name'] == '第一份.jpeg'
    assert edit(c, csrf, version=4, hide_attachments='1').status_code == 302
    assert edit(c, csrf, version=5, note_files=files((raw, '再次显示.jpg'))).status_code == 302
    assert d.one('SELECT hidden FROM job_attachments WHERE id=1')['hidden'] == 0
    d.c.close()


def test_platform_save_guards_notes_and_files_together(env):
    app, c, csrf = env
    job(c, csrf)
    values = dict(platform='BOSS直聘', reading='未知', reply='待核对', invitation='待核对',
                  stage='待准备', job_version='1', row_version='1', job_note='平台共用备注')
    assert post(c, csrf, '/platforms/1/edit', **values, note_files=files((image(), '平台截图.png'))).status_code == 302
    assert '平台截图.png' in c.get('/jobs/1').data.decode()
    assert '平台截图.png' in c.get('/platforms/1/edit').data.decode()
    result = post(c, csrf, '/platforms/1/edit', **values, communicated='1', note_files=files((image('JPEG'), '旧表单.jpg')))
    assert result.status_code == 409 and '平台共用备注' in result.data.decode()
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT communicated FROM platforms')['communicated'] == 0
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 1
    d.c.close()


def test_export_backup_restore_and_missing_original(env, tmp_path):
    app, c, csrf = env
    raw, pdf = image(), (Path(__file__).parent/'fixtures/样例简历.pdf').read_bytes()
    job(c, csrf, note_files=files((raw, '微信.png'), (pdf, 'PDF备注.pdf')))
    job(c, csrf, company='共享原件岗位', note_files=files((raw, '同图.png')))
    assert edit(c, csrf, hide_attachments='1').status_code == 302
    assert post(c, csrf, '/materials/new', title='正文', kind='背调', jobs=['1'], body='独立统计').status_code == 302
    assert post(c, csrf, '/resumes/new', name='同 PDF', jobs=['1'], file=(io.BytesIO(pdf), '实际简历.pdf')).status_code == 302
    for scope, count in [('job_id=1',2), ('q=共享原件岗位',1), ('archived=all',3)]:
        result = c.get('/export?'+scope)
        assert result.status_code == 200
        with zipfile.ZipFile(io.BytesIO(result.data)) as z:
            m = json.loads(z.read('manifest.json'))
            assert len(m['job_note_attachments']) == count
            assert len(json.loads(z.read('relations.json'))['job_attachments']) == count
            assert len([n for n in z.namelist() if n.startswith('originals/')]) == len(m['files'])
            for file in m['files']:
                assert hashlib.sha256(z.read(file['export_path'])).hexdigest() == file['hash']
            assert 'job_attachments.csv' in z.namelist()
            if scope=='job_id=1':
                assert m['unique_material_count'] == 1 and m['material_content_counts'] == {'含文本正文':1}
                assert m['job_note_attachments'][0]['hidden'] == 1
                assert '已收起' in z.read('summary.md').decode()
    store = app.extensions['store']
    folder = store.backup()
    assert store.validate(folder)['counts']['job_attachments'] == 3
    output = tmp_path/'中文完整备份.zip'
    store.zip_backup(folder, output)
    restored = Store(tmp_path/'独立中文恢复目录')
    restored.restore(restored.unpack(output))
    a, b = db.Database(store.path), db.Database(restored.path)
    for table in ['jobs','platforms','files','job_attachments','history','resumes','materials','job_materials']:
        assert a.all('SELECT * FROM '+table) == b.all('SELECT * FROM '+table)
    for f in db.referenced_files(b):
        assert (restored.current/f['path']).read_bytes() == (store.current/f['path']).read_bytes()
    assert restored.epoch != store.epoch
    f = a.one("SELECT * FROM files WHERE ext='png'")
    (store.current/f['path']).write_bytes(b'corrupt')
    assert c.get('/export?job_id=1').status_code == 409
    with pytest.raises(ValueError, match='损坏'):
        store.backup()
    a.c.close(); b.c.close()


def legacy_two(root):
    (root/'current').mkdir(parents=True)
    connection = sqlite3.connect(root/'current/db.sqlite3')
    connection.executescript(db.DDL)
    connection.execute("ALTER TABLE resumes ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE materials ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
    connection.executemany('INSERT INTO db_meta VALUES(?,?)', [('schema_version','2'), ('workspace_id','legacy')])
    connection.execute("INSERT INTO jobs(company,title,created_at,updated_at,note) VALUES('旧公司','旧岗位','旧时间','旧时间','旧备注')")
    connection.commit(); connection.close()


def test_schema_two_migration_backup_restore_and_transactional_rollback(tmp_path):
    root = tmp_path/'旧版本'
    legacy_two(root)
    store = Store(root)
    d = db.Database(store.path)
    assert d.one('SELECT note FROM jobs')['note'] == '旧备注'
    assert d.one('SELECT count(*) n FROM job_attachments')['n'] == 0
    d.c.close()
    original = next(b for b in store.list_backups() if b['purpose']=='pre-migrate')
    assert original['schema_version'] == 2 and Store.validate(original['location'])['schema_version'] == 2
    restored = Store(tmp_path/'独立旧备份恢复')
    target = restored.staging/'copy'
    shutil.copytree(original['location'], target)
    restored.restore(target)
    d = db.Database(restored.path)
    assert d.one("SELECT value FROM db_meta WHERE key='schema_version'")['value'] == str(db.SCHEMA)
    assert d.one('SELECT note FROM jobs')['note'] == '旧备注'
    d.c.close()
    broken = tmp_path/'失败升级'
    legacy_two(broken)
    with patch.object(db, 'NOTE_DDL', [db.NOTE_DDL[0], 'INVALID SQL']):
        with pytest.raises(RuntimeError, match='迁移失败'):
            Store(broken)
    d = db.Database(broken/'current/db.sqlite3')
    assert d.one("SELECT value FROM db_meta WHERE key='schema_version'")['value'] == '2'
    assert not db.has_table(d, 'job_attachments') and d.one('SELECT note FROM jobs')['note'] == '旧备注'
    d.c.close()
