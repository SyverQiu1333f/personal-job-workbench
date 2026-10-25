import io
import json
import shutil
import sqlite3
import zipfile
from unittest.mock import patch

import pytest
from workbench import db
from workbench.storage import Store
from test_jobs import env, job, post
from test_material_attachments import legacy_three


def edit(c, csrf, version, **extra):
    return post(c, csrf, '/jobs/1/edit', company='宁波测试', title='产品经理',
                stage='待准备', direct='未知', row_version=str(version), **extra)


def test_company_size_capture_display_edit_conflict_and_legacy_form(env):
    app,c,csrf=env
    assert job(c,csrf,company_size=' 100–499人 ').status_code==302
    d=db.Database(app.extensions['store'].path)
    assert d.one('SELECT company_size FROM jobs')['company_size']=='100–499人'
    assert 'value="100–499人"' in c.get('/jobs/1/edit').data.decode()
    assert '公司规模：100–499人' in c.get('/jobs/1').data.decode()
    assert '100–499人' in c.get('/jobs?view=').data.decode()
    # A pre-upgrade form without the new field must not blank the saved value.
    assert edit(c,csrf,1).status_code==302
    assert d.one('SELECT company_size FROM jobs')['company_size']=='100–499人'
    assert edit(c,csrf,2,company_size='20人以内（初创团队）').status_code==302
    assert edit(c,csrf,2,company_size='旧表单覆盖').status_code==409
    assert d.one('SELECT company_size FROM jobs')['company_size']=='20人以内（初创团队）'
    assert '公司规模' in c.get('/jobs/1').data.decode()
    assert d.one("SELECT new_value FROM history WHERE field='company_size' ORDER BY id DESC LIMIT 1")['new_value']=='"20人以内（初创团队）"'
    assert edit(c,csrf,3,company_size='').status_code==302
    assert '公司规模：待补' in c.get('/jobs/1').data.decode()
    assert job(c,csrf,**{'continue':'1'}).status_code==302
    assert d.one('SELECT company_size FROM jobs WHERE id=2')['company_size']==''
    d.c.close()


def test_company_size_exports_and_independent_restore(env,tmp_path):
    app,c,csrf=env
    job(c,csrf,company_size='500–999人')
    for scope in ['job_id=1','q=宁波测试','archived=all']:
        with zipfile.ZipFile(io.BytesIO(c.get('/export?'+scope).data)) as z:
            assert json.loads(z.read('relations.json'))['jobs'][0]['company_size']=='500–999人'
            assert 'company_size' in z.read('jobs.csv').decode('utf-8-sig')
            assert '500–999人' in z.read('summary.md').decode()
    store=app.extensions['store']
    folder=store.backup()
    zipped=tmp_path/'含公司规模的完整备份.zip'
    store.zip_backup(folder,zipped)
    restored=Store(tmp_path/'独立公司规模恢复')
    restored.restore(restored.unpack(zipped))
    a,b=db.Database(store.path),db.Database(restored.path)
    assert a.all('SELECT * FROM jobs')==b.all('SELECT * FROM jobs')
    assert a.all('SELECT * FROM history')==b.all('SELECT * FROM history')
    assert b.one('SELECT company_size FROM jobs')['company_size']=='500–999人'
    a.c.close();b.c.close()


def legacy_four(root):
    legacy_three(root)
    c=sqlite3.connect(root/'current/db.sqlite3')
    for statement in db.MATERIAL_NOTE_DDL:c.execute(statement)
    c.execute("UPDATE db_meta SET value='4' WHERE key='schema_version'")
    c.commit();c.close()


def test_company_size_migration_preserves_job_original_and_rollback(tmp_path):
    root=tmp_path/'结构四'
    legacy_four(root)
    store=Store(root)
    d=db.Database(store.path)
    assert d.one('SELECT company_size,note FROM jobs')==dict(company_size='',note='旧备注')
    assert d.one('SELECT count(*) n FROM job_attachments')['n']==1
    assert d.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    original=next(b for b in store.list_backups() if b['purpose']=='pre-migrate')
    assert original['schema_version']==4 and len(Store.validate(original['location'])['files'])==1
    restored=Store(tmp_path/'旧结构四独立恢复')
    staging=restored.staging/'old'
    shutil.copytree(original['location'],staging)
    restored.restore(staging)
    r=db.Database(restored.path)
    assert r.all('SELECT * FROM jobs')==d.all('SELECT * FROM jobs')
    assert r.all('SELECT * FROM job_attachments')==d.all('SELECT * FROM job_attachments')
    r.c.close();d.c.close()
    broken=tmp_path/'失败迁移'
    legacy_four(broken)
    with patch.object(db,'COMPANY_SIZE_DDL',[db.COMPANY_SIZE_DDL[0],'INVALID SQL']):
        with pytest.raises(RuntimeError,match='迁移失败'):Store(broken)
    d=db.Database(broken/'current/db.sqlite3')
    assert 'company_size' not in [r['name'] for r in d.all('PRAGMA table_info(jobs)')]
    assert d.one("SELECT value FROM db_meta WHERE key='schema_version'")['value']=='4'
    assert d.one('SELECT count(*) n FROM job_attachments')['n']==1
    d.c.close()
