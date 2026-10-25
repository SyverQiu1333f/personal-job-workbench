import html
import io
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from workbench import db
from workbench.storage import LOCAL
from datetime import datetime
from test_jobs import env, post


def reset_link(page):
    return html.unescape(re.search(r'<a class="button" href="([^"]*)">重置筛选</a>', page).group(1))


def seed_jobs(app):
    database = db.Database(app.extensions['store'].path)
    for archived in [0, 1]:
        ident = database.insert('jobs', dict(company='收口测试', title='2026届产品经理', star=1, stage='面试中', archived=archived, created_at=db.now(), updated_at=db.now()))
        database.set_tags(ident, '优先')
        database.insert('platforms', dict(job_id=ident, platform='测试平台', sent=1, sent_date=datetime.now(LOCAL).date().isoformat(), reply='已有回复', invitation='已有明确邀约', created_at=db.now(), updated_at=db.now()))
    database.c.commit()
    database.c.close()


def test_reset_keeps_each_view_and_clears_all_added_conditions(env):
    app, client, csrf = env
    seed_jobs(app)
    for view in ['', 'star', 'recent', 'reply', 'invitation', 'interview', 'archived']:
        baseline = client.get('/jobs?' + urlencode({'view': view})).data.decode()
        expected = re.findall(r'<article id="job-(\d+)"', baseline)
        assert len(expected) == 1
        old = dict(view=view, q='无匹配', tags='无标签', stage='已结束', tag_mode='all', page='2', platform='不存在', region='无地区', fact='sent', archived='all', reply='待核对', sort='oldest')
        page = client.get('/jobs?' + urlencode(old)).data.decode()
        link = reset_link(page)
        assert urlsplit(link).path == '/jobs'
        assert parse_qs(urlsplit(link).query, keep_blank_values=True) == {'view': [view]}
        result = client.get(link).data.decode()
        assert re.findall(r'<article id="job-(\d+)"', result) == expected
        assert '当前第 1 页' in result and 'name="tag_mode" value="any"' in result
        assert 'class="active" href="/jobs?view=' + view + '"' in result
        database = db.Database(app.extensions['store'].path)
        saved = database.one("SELECT value FROM preferences WHERE key='job_filters'")['value']
        assert parse_qs(saved, keep_blank_values=True) == {'view': [view]}
        assert database.one('SELECT COUNT(*) n FROM jobs')['n'] == 2
        assert database.one('SELECT COUNT(*) n FROM platforms')['n'] == 2
        database.c.close()
    result = client.get('/jobs').location
    assert parse_qs(urlsplit(result).query)['view'] == ['archived']


def test_filter_reset_urls_and_task_actions(env):
    app, client, csrf = env
    for path, reset in [
        ('/resumes?q=2026&tag=优先&hidden=all&page=2', '/resumes'),
        ('/materials?q=2026&kind=背调&page=2', '/materials'),
        ('/analysis?platform=测试&tags=优先&tag_mode=all&archived=all&start=2026-01-01&date_scope=unknown', '/analysis'),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        page = response.data.decode()
        assert reset_link(page) == reset
        assert '<button>筛选</button>' in page
        assert re.search(r'<a class="button" href="/jobs/new[^\"]*">＋ 收录岗位</a>', page)
        assert client.get(reset).status_code == 200
    for path in ['/', '/jobs?view=']:
        page = client.get(path).data.decode()
        assert re.search(r'<a class="button primary" href="/jobs/new[^\"]*">＋ 收录岗位</a>', page)


def test_breadcrumbs_cover_new_edit_detail_and_failed_forms(env):
    app, client, csrf = env
    seed_jobs(app)
    data = (Path(__file__).parent / 'fixtures' / '样例简历.pdf').read_bytes()
    assert post(client, csrf, '/resumes/new', name='2026最终版.pdf', jobs=['1'], file=(io.BytesIO(data), '2026最终版.pdf')).status_code == 302
    assert post(client, csrf, '/materials/new', title='第2轮复盘', kind='真实面试复盘', jobs=['1'], body='自由正文').status_code == 302
    cases = {
        '/tags': ('标签管理', '/jobs'),
        '/jobs/new': ('快速收录', '/jobs'),
        '/jobs/1/edit': ('编辑岗位', '/jobs'),
        '/jobs/1': ('2026届产品经理', '/jobs'),
        '/platforms/1/edit': ('集中补记', '/jobs'),
        '/jobs/1/platforms/new': ('集中补记', '/jobs'),
        '/resumes/new': ('导入最终 PDF', '/resumes'),
        '/resumes/1/edit': ('编辑简历信息', '/resumes'),
        '/resumes/1': ('2026最终版.pdf', '/resumes'),
        '/materials/new': ('保存资料', '/materials'),
        '/materials/1/edit': ('编辑资料', '/materials'),
        '/materials/1': ('第2轮复盘', '/materials'),
    }
    for path, (leaf, module) in cases.items():
        response = client.get(path)
        assert response.status_code == 200
        page = response.data.decode()
        crumb = re.search(r'<div class="breadcrumb".*?</div>', page, re.S).group()
        assert '<strong aria-current="page">' + leaf + '</strong>' in crumb
        assert 'href="' + module + '"' in crumb
        if '/new' in path or path.endswith('/edit'):
            assert re.search(r'<a class="button" href="/jobs/new[^\"]*">＋ 收录岗位</a>', page)
    failed = post(client, csrf, '/jobs/1/edit', company='保留输入', title='2027待修改', stage='面试中', direct='未知', row_version='1', commute='不是数字')
    assert failed.status_code == 200
    assert '<strong aria-current="page">编辑岗位</strong>'.encode() in failed.data
    assert 'value="保留输入"'.encode() in failed.data
    assert '保存失败'.encode() in failed.data
    database = db.Database(app.extensions['store'].path)
    assert database.one('SELECT title FROM jobs WHERE id=1')['title'] == '2026届产品经理'
    database.c.close()
