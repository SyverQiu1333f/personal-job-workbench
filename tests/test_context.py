import io
import re
from workbench import db
from workbench.reporting import export_analysis
from test_jobs import env, job, post


def test_stable_page_identity_filter_exit_and_recovery_epoch(env,tmp_path):
    app,c,csrf=env
    d=db.Database(app.extensions['store'].path)
    for n in range(55):
        jid=d.insert('jobs',dict(company='分页测试',title=str(n),created_at=db.now(),updated_at=db.now()))
        d.insert('platforms',dict(job_id=jid,created_at=db.now(),updated_at=db.now()))
    d.c.commit()
    response=c.get('/jobs?q=分页测试&page=2&reply=待核对')
    assert response.status_code==200
    assert '当前第 2 页'.encode() in response.data
    payload=dict(row_version='1',job_version='1',stage='待准备',reading='未知',reply='已有回复',reply_kind='',invitation='待核对',back='/jobs?q=分页测试&page=2&reply=待核对#job-1')
    response=post(c,csrf,'/platforms/1/edit',**payload)
    assert response.status_code==302
    from urllib.parse import unquote
    assert 'page=2' in response.location and '现已移出当前筛选结果' in unquote(response.location)
    assert d.one('SELECT reply FROM platforms WHERE id=1')['reply']=='已有回复'
    assert d.one('SELECT reply FROM platforms WHERE id=2')['reply']=='待核对'
    store=app.extensions['store']
    old_epoch=store.epoch
    backup=store.backup()
    import shutil
    folder=store.staging/'独立恢复'
    shutil.copytree(backup,folder)
    d.c.close()
    store.restore(folder)
    response=c.post('/jobs/1/flag',data=dict(csrf=csrf,data_epoch=old_epoch,request_id='old-form',row_version='2',field='star',value='1'))
    assert response.status_code==409 and '资料已恢复或切换'.encode() in response.data


def test_offline_routes_assets_and_duplicate_create(env,monkeypatch):
    app,c,csrf=env
    # Reject every outbound socket; the local WSGI flow and all assets remain functional.
    import socket
    def no_network(*a,**kw):raise AssertionError('离线验证禁止外部网络调用')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    data=dict(csrf=csrf,data_epoch=app.extensions['store'].epoch,request_id='double-click',company='离线公司',title='岗位',stage='待准备',direct='未知')
    first=c.post('/jobs/new',data=data)
    second=c.post('/jobs/new',data=data)
    assert first.status_code==second.status_code==302 and first.location==second.location
    for path in ['/','/jobs?view=','/resumes','/materials','/analysis','/backup','/export?archived=all','/static/app.css','/static/app.js','/static/htmx.min.js']:
        response=c.get(path)
        assert response.status_code==200
        if response.mimetype=='text/html':
            assert not re.findall(rb'(?:src|href)=[\"\']https?://',response.data)
