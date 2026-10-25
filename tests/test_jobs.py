import re
import uuid
import pytest
from workbench.web import create_app
from workbench import db


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / '数据')
    app.config['TESTING'] = True
    client = app.test_client()
    client.get('/jobs?view=')
    with client.session_transaction() as s:
        csrf = s['csrf']
    return app, client, csrf


def post(client, csrf, path, **values):
    return client.post(path, data=dict(csrf=csrf, data_epoch=client.application.extensions['store'].epoch, request_id=uuid.uuid4().hex, **values))


def job(client, csrf, **values):
    return post(client, csrf, '/jobs/new', **dict(company='宁波测试', title='产品经理', stage='待准备', direct='未知', **values))


def test_jobs_filters_conflict_duplicate(env):
    app, c, csrf = env
    assert job(c, csrf, tags='  重点,重点,可直达', jd='中文\n完整正文', star='1').status_code == 302
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT count(*) n FROM tags')['n'] == 2
    assert d.one('SELECT count(*) n FROM platforms')['n'] == 1
    assert len(d.jobs({'tags': '重点,可直达', 'tag_mode': 'all'})[0]) == 1
    assert len(d.jobs({'tags': '不存在,重点', 'tag_mode': 'all'})[0]) == 0
    result = job(c, csrf)
    assert '可能重复'.encode() in result.data
    assert d.one('SELECT count(*) n FROM jobs')['n'] == 1
    assert job(c, csrf, **{'continue': '1'}).status_code == 302
    flag = dict(row_version='1', field='archived', value='1')
    assert post(c, csrf, '/jobs/1/flag', **flag).status_code == 302
    assert post(c, csrf, '/jobs/1/flag', **dict(flag, value='0')).status_code == 409
    assert d.one('SELECT archived FROM jobs WHERE id=1')['archived'] == 1
    assert c.get('/jobs/1').status_code == 200
    assert c.get('/jobs/1/edit').status_code == 200
    assert c.get('/jobs?archived=1&tags=重点').status_code == 200
    assert c.get('/jobs').status_code == 302
    d.c.close()
