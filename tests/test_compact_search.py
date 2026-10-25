from urllib.parse import urlencode
from workbench import db
from test_jobs import env


def test_three_field_search_clears_legacy_filters_and_respects_view_scope(env):
    app,c,csrf=env
    d=db.Database(app.extensions['store'].path)
    for stage,archived in [('待准备',0),('面试中',0),('待准备',1)]:
        jid=d.insert('jobs',dict(company='三栏搜索',title=stage,stage=stage,archived=archived,created_at=db.now(),updated_at=db.now()))
        d.set_tags(jid,'产品')
        d.insert('platforms',dict(job_id=jid,platform='测试平台',created_at=db.now(),updated_at=db.now()))
    d.c.commit()
    old=c.get('/jobs?platform=测试平台&reply=待核对').data.decode()
    assert '当前范围：平台：测试平台' in old
    response=c.get('/jobs?'+urlencode(dict(apply_filters='1',view='interview',stage='待准备',q='三栏搜索',tags='产品',platform='不存在',reply='已有回复',page='2')))
    assert response.status_code==302
    assert 'apply_filters' not in response.location and 'platform=' not in response.location and 'page=' not in response.location
    page=c.get(response.location).data.decode()
    assert '岗位管理 <span>1</span>' in page and 'job-1' in page and 'job-2' not in page
    saved=d.one("SELECT value FROM preferences WHERE key='job_filters'")['value']
    assert 'apply_filters' not in saved and 'platform=' not in saved
    archived=c.get('/jobs?'+urlencode(dict(apply_filters='1',view='archived',stage='待准备',q='三栏搜索',tags='产品')),follow_redirects=True).data.decode()
    assert 'job-3' in archived and 'job-1' not in archived
    interview=c.get('/jobs?view=interview').data.decode()
    assert 'value="面试中" selected' in interview
    d.c.close()
