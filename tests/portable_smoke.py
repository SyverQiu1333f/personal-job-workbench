"""Exercise the actual ZIP in two independent Windows folders, never live data."""
import argparse
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(raw):return hashlib.sha256(raw).hexdigest()


def extract(artifact,destination):
    assert not destination.exists()
    destination.mkdir(parents=True)
    with zipfile.ZipFile(artifact) as z:
        for item in z.infolist():
            assert (destination/item.filename).resolve().is_relative_to(destination.resolve())
        z.extractall(destination)
    package=next(destination.iterdir())
    manifest=json.loads((package/'package-manifest.json').read_text('utf-8'))
    actual={p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    assert actual==set(manifest['files'])|{'package-manifest.json'}
    for rel,digest in manifest['files'].items():assert sha((package/rel).read_bytes())==digest,rel
    assert not any((package/p).exists() for p in ('data','run','logs','.venv','.git','tests'))
    return package


def call(package,env,*args):
    result=subprocess.run([str(package/'runtime/python.exe'),'-X','utf8',str(package/'portable.py'),*args],
                          cwd=env['TEMP'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=55,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stderr.decode('utf-8','replace')
    return json.loads(result.stdout.decode('utf-8'))


def bat(package,env,name,*args):
    result=subprocess.run([os.environ['COMSPEC'],'/d','/c',str(package/name),*args],cwd=env['TEMP'],env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=55,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
    # chcp's output is suppressed; the helper emits one JSON object.
    return json.loads(result.stdout.decode('utf-8').strip())


class Client:
    def __init__(self,base):
        self.base=base
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self,path):return self.opener.open(self.base+path,timeout=35).read()

    def post(self,path,values=None,files=None):
        form=self.get(path).decode()
        values=dict(values or {})
        for key in ('csrf','data_epoch'):
            values[key]=re.search('name="'+key+'" value="([^"]+)"',form).group(1)
        values['request_id']=uuid.uuid4().hex
        if files:
            boundary='----portable-'+uuid.uuid4().hex
            body=bytearray()
            for key,value in values.items():
                for item in value if isinstance(value,list) else [value]:
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{item}\r\n'.encode())
            for key,name,raw,mime in files:
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
                body.extend(raw);body.extend(b'\r\n')
            body.extend(f'--{boundary}--\r\n'.encode())
            content_type='multipart/form-data; boundary='+boundary
        else:
            body=urllib.parse.urlencode(values,doseq=True).encode();content_type='application/x-www-form-urlencoded'
        request=urllib.request.Request(self.base+path,data=bytes(body),headers={'Content-Type':content_type})
        with self.opener.open(request,timeout=45) as response:return response.read()


def database_rows(path):
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='db_meta'")]
    result={table:c.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall() for table in tables}
    c.close();return result


def run(artifact,out):
    out=out.resolve()
    assert out.is_relative_to((ROOT/'test-output').resolve()) and not out.exists()
    out.mkdir(parents=True)
    system=Path(os.environ['SystemRoot'])
    env=dict(os.environ,PATH=str(system/'System32')+';'+str(system),PYTHONHOME='Z:\\no-system-python',
             PYTHONPATH='Z:\\no-system-packages',PYTHONNOUSERSITE='1')
    package=extract(artifact,out/'首次中文 空格目录')
    probe="import sys,sqlite3,json,importlib.util,importlib.metadata;print(json.dumps(dict(isolated=sys.flags.isolated,executable=sys.executable,paths=sys.path,python=sys.version,sqlite=sqlite3.sqlite_version,pip=importlib.util.find_spec('pip') is not None,pytest=importlib.util.find_spec('pytest') is not None,flask=importlib.metadata.version('Flask'),pillow=importlib.metadata.version('Pillow'))))"
    result=subprocess.run([str(package/'runtime/python.exe'),'-X','utf8','-c',probe],env=env,cwd=out,
                          capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stderr.decode('utf-8','replace')
    runtime=json.loads(result.stdout)
    assert runtime['isolated']==1 and not runtime['pip'] and not runtime['pytest']
    assert all(Path(p).resolve().is_relative_to(package.resolve()) for p in runtime['paths'])
    started=bat(package,env,'启动工作台.bat','--no-browser')
    try:
        rows=database_rows(package/'data/current/db.sqlite3')
        assert all(not value for value in rows.values()),'新包不是空白工作区'
        again=bat(package,env,'启动工作台.bat','--no-browser')
        assert all(again[key]==started[key] for key in ('pid','started_at','workspace_id','port'))
        desktop=out/'模拟桌面';desktop.mkdir()
        call(package,env,'shortcut','--desktop-dir',str(desktop))
        shortcut=desktop/'个人求职工作台.lnk'
        assert shortcut.exists()
        client=Client(started['url'])
        saved=client.post('/jobs/new',dict(company='分享包验收公司（虚构）',title='AI 产品经理 · 验收',
                          company_size='100–499人',stage='待准备',direct='未知',source='BOSS直聘',salary='8–12K/月'))
        assert '已保存' in saved.decode()
        from PIL import Image
        output=io.BytesIO();Image.new('RGB',(640,360),'#e7f0f6').save(output,'PNG');png=output.getvalue()
        pdf=(ROOT/'tests/fixtures/两页样例.pdf').read_bytes()
        client.post('/jobs/1/edit',dict(company='分享包验收公司（虚构）',title='AI 产品经理 · 验收',
                    company_size='100–499人',stage='待准备',direct='未知',row_version='1',note='解压包验收备注'),
                    [('note_files','验收截图.png',png,'image/png'),('note_files','两页备注.pdf',pdf,'application/pdf')])
        client.post('/resumes/new',dict(name='验收最终简历',jobs=['1']),[('file','验收简历.pdf',pdf,'application/pdf')])
        client.post('/materials/new',dict(title='自由复盘验收',kind='真实面试复盘',jobs=['1'],body='自由正文保持'),
                    [('note_files','复盘截图.png',png,'image/png')])
        client.post('/platforms/1/edit',dict(platform='BOSS直聘',reading='已读',reply='待核对',invitation='待核对',
                    stage='待准备',job_note='解压包验收备注',job_version='2',row_version='1',communicated='1',sent='1',resume_id='1'))
        assert 'note-images' in client.get('/jobs/1').decode()
        assert 'note-images' in client.get('/materials/1').decode()
        assert '/preview/' in client.get('/resumes/1').decode()
        for asset in ('/static/pdfjs/build/pdf.min.mjs','/static/pdfjs/build/pdf.worker.min.mjs','/static/pdfjs/standard_fonts/LiberationSans-Regular.ttf'):
            assert client.get(asset)
        with zipfile.ZipFile(io.BytesIO(client.get('/export?job_id=1'))) as z:
            manifest=json.loads(z.read('manifest.json'))
            assert manifest['app_version']=='1.1.3' and len(manifest['files'])==2
            assert json.loads(z.read('relations.json'))['jobs'][0]['company_size']=='100–499人'
            for f in manifest['files']:assert sha(z.read(f['export_path']))==f['hash']
        client.post('/backup',dict(operation='manual'))
        manual=[p.parent for p in (package/'data/backups').glob('*/manifest.json')
                if json.loads(p.read_text('utf-8'))['purpose']=='manual']
        assert len(manual)==1
        backup=out/'真实完整备份.zip';backup.write_bytes(client.get('/backup/'+manual[0].name+'/download'))
        with zipfile.ZipFile(backup) as z:
            copied=out/'备份快照.sqlite3';copied.write_bytes(z.read('db.sqlite3'))
        expected=database_rows(copied)
        before=database_rows(package/'data/current/db.sqlite3')
    finally:bat(package,env,'关闭工作台.bat')
    restarted=bat(package,env,'启动工作台.bat','--no-browser')
    assert restarted['workspace_id']==started['workspace_id'] and restarted['pid']!=started['pid']
    assert database_rows(package/'data/current/db.sqlite3')==before
    bat(package,env,'关闭工作台.bat')
    moved=out/'移动后的中文 空格目录'
    assert package.resolve().is_relative_to(out) and moved.resolve().is_relative_to(out)
    deadline=time.monotonic()+5
    while True:
        try:package.rename(moved);break
        except PermissionError:
            if time.monotonic()>deadline:raise
            time.sleep(.1)
    moved_status=bat(moved,env,'启动工作台.bat','--no-browser')
    assert moved_status['workspace_id']==started['workspace_id']
    assert database_rows(moved/'data/current/db.sqlite3')==before
    bat(moved,env,'关闭工作台.bat')
    target=extract(artifact,out/'第二个全新解压目录')
    second=bat(target,env,'启动工作台.bat','--no-browser')
    try:
        assert second['workspace_id']!=started['workspace_id']
        restore_client=Client(second['url'])
        preview=restore_client.post('/backup',dict(operation='preview'),[('backup','完整备份.zip',backup.read_bytes(),'application/zip')])
        assert '校验通过' in preview.decode()
        token=re.search(rb'name="token" value="([a-f0-9]+)"',preview).group(1).decode()
        restore_client.post('/backup',dict(operation='restore',token=token,confirmed='1'))
        actual=database_rows(target/'data/current/db.sqlite3')
        assert actual==expected,'完整备份恢复后的业务表不一致'
        with zipfile.ZipFile(backup) as z:
            m=json.loads(z.read('manifest.json'))
            for f in m['files']:assert sha((target/'data/current'/f['path']).read_bytes())==f['hash']
        assert '自由正文保持' in restore_client.get('/materials/1').decode()
    finally:bat(target,env,'关闭工作台.bat')
    report=dict(zip=str(artifact.resolve()),zip_sha256=sha(artifact.read_bytes()),runtime=runtime,
                initial_status=started,second_workspace=second['workspace_id'],tables_identical=len(expected),
                originals_identical=len(m['files']),portable_start_stop_restart_move='passed',
                shortcut=str(shortcut),restored_package=str(target),backup=str(backup),result='passed')
    (out/'验收结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    opt=parser.parse_args()
    print(json.dumps(run(opt.zip,opt.output),ensure_ascii=False))
