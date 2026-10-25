import io
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage
from workbench import db
from workbench.storage import Store
from test_jobs import env, job, post


def test_verified_v1_migration_and_rollback(tmp_path):
    root = tmp_path / '旧结构'
    current = root / 'current'
    current.mkdir(parents=True)
    c = sqlite3.connect(current / 'db.sqlite3')
    c.executescript(db.DDL)
    c.executemany('INSERT INTO db_meta VALUES(?,?)',[('schema_version','1'),('workspace_id','old')])
    c.execute("INSERT INTO jobs(company,title,created_at,updated_at) VALUES('旧公司','旧岗位','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    c.commit();c.close()
    store=Store(root)
    c=db.Database(store.path)
    assert c.one("SELECT value FROM db_meta WHERE key='schema_version'")['value']==str(db.SCHEMA)
    assert c.one('SELECT company FROM jobs')['company']=='旧公司'
    assert len([b for b in store.list_backups() if b['purpose']=='pre-migrate'])==1
    c.c.close()
    # Force a migration error after ALTER: SQLite transactional DDL must roll back.
    bad=tmp_path/'失败迁移'
    (bad/'current').mkdir(parents=True)
    c=sqlite3.connect(bad/'current'/'db.sqlite3')
    c.executescript(db.DDL)
    c.execute("INSERT INTO db_meta VALUES('schema_version','1')")
    c.execute("INSERT INTO db_meta VALUES('workspace_id','old')")
    c.execute("ALTER TABLE materials ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
    c.commit();c.close()
    with pytest.raises(RuntimeError,match='迁移失败'):
        Store(bad)
    c=sqlite3.connect(bad/'current'/'db.sqlite3')
    assert 'original_name' not in [r[1] for r in c.execute('PRAGMA table_info(resumes)')]
    assert c.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()[0]=='1'
    c.close()


def test_large_restore_and_daily_rotation(tmp_path):
    store=Store(tmp_path/'原件')
    c=db.Database(store.path)
    # Two valid business files, each <50MB; total backup ZIP genuinely >50MB.
    for i in range(2):
        raw=b'%PDF-1.4\n'+os.urandom(25_100_000)
        info,_=store.put(FileStorage(stream=io.BytesIO(raw),filename=f'简历{i}.pdf'),True)
        fid=c.insert('files',info)
        c.insert('resumes',dict(name=f'简历{i}',file_id=fid,created_at=db.now(),updated_at=db.now(),original_name=f'简历{i}.pdf'))
    c.c.commit();c.c.close()
    folder=store.backup()
    output=tmp_path/'超过50MB.zip'
    store.zip_backup(folder,output)
    assert output.stat().st_size>50_000_000
    other=Store(tmp_path/'独立恢复')
    other.restore(other.unpack(output))
    assert sum(p.stat().st_size for p in (other.current/'files').rglob('*') if p.is_file())>50_000_000
    # Rotation with small independent snapshots; manual ones survive.
    small=Store(tmp_path/'轮换')
    manual=small.backup('manual')
    for i in range(8):
        folder=small.backup('daily')
        m=json.loads((folder/'manifest.json').read_text('utf-8'))
        m['local_day']=f'2026-09-{i+1:02}'
        (folder/'manifest.json').write_text(json.dumps(m),'utf-8')
    small.daily()
    daily=[b for b in small.list_backups() if b['purpose']=='daily']
    assert len(daily)==7 and manual.exists()


def test_restore_switch_failure_and_interruption(tmp_path,monkeypatch):
    store=Store(tmp_path/'数据')
    backup=store.backup()
    folder=store.staging/'恢复副本'
    shutil.copytree(backup,folder)
    original=os.replace
    def failure(src,dest):
        if Path(src)==folder:
            raise OSError('模拟切换失败')
        return original(src,dest)
    with monkeypatch.context() as m:
        m.setattr(os,'replace',failure)
        with pytest.raises(OSError):
            store.restore(folder)
    assert store.path.exists()
    check=db.Database(store.path)
    assert check.one('SELECT count(*) n FROM jobs')['n']==0
    check.c.close()
    old=store.root/'previous-interruption'
    os.replace(store.current,old)
    (store.root/'restore-switch.json').write_text(json.dumps({'previous':old.name}),'utf-8')
    restarted=Store(store.root)
    assert restarted.path.exists() and not (store.root/'restore-switch.json').exists()


def test_busy_feedback_and_deduplicated_upload(env):
    app,c,csrf=env
    job(c,csrf)
    import threading
    ready,release=threading.Event(),threading.Event()
    def hold_writer():
        blocker=db.Database(app.extensions['store'].path)
        blocker.execute('BEGIN IMMEDIATE');ready.set()
        release.wait(15)
        blocker.c.rollback();blocker.c.close()
    writer=threading.Thread(target=hold_writer)
    writer.start();assert ready.wait(3)
    try:
        response=post(c,csrf,'/jobs/1/flag',row_version='1',field='star',value='1')
        assert response.status_code==409 and '数据库忙'.encode() in response.data
    finally:
        release.set();writer.join(timeout=3)
    data=dict(csrf=csrf,data_epoch=app.extensions['store'].epoch,request_id='same-upload',name='最终文件')
    for i in range(2):
        response=c.post('/resumes/new',data=dict(data,file=(io.BytesIO(b'%PDF-1.4\n same'),'原件.pdf')))
        assert response.status_code==302
    check=db.Database(app.extensions['store'].path)
    assert check.one('SELECT count(*) n FROM resumes')['n']==1
    assert len(list((app.extensions['store'].current/'files').rglob('*.pdf')))==1
    check.c.close()


def test_zip_traversal_rejected(tmp_path):
    store=Store(tmp_path/'安全')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('../escape.txt','bad')
    stream.seek(0)
    with pytest.raises(ValueError,match='不安全'):
        store.unpack(stream)
    assert not (tmp_path/'escape.txt').exists()
