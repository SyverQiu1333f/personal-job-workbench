import io
from pathlib import Path
import pytest
from werkzeug.datastructures import FileStorage
from workbench import db
from workbench.materials import rendered_markdown
from workbench.storage import Store
from test_jobs import env, job, post


def test_shared_material_and_original_backup(env, tmp_path):
    app, c, csrf = env
    job(c, csrf)
    job(c, csrf, **{'continue': '1'})
    source = '# 中文模拟\n\n原文换行\n<script>alert(1)</script>\n[锚点](#part)\n[危险](javascript:alert(1))'
    result = post(c, csrf, '/materials/new', title='跨岗分析', kind='外部分析结果', jobs=['1', '2'], import_body='1', file=(io.BytesIO(source.encode()), '分析.md'))
    assert result.status_code == 302
    d = db.Database(app.extensions['store'].path)
    material = d.one('SELECT * FROM materials')
    assert material['body'] == source
    assert d.one('SELECT count(*) n FROM job_materials')['n'] == 2
    html = str(rendered_markdown(source))
    assert '<script' not in html and 'href="javascript:' not in html
    assert 'href="#part"' in html
    assert post(c, csrf, '/materials/new', title='自由复盘', kind='真实面试复盘', jobs=['1'], body='我想记什么就记什么').status_code == 302
    for path in ['/materials','/materials/1','/materials/1/edit','/jobs/1','/jobs/2']:
        assert c.get(path).status_code == 200
    backup = app.extensions['store'].backup()
    restored = Store(tmp_path / '新目录')
    import shutil
    staging = restored.staging / 'import'
    shutil.copytree(backup, staging)
    restored.restore(staging)
    check = db.Database(restored.path)
    assert check.one('SELECT count(*) n FROM job_materials')['n'] == 3
    f = check.one('SELECT * FROM files')
    assert (restored.current / f['path']).read_bytes() == source.encode()
    check.c.close()
    d.c.close()


def test_file_limit_no_partial_and_input_retained(env):
    app, c, csrf = env
    job(c, csrf)
    result = post(c, csrf, '/materials/new', title='保留这个标题', body='保留这个正文', kind='真实面试复盘', jobs=['1'], file=(io.BytesIO(b'%PDF-' + b'0' * 50_000_000), '过大.pdf'))
    assert '50MB'.encode() in result.data
    assert '保留这个标题'.encode() in result.data and '保留这个正文'.encode() in result.data
    d = db.Database(app.extensions['store'].path)
    assert d.one('SELECT count(*) n FROM materials')['n'] == 0
    assert not list(app.extensions['store'].staging.iterdir())
    d.c.close()


def test_image_boundary_keeps_body_and_export(env):
    app,c,csrf=env
    job(c,csrf)
    source='正文 "原样保留"\n\n![图](https://example.com/a.png)'
    assert post(c,csrf,'/materials/new',title='图片边界',kind='真实面试复盘',jobs=['1'],body=source).status_code==302
    d=db.Database(app.extensions['store'].path)
    assert d.one('SELECT body FROM materials')['body']==source
    page=c.get('/materials/1').data
    assert '不在页面显示'.encode() in page and '已上传的图片在下方附件区展示'.encode() in page
    # Application branding may use local images; imported material images may not.
    import re
    rendered = re.search(rb'<article class="card rich">(.*?)</article>', page, re.S).group(1)
    assert b'<img ' not in rendered
    assert b'src="https://example.com/a.png"' not in page
    import zipfile,json
    with zipfile.ZipFile(io.BytesIO(c.get('/export?archived=all').data)) as z:
        assert z.read('bodies/material-1.md').decode()==source
        assert source in z.read('summary.md').decode()
        assert json.loads(z.read('manifest.json'))['materials'][0]['content']=='含文本正文'
    d.c.close()
