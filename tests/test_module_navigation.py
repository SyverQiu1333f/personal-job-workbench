import io
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from workbench.navigation import list_context, module_parent
from test_jobs import env, job, post


def parent_link(response):
    class Parent(HTMLParser):
        link = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a' and 'data-module-parent' in attrs and 'hidden' not in attrs:
                self.link = attrs

    parser = Parent()
    parser.feed(response.data.decode())
    return parser.link


def test_first_level_has_no_parent_even_with_foreign_back(env):
    app, client, csrf = env
    for path in ['/', '/jobs?view=&back=/materials', '/resumes?back=/jobs', '/materials?back=/resumes', '/analysis', '/backup']:
        response = client.get(path)
        assert response.status_code == 200
        assert parent_link(response) is None


def test_edit_detail_and_cross_module_hierarchy(env):
    app, client, csrf = env
    job(client, csrf)
    pdf = (Path(__file__).parent / 'fixtures/样例简历.pdf').read_bytes()
    assert post(client, csrf, '/resumes/new', name='最终版', jobs=['1'], file=(io.BytesIO(pdf), '样例.pdf')).status_code == 302
    assert post(client, csrf, '/materials/new', title='复盘', kind='真实面试复盘', jobs=['1'], body='正文').status_code == 302
    for module, prefix, label in [('jobs', 'job', '岗位'), ('resumes', 'resume', '简历'), ('materials', 'material', '资料')]:
        filters = f'/{module}?q=保留&page=2#{prefix}-1'
        detail = f'/{module}/1?back=' + quote(filters, safe='')
        # Even if edit was opened directly from a list, its parent is this record.
        for back in [filters, detail, f'/{module}/99?back=' + quote(filters, safe='')]:
            page = client.get(f'/{module}/1/edit?back=' + quote(back, safe=''), headers={'Referer': 'http://localhost/analysis'})
            link = parent_link(page)
            parts = urlsplit(link['href'])
            assert parts.path == f'/{module}/1'
            kept_list = parse_qs(parts.query)['back'][0]
            assert parse_qs(urlsplit(kept_list).query) == {'q': ['保留'], 'page': ['2']}
            assert urlsplit(kept_list).fragment == prefix + '-1'
            assert link['aria-label'] == '返回' + label + '详情'
        link = parent_link(client.get(detail))
        assert urlsplit(link['href']).path == '/' + module
        assert parse_qs(urlsplit(link['href']).query) == {'q': ['保留'], 'page': ['2']}
        # The previous screen may be another module; it cannot become the parent.
        for suffix in ['/1', '/new']:
            link = parent_link(client.get('/' + module + suffix + '?back=/analysis'))
            assert urlsplit(link['href']).path == '/' + module
    assert urlsplit(parent_link(client.get('/platforms/1/edit?back=/analysis'))['href']).path == '/jobs/1'
    assert urlsplit(parent_link(client.get('/jobs/1/platforms/new?back=/resumes'))['href']).path == '/jobs/1'
    panel = client.get('/platforms/1/edit?back=' + quote('/jobs?q=保留&page=2#job-1', safe=''), headers={'HX-Request': 'true'})
    assert 'data-module-parent-url="/jobs?q=%E4%BF%9D%E7%95%99&amp;page=2#job-1"'.encode() in panel.data


def test_parent_links_do_not_follow_unsafe_or_unbounded_returns():
    for back in ['https://external.example/', '//external.example/jobs', '/\\external.example', '/materials', None]:
        assert list_context('/jobs', back) == '/jobs'
    nested = '/jobs?q=保留&page=2#job-1'
    for _ in range(13):
        nested = '/jobs/1?back=' + quote(nested, safe='')
    assert list_context('/jobs', nested) == '/jobs'
    assert module_parent('index') == (None, '')
    assert module_parent('backup', restore_preview=True) == ('/backup', '返回备份列表')
