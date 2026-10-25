import json
from workbench import db
from workbench.presentation import history_value
from test_jobs import env,job,post


def test_history_semantics_and_database_unchanged(env):
    app,c,csrf=env
    job(c,csrf,note='他问："准备好了吗？"',commute='0',tags='重点,转行友好')
    post(c,csrf,'/jobs/1/flag',field='star',value='1',row_version='1')
    d=db.Database(app.extensions['store'].path)
    before=d.all('SELECT * FROM history')
    response=c.get('/jobs/1')
    assert response.status_code==200
    assert '重点、转行友好'.encode() in response.data
    assert '准备好了吗'.encode() in response.data
    assert before==d.all('SELECT * FROM history')
    d.c.close()
    cases=[('0','commute','0'),('0','star','否'),('1','sent','是'),('null','note','未填写'),
           ('"未知"','reading','未知'),('"待核对"','reply','待核对'),
           (json.dumps('他问："准备好了吗？"',ensure_ascii=False),'note','他问："准备好了吗？"'),
           ('"2026-10-18T18:33:05Z"','reply_checked','2026-10-19 02:33:05'),
           ('"2026-10-18T18:33:05Z"','checked_at','2026-10-19 02:33:05'),
           ('"2026-10-18"','sent_date','2026-10-18'),('{legacy','note','{legacy'),
           ('[1,2]','jobs','岗位 #1、岗位 #2')]
    for raw,field,expected in cases:
        assert history_value(raw,field)==expected
