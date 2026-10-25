import json
import pytest
from workbench import db
from test_jobs import env, job, post


def test_bad_page_never_poisons_preference_and_legacy_recovers(env):
    app,c,csrf=env
    job(c,csrf)
    c.get('/jobs?q=宁波测试&page=2')
    d=db.Database(app.extensions['store'].path)
    original=d.one("SELECT value FROM preferences WHERE key='job_filters'")['value']
    for path in ['/jobs','/resumes','/materials']:
        for page in ['abc','1.5','1e3','9'*30]:
            response=c.get(path+'?page='+page)
            assert response.status_code==400
            assert '页码'.encode() in response.data and b'Internal Server Error' not in response.data
    assert d.one("SELECT value FROM preferences WHERE key='job_filters'")['value']==original
    assert 'page=2' in c.get('/jobs').location
    feedback=c.get('/jobs?q=宁波测试&page=2&saved=已保存').data.decode()
    import re
    links=re.findall(r'href="([^"]*page=1[^"]*)"',feedback)
    assert links and all('saved=' not in link for link in links)
    d.execute("UPDATE preferences SET value='q=%E5%AE%81%E6%B3%A2&page=abc' WHERE key='job_filters'");d.c.commit()
    response=c.get('/jobs',follow_redirects=True)
    assert response.status_code==200 and '宁波测试'.encode() in response.data
    assert 'page=1' in d.one("SELECT value FROM preferences WHERE key='job_filters'")['value']
    for page in ['0','-5','9999']:
        assert c.get('/jobs?page='+page).status_code==200
    # Export all filtered jobs ignores irrelevant pagination, including old malformed links.
    assert len(d.jobs({'page':'abc'},paged=False)[0])==1
    d.c.close()


def test_number_validation_and_preserved_input(env):
    app,c,csrf=env
    d=db.Database(app.extensions['store'].path)
    for field,value in [('commute','abc'),('salary_min','1e3'),('salary_max','8,000'),('salary_min','1.5'),('commute','-1'),('salary_max','9'*30)]:
        response=job(c,csrf,**{field:value},note='不能丢失的备注')
        assert response.status_code==200
        assert b'invalid literal' not in response.data and '不能丢失的备注'.encode() in response.data
        assert d.one('SELECT count(*) n FROM jobs')['n']==0
    response=job(c,csrf,salary_min='200',salary_max='100')
    assert '下限不能大于上限'.encode() in response.data
    assert job(c,csrf,salary='1.5万，面议',salary_min='1000',commute='0').status_code==302
    row=d.one('SELECT * FROM jobs')
    assert row['salary']=='1.5万，面议' and row['salary_min']==1000 and row['salary_max'] is None and row['commute']==0
    malformed=post(c,csrf,'/jobs/1/flag',field='star',value='一万',row_version='1')
    assert malformed.status_code==409 and b'invalid literal' not in malformed.data
    assert '输入格式不正确'.encode() in malformed.data
    assert d.one('SELECT star FROM jobs WHERE id=1')['star']==0
    d.c.close()


def test_unexpected_error_has_chinese_navigation_without_trace(env,monkeypatch):
    app,c,csrf=env
    app.config['TESTING']=False
    original=db.Database.jobs
    def broken(self,*a,**kw):raise RuntimeError('PRIVATE TRACE DETAIL')
    monkeypatch.setattr(db.Database,'jobs',broken)
    response=c.get('/jobs?view=')
    assert response.status_code==500
    assert '回首页'.encode() in response.data
    assert b'PRIVATE TRACE DETAIL' not in response.data
