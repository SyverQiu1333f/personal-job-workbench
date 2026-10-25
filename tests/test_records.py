import io
from workbench import db
from test_jobs import env, job, post


def test_platform_atomic_and_resume_trace(env):
    app, c, csrf = env
    job(c, csrf)
    job(c, csrf, **{'continue': '1'})
    pdf = b'%PDF-1.4\n final resume'
    r = post(c, csrf, '/resumes/new', name='最终简历', jobs=['1','2'], file=(io.BytesIO(pdf), '同名.pdf'))
    assert r.status_code == 302
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT count(*) n FROM job_resumes')['n'] == 2
    payload = dict(platform='平台甲', row_version='1', job_version='1', stage='面试中', reading='未知', reply='已有回复', reply_kind='明确拒绝', invitation='待核对', sent='1', sent_date='2026-10-01', resume_id='1', check_reply='1', job_note='备注')
    assert post(c, csrf, '/platforms/1/edit', **payload).status_code == 302
    assert d.one('SELECT * FROM platforms WHERE id=1')['reply_checked']
    assert d.one('SELECT * FROM platforms WHERE id=1')['invitation_checked'] is None
    assert d.one('SELECT * FROM jobs WHERE id=1')['stage'] == '面试中'
    second = dict(payload, platform='平台乙', row_version='1', job_version='2', stage='待准备', reply='待核对', sent_date='')
    assert post(c, csrf, '/jobs/1/platforms/new', **second).status_code == 302
    assert d.one('SELECT * FROM platforms WHERE id=3')['reply'] == '待核对'
    assert d.one('SELECT * FROM platforms WHERE id=1')['reply'] == '已有回复'
    # Job guard succeeds first but platform guard fails: entire action must roll back.
    stale = dict(payload, job_version='3', stage='已结束')
    assert post(c, csrf, '/platforms/1/edit', **stale).status_code == 409
    assert d.one('SELECT * FROM jobs WHERE id=1')['stage'] == '待准备'
    assert post(c, csrf, '/resumes/new', name='同名不同字节', file=(io.BytesIO(pdf+b'other'), '同名.pdf')).status_code == 302
    assert d.one('SELECT count(*) n FROM files')['n'] == 2
    assert d.one('SELECT resume_id FROM platforms WHERE id=1')['resume_id'] == 1
    assert post(c, csrf, '/resumes/1/edit', name='收起', row_version='1', hidden='1', jobs=['2']).status_code == 302
    assert c.get('/file/1').data == pdf
    for path in ['/resumes','/resumes/1','/resumes/1/edit','/platforms/1/edit','/jobs/1']:
        assert c.get(path).status_code == 200
    assert '集中补记'.encode() in c.get('/platforms/1/edit', headers={'HX-Request':'true'}).data
    d.c.close()
