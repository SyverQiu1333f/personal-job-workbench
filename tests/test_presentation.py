import re
from workbench import db
from test_jobs import env,job,post


def test_summary_and_statistical_sets_are_preserved(env):
    app,c,csrf=env
    job(c,csrf)
    listing=c.get('/jobs?view=').data.decode()
    assert '#1 · 进展待补' in listing and '/platforms/1/edit' in listing
    home=c.get('/').data.decode()
    assert re.search(r'平台记录<b>1</b>',home)
    assert re.search(r'已触达<b>0</b>',home)
    assert post(c,csrf,'/platforms/1/edit',platform='同一平台',row_version='1',job_version='1',stage='待准备',reading='未知',reply='已有回复',reply_kind='明确拒绝',invitation='待核对').status_code==302
    d=db.Database(app.extensions['store'].path)
    original=d.one('SELECT * FROM platforms WHERE id=1')
    home=c.get('/').data.decode()
    assert re.search(r'已有回复<b>1</b>',home)
    assert re.search(r'已触达<b>0</b>',home)
    analysis=c.get('/analysis').data.decode()
    assert '0/0（无分母）' in analysis and '按已触达记录计算' in analysis
    listing=c.get('/jobs?view=').data.decode()
    assert '同一平台 · #1' in listing and '已有回复' in listing
    assert original==d.one('SELECT * FROM platforms WHERE id=1')
    d.c.close()
