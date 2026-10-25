import logging
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from launcher import DailyLog
from workbench import db
from workbench.storage import Store


class Clock(datetime):
    moment=datetime(2026,10,19,15,59,59,tzinfo=timezone.utc)

    @classmethod
    def now(cls,tz=None):
        return cls.moment.astimezone(tz) if tz else cls.moment.replace(tzinfo=None)


def test_running_store_crosses_midnight_and_restart_does_not_duplicate(tmp_path):
    root=tmp_path/'跨日';store=Store(root);c=db.Database(store.path)
    with patch('workbench.storage.datetime',Clock):
        def create():return c.insert('jobs',dict(company='跨日样例',title='岗位',created_at=db.now(),updated_at=db.now()))
        c.save(store,'day1-create','1',create)
        c.save(store,'day1-edit','2',lambda:c.update('jobs',1,1,{'note':'当天修改'}))
        assert len([x for x in store.list_backups() if x['purpose']=='daily'])==1
        Clock.moment+=timedelta(seconds=2)
        c.save(store,'day2-edit','3',lambda:c.update('jobs',1,2,{'note':'跨日修改'}))
        daily=[x for x in store.list_backups() if x['purpose']=='daily']
        assert {x['local_day'] for x in daily}=={'2026-10-19','2026-10-20'}
        assert {x['counts']['jobs'] for x in daily}=={0,1}
        c.c.close()
        restarted=Store(root);c=db.Database(restarted.path)
        c.save(restarted,'day2-restart-edit','4',lambda:c.update('jobs',1,3,{'note':'重启同日'}))
        assert len([x for x in restarted.list_backups() if x['purpose']=='daily'])==2
        c.c.close()
    Clock.moment=datetime(2026,10,19,15,59,59,tzinfo=timezone.utc)


def test_log_crosses_local_midnight_and_retains_14_days(tmp_path):
    logs=tmp_path/'日志';logs.mkdir()
    (logs/'app-20261005.log').write_text('old','utf-8')
    (logs/'app-20261007.log').write_text('keep','utf-8')
    handler=DailyLog(logs)
    with patch('launcher.datetime',Clock):
        handler.emit(logging.LogRecord('test',logging.INFO,'test',1,'first',(),None))
        Clock.moment+=timedelta(seconds=2)
        handler.emit(logging.LogRecord('test',logging.INFO,'test',2,'second',(),None))
    assert 'first' in (logs/'app-20261019.log').read_text('utf-8')
    assert 'second' in (logs/'app-20261020.log').read_text('utf-8')
    assert not (logs/'app-20261005.log').exists()
    assert (logs/'app-20261007.log').exists()
    Clock.moment=datetime(2026,10,19,15,59,59,tzinfo=timezone.utc)
