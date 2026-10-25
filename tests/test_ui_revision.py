import io
import json
import re
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from workbench import db
from test_jobs import env, job, post


def test_single_salary_and_choices_keep_legacy_data(env):
    app, client, csrf = env
    assert job(client, csrf, salary_min='8000', salary_max='12000', source='原有平台', region='原有片区').status_code == 302
    database = db.Database(app.extensions['store'].path)
    database.execute("UPDATE jobs SET city='原有城市' WHERE id=1")
    database.c.commit()
    page = client.get('/jobs/1/edit').data.decode()
    assert 'name="salary" value="8000–12000 元"' in page
    for name in ['salary_min', 'salary_max', 'city']:
        assert f'name="{name}"' not in page
    assert '<select name="source">' in page and '<select name="region">' in page
    for label in ['北仑区', '鄞州区', '海曙区', '江北区', '镇海区', '奉化区', '慈溪市', '余姚市', '宁海县', '象山县', 'BOSS直聘', '智联招聘', '猎聘', '其他']:
        assert f'value="{label}"' in page
    assert 'value="原有平台" selected' in page and 'value="原有片区" selected' in page
    response = post(client, csrf, '/jobs/1/edit', company='宁波测试', title='更新岗位', stage='待准备', direct='未知', row_version='1', salary='10–15K/月 · 14薪', source='BOSS直聘', region='北仑区')
    assert response.status_code == 302
    row = database.one('SELECT * FROM jobs WHERE id=1')
    assert (row['salary'], row['salary_min'], row['salary_max'], row['city']) == ('10–15K/月 · 14薪', 8000, 12000, '原有城市')
    assert (row['source'], row['region']) == ('BOSS直聘', '北仑区')
    assert '原有城市' not in client.get('/jobs/1').data.decode()
    with zipfile.ZipFile(io.BytesIO(client.get('/export?job_id=1').data)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        exported = json.loads(archive.read('relations.json'))['jobs'][0]
        assert 'jobs.salary' in manifest['legacy_fields_note']
        assert '旧字段'.encode() in archive.read('summary.md')
        assert exported['salary'] == '10–15K/月 · 14薪' and exported['salary_min'] == 8000
    database.c.close()


def test_searches_are_collapsed_and_show_applied_scope(env):
    app, client, csrf = env
    for path in ['/jobs?view=', '/resumes?q=简历', '/materials?kind=背调', '/analysis?platform=BOSS直聘']:
        page = client.get(path).data.decode()
        assert 'data-search-open' in page and 'aria-expanded="false"' in page
        dialog = re.search(r'<dialog\b([^>]*)>(.*?)</dialog>', page, re.S)
        assert dialog and not re.search(r'\bopen(?:\s|=|$)', dialog.group(1))
        assert '<form ' in dialog.group(2) and '重置筛选' in dialog.group(2)
        if path != '/jobs?view=':
            assert '<p class="filter-summary">当前范围：' in page
        assert 'data-module-parent' in page


def test_read_only_details_and_edit_return_context(env):
    app, client, csrf = env
    job(client, csrf)
    pdf = (Path(__file__).parent / 'fixtures/样例简历.pdf').read_bytes()
    assert post(client, csrf, '/resumes/new', name='最终PDF', jobs=['1'], file=(io.BytesIO(pdf), '原件.pdf')).status_code == 302
    assert post(client, csrf, '/materials/new', title='自由复盘', kind='真实面试复盘', jobs=['1'], body='正文').status_code == 302
    for module, label in [('jobs', '编辑岗位'), ('resumes', '编辑简历'), ('materials', '编辑资料')]:
        back = f'/{module}?q=查找&page=2#{module}-1'
        detail_url = f'/{module}/1?back=' + quote(back, safe='')
        page = client.get(detail_url).data.decode()
        assert '<form ' not in page
        assert f'>{label}</a>' in page
        assert f'href="/{module}/1/edit?back=' in page
        assert 'data-module-parent' in page
        listing = client.get(f'/{module}?' + ('view=' if module == 'jobs' else '')).data.decode()
        assert 'record-card' in listing and 'record-link' in listing
        edit_url = f'/{module}/1/edit?back=' + quote(detail_url, safe='')
        if module == 'jobs':
            response = post(client, csrf, edit_url, row_version='1', company='宁波测试', title='产品经理', stage='准备中', direct='未知', back=detail_url)
        elif module == 'resumes':
            response = post(client, csrf, edit_url, row_version='1', name='更新简历名', jobs=['1'], back=detail_url)
        else:
            response = post(client, csrf, edit_url, row_version='1', title='更新复盘', kind='真实面试复盘', jobs=['1'], body='保留正文', back=detail_url)
        assert response.status_code == 302 and response.location.startswith(f'/{module}/1?back=')
    detail = client.get('/jobs/1').data.decode()
    assert '/platforms/1/edit' not in detail and '/jobs/1/flag' not in detail
    edit = client.get('/jobs/1/edit').data.decode()
    assert '/platforms/1/edit' in edit and '/jobs/1/flag' in edit


def test_failed_check_scope_selection_is_retained(env):
    app, client, csrf = env
    job(client, csrf)
    with patch.object(app.extensions['store'], 'daily', side_effect=OSError('模拟快照失败')):
        response = post(client, csrf, '/platforms/1/edit', row_version='1', job_version='1', stage='待准备', reading='未知', reply='待核对', reply_kind='', invitation='待核对', check_all='1', check_reply='1', note='未保存备注')
    page = response.data.decode()
    assert '保存失败' in page and '未保存备注' in page
    assert re.search(r'name="check_all"[^>]*checked', page)
    assert re.search(r'name="check_reply"[^>]*checked', page)
    database = db.Database(app.extensions['store'].path)
    assert database.one('SELECT checked_at FROM platforms WHERE id=1')['checked_at'] is None
    database.c.close()
