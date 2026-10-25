import io
import json
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage
from workbench import db
from workbench.storage import Store, digest


def test_complete_backup_restore_independent(tmp_path):
    source = Store(tmp_path / '中文原目录')
    with source.coordinated():
        info, _ = source.put(FileStorage(stream=io.BytesIO(b'%PDF-1.4\n test original'), filename='最终简历.pdf'), True)
        c = db.Database(source.path)
        def action():
            fid = c.insert('files', info)
            jid = c.insert('jobs', dict(company='宁波公司', title='测试岗位', created_at=db.now(), updated_at=db.now()))
            rid = c.insert('resumes', dict(name='最终版', file_id=fid, created_at=db.now(), updated_at=db.now()))
            c.insert('job_resumes', dict(job_id=jid, resume_id=rid))
            return '/jobs/1'
        assert c.save(source, 'request1', 'hash1', action) == '/jobs/1'
        assert c.save(source, 'request1', 'hash1', action) == '/jobs/1'
        with pytest.raises(db.Conflict):
            c.save(source, 'request1', 'other', action)
        c.c.close()
    backup = source.backup()
    zipped = tmp_path / '完整备份.zip'
    source.zip_backup(backup, zipped)
    independent = Store(tmp_path / '空目录恢复')
    staging = independent.unpack(zipped)
    independent.restore(staging)
    restored = db.Database(independent.path)
    assert restored.one('SELECT count(*) n FROM jobs')['n'] == 1
    assert restored.one('SELECT count(*) n FROM job_resumes')['n'] == 1
    restored.c.close()
    assert digest(independent.current / info['path']) == info['hash']
    assert len([b for b in source.list_backups() if b['purpose'] == 'daily']) == 1
    # Source can disappear: the restored copy is independent.
    import shutil
    shutil.rmtree(source.root)
    assert (independent.current / info['path']).read_bytes().startswith(b'%PDF-')


def test_corruption_and_schema_block(tmp_path):
    store = Store(tmp_path / 'test')
    backup = store.backup()
    (backup / 'db.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='数据库校验失败'):
        store.restore(backup)
    c = db.Database(store.path)
    assert c.one('SELECT count(*) n FROM jobs')['n'] == 0
    c.execute("UPDATE db_meta SET value='999' WHERE key='schema_version'")
    c.c.commit()
    c.c.close()
    with pytest.raises(RuntimeError, match='不兼容'):
        Store(store.root)


def test_atomic_version_and_daily_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / 'test')
    c = db.Database(store.path)
    def fail(*args):
        raise OSError('模拟磁盘故障')
    monkeypatch.setattr(store, 'backup', fail)
    with pytest.raises(OSError):
        c.save(store, 'first', 'hash', lambda: c.insert('jobs', dict(company='a', title='b', created_at=db.now(), updated_at=db.now())))
    assert c.one('SELECT count(*) n FROM jobs')['n'] == 0
    c.c.close()
