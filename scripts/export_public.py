"""Export an explicit public source allowlist; never copy business data/history."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('workbench', 'tests')
FILES = ('README.md', '.gitignore', '.gitattributes', 'launcher.py', 'portable.py', '启动工作台.bat',
         '关闭工作台.bat', '创建桌面快捷方式.bat', 'requirements-runtime-lock.txt', 'pytest.ini',
         'requirements.txt', 'requirements-dev.txt', 'requirements-lock.txt',
         'docs/AI_UPDATE.md', 'docs/THIRD_PARTY_NOTICES.md',
         'docs/images/job-detail.png', 'docs/PORTABLE.md', 'docs/RELEASE-v1.1.3.md', 'scripts/build_portable.py', 'scripts/export_public.py')


def export(destination):
    destination = Path(destination).resolve()
    if destination == ROOT or destination in ROOT.parents or destination.is_relative_to(ROOT / 'data'):
        raise ValueError('目标不能是源项目、其上级或业务数据目录')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('目标目录必须为空，禁止覆盖既有文件或混入旧内容')
    paths = [ROOT / path for path in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and not {'__pycache__', '.pytest_cache'} & set(p.relative_to(ROOT).parts)
                     and p.suffix not in {'.pyc', '.pyo'})
    if any(p.is_symlink() or not p.resolve().is_relative_to(ROOT) for p in paths):
        raise ValueError('不允许通过链接导出源目录之外的文件')
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for source in sorted(set(paths)):
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', required=True, type=Path)
    args = parser.parse_args()
    manifest = export(args.dest)
    print(json.dumps(dict(destination=str(args.dest.resolve()), files=len(manifest)), ensure_ascii=False))
