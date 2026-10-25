"""Build a blank Windows x64 portable ZIP from explicit code and runtime inputs."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.db import VERSION, SCHEMA

PYTHON_VERSION='3.12.10'
PYTHON_ZIP='python-3.12.10-embed-amd64.zip'
PYTHON_URL='https://www.python.org/ftp/python/3.12.10/'+PYTHON_ZIP
# Official python.org artifact, also checked against the published Sigstore digest.
PYTHON_SHA256='4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
SOURCE_FILES=('launcher.py','portable.py','启动工作台.bat','关闭工作台.bat','创建桌面快捷方式.bat',
              'requirements-runtime-lock.txt','requirements-lock.txt','requirements.txt','README.md','docs/AI_UPDATE.md',
              'docs/THIRD_PARTY_NOTICES.md','docs/PORTABLE.md','docs/RELEASE-v1.1.3.md','docs/images/job-detail.png')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def runtime_archive(cache):
    cache.mkdir(parents=True,exist_ok=True)
    archive=cache/PYTHON_ZIP
    if not archive.exists():
        with urllib.request.urlopen(PYTHON_URL,timeout=40) as response:
            raw=response.read()
        if hashlib.sha256(raw).hexdigest()!=PYTHON_SHA256:
            raise ValueError('官方 Python 包校验失败，未使用下载内容')
        temporary=cache/(PYTHON_ZIP+'.tmp')
        temporary.write_bytes(raw)
        os.replace(temporary,archive)
    if digest(archive)!=PYTHON_SHA256:
        raise ValueError('Python 缓存哈希不符，保留现场，停止打包')
    signature=cache/(PYTHON_ZIP+'.sigstore')
    if not signature.exists():
        with urllib.request.urlopen(PYTHON_URL+'.sigstore',timeout=30) as response:
            signature.write_bytes(response.read())
    metadata=json.loads(signature.read_text('utf-8'))
    expected=metadata['messageSignature']['messageDigest']
    assert expected['algorithm']=='SHA2_256'
    assert base64.b64decode(expected['digest']).hex()==PYTHON_SHA256
    return archive


def prune_entrypoints(site):
    """pip's unused Windows CLI wrappers embed the build interpreter path."""
    for name in ('bin','Scripts'):
        directory=site/name
        if not directory.exists():continue
        if directory.is_symlink() or not directory.resolve().is_relative_to(site.resolve()):
            raise ValueError('运行依赖启动器路径异常')
        files=list(directory.iterdir())
        if any(p.is_symlink() or not p.is_file() or p.name not in {'flask.exe','markdown_py.exe','waitress-serve.exe'} for p in files):
            raise ValueError('出现未审查的依赖启动器，保留现场并停止打包')
        for path in files:path.unlink()
        directory.rmdir()


def build(output,cache):
    if os.name!='nt' or sys.version_info[:2]!=(3,12):
        raise RuntimeError('请使用 Windows x64 的 Python 3.12 构建')
    import struct
    if struct.calcsize('P')!=8:raise RuntimeError('需要 64 位 Python')
    output=output.resolve();cache=cache.resolve()
    if output==ROOT or output in ROOT.parents or output.is_relative_to(ROOT/'data'):
        raise ValueError('输出目录不能是项目根目录、其上级或业务数据目录')
    output.mkdir(parents=True,exist_ok=True)
    name=f'个人求职工作台-v{VERSION}'
    final=output/(name+'-Windows-x64.zip')
    if final.exists():raise FileExistsError('同名压缩包已存在，未覆盖：'+str(final))
    stage_parent=ROOT/'build';stage_parent.mkdir(exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='portable-',dir=stage_parent))
    package=stage/name;package.mkdir()
    for relative in SOURCE_FILES:
        source=ROOT/relative
        if source.is_symlink():raise ValueError('不允许链接文件：'+relative)
        target=package/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    for source in (ROOT/'workbench').rglob('*'):
        if not source.is_file() or '__pycache__' in source.parts or source.suffix in {'.pyc','.pyo'}:continue
        if source.is_symlink():raise ValueError('运行文件不可为链接')
        target=package/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    shutil.copyfile(ROOT/'docs/PORTABLE.md',package/'先读我.md')
    runtime=package/'runtime';runtime.mkdir()
    with zipfile.ZipFile(runtime_archive(cache)) as z:
        for item in z.infolist():
            if not (runtime/item.filename).resolve().is_relative_to(runtime):raise ValueError('运行环境路径异常')
        z.extractall(runtime)
    (runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n..\nimport site\n','utf-8')
    subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-compile',
                    '--only-binary=:all:','--no-deps','--target',str(runtime/'Lib/site-packages'),
                    '-r',str(ROOT/'requirements-runtime-lock.txt')],check=True)
    prune_entrypoints(runtime/'Lib/site-packages')
    # Never initialize Store in the factory package: each recipient gets a new identity.
    assert not any((package/path).exists() for path in ('data','run','logs','.venv','.git','tests','test-output'))
    paths=sorted(p for p in package.rglob('*') if p.is_file())
    private_paths={str(ROOT),str(package),str(sys.executable),os.environ.get('USERPROFILE','')}-{''}
    for path in paths:
        raw=path.read_bytes()
        if any(value.encode('utf-8') in raw or value.encode('utf-16le') in raw for value in private_paths):
            raise ValueError('发行文件包含构建机路径，停止发布：'+path.relative_to(package).as_posix())
    manifest=dict(format_version=1,app_version=VERSION,schema_version=SCHEMA,platform='Windows x64',
                  python_version=PYTHON_VERSION,python_url=PYTHON_URL,python_zip_sha256=PYTHON_SHA256,
                  factory_empty_workspace=True,files={p.relative_to(package).as_posix():digest(p) for p in paths})
    (package/'package-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    temporary=output/(final.name+'.partial')
    with temporary.open('xb') as stream:
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for path in sorted(package.rglob('*')):
                if path.is_file():z.write(path,path.relative_to(stage).as_posix())
    os.replace(temporary,final)
    sha=digest(final)
    checksum=final.with_suffix(final.suffix+'.sha256')
    checksum.write_text(sha+'  '+final.name+'\n','utf-8')
    result=dict(zip=str(final),sha256=sha,bytes=final.stat().st_size,files=len(manifest['files'])+1,
                stage=str(package),python_version=PYTHON_VERSION,app_version=VERSION,schema_version=SCHEMA)
    (output/'portable-build.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--cache',type=Path,default=ROOT/'build/cache')
    args=parser.parse_args()
    print(json.dumps(build(args.output,args.cache),ensure_ascii=False))
